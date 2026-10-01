"""Execute a pinned ReplayRun through the shared completed-bar pipeline.

The merged SCRUM-61 lifecycle (``replay_run``) pins identity, configuration,
and cursor rows but never invokes analytical components. This module completes
that story's execution-level acceptance: :class:`ReplayPipeline` wires the
persisted run to the SCRUM-63 :class:`ReplayCursor` and the SCRUM-80
:class:`DetectorRuntime` (which owns the SCRUM-77 market-state aggregator) so
every step processes exactly one completed canonical bar with current/past-only
context. Warm-up bars before the selected interval are ordinary steps whose
views are not visible; the visible interval starts at ``selected_start``.

Construction re-verifies every pinned lineage element through
``load_replay_context`` (snapshot hash, dataset revision and lineage bar
count, calendar version against the dataset revision), recomputes the
detection config hash against the stored snapshot pin, binds canonical bar
content to the persisted lineage through ``load_bar_sequence``, resolves the
pinned calendar version through the injected SCRUM-71 calendar path, and builds
the clock, cursor, aggregator and runtime from that verified state only.

Per-bar wiring, one deterministic causal chain per step:

1. ``load_replay_context`` re-verifies the pinned context and
   :func:`_validate_replay_clock` re-checks clock/DB-cursor parity.
2. ``ReplayCursor.step_one()`` advances the :class:`SimulationClock` by exactly
   one completed bar and hands the immutable :class:`ObservableBars` view to
   the adapter exactly once, in canonical order.
3. The adapter drives ``DetectorRuntime.process_bar(view.current_bar)``; the
   runtime finalizes the canonical market-state frame and commits validated
   detector events in memory (SCRUM-81 owns persistence).
4. Only after the analytical step succeeded does the pipeline persist the
   cursor advance through ``advance_replay_cursor`` inside the same
   ``begin_nested`` savepoint, mirroring ``step_replay``'s guard discipline.

Failure contract: a failed step leaves the clock and analytical state consumed
at bar k, the persisted cursor at k-1, and the lifecycle FAILED. Analytical
state is not rolled back; the pipeline latches and cannot be reused. The
caller owns the outer transaction and must commit after catching the error
to retain the failure record. The pipeline surfaces a structured SCRUM-112 error
record carrying run, instrument and component context. Every successful step
logs an execution record with the same context plus bar identity.

Recovery is a fresh run: a persisted ReplayRun has no cursor-reset path
(``replay_runs`` only advances) and SCRUM-61's reset semantics are "a new run
id over the same pinned inputs, never rewriting an old snapshot".
:meth:`ReplayPipeline.reset` documents and refuses the in-place alternative,
so replay/repeat parity is proven by replaying a second fresh run over the
same dataset revision and configuration.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection

from market_analysis.application.logging import ResearchLogger, research_logger
from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.detection import (
    DetectorBinding,
    DetectorEvent,
    DetectorRuntime,
    DetectorRuntimeError,
    DetectorRuntimeResult,
)
from market_analysis.domain import (
    Bar,
    BarSequence,
    ObservableBars,
    ReplayCursor,
    SimulationClock,
    TradingCalendar,
)
from market_analysis.patterns import ParameterSpec, PatternDefinition
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.market_data import load_bar_sequence
from market_analysis.persistence.replay_runs import (
    ReplayRunRecord,
    ReplayStatus,
    advance_replay_cursor,
    transition_replay_run,
)
from market_analysis.persistence.runs import RunSnapshotRecord

from .replay_run import (
    ReplayRunContext,
    _validate_replay_clock,
    complete_replay,
    load_replay_context,
)

COMPONENT = "replay-pipeline"
ParameterSpecs = Mapping[tuple[str, str], tuple[ParameterSpec, ...]]
PatternDefinitions = Mapping[tuple[str, str], PatternDefinition]
CalendarResolver = Callable[[str, str], TradingCalendar]
BindingsFactory = Callable[[], Sequence[DetectorBinding]]
"""Resolve (calendar_id, pinned calendar_version) through the SCRUM-71 path."""

_RUNTIME_FAILURE = re.compile(
    r"pattern=(?P<pattern_id>\S+)@(?P<pattern_version>\S+) instance=(?P<instance>'[^']*')"
)


class ReplayPipelineError(ValueError):
    """A pipeline wiring, stepping, or recovery invariant was violated."""


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """One committed pipeline step: the consumed view and its detector outcome."""

    view: ObservableBars
    result: DetectorRuntimeResult

    @property
    def index(self) -> int:
        return self.view.index

    @property
    def is_visible(self) -> bool:
        return self.view.is_visible


@dataclass(frozen=True, slots=True)
class ReplayCompletion:
    """The completed lifecycle record plus processed warm-up/visible totals."""

    record: ReplayRunRecord
    warmup_bars: int
    visible_bars: int


@dataclass(frozen=True, slots=True)
class DetectorEventFilter:
    """Exact AND-filter over a newly emitted detector transition.

    ``trigger_id`` is the event reason/type. All fields unset matches any
    DetectorEvent; only events emitted by the current completed bar are tested.
    """

    pattern_id: str | None = None
    pattern_version: str | None = None
    instance_id: str | None = None
    trigger_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "pattern_id", "pattern_version", "instance_id", "trigger_id",
            "from_state", "to_state",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ReplayPipelineError(f"{name} filter must be a non-empty string")

    def matches(self, event: DetectorEvent) -> bool:
        for name in (
            "pattern_id", "pattern_version", "instance_id", "trigger_id",
            "from_state", "to_state",
        ):
            selected = getattr(self, name)
            if selected is not None and getattr(event, name) != selected:
                return False
        return True


@dataclass(frozen=True, slots=True)
class ReplayUntilResult:
    """An event stop or normal end-of-data completion."""

    record: ReplayRunRecord
    processed_bars: int
    stop_step: ReplayStep | None
    matched_event: DetectorEvent | None

    @property
    def stopped_on_event(self) -> bool:
        return self.matched_event is not None


class _RuntimePipeline:
    """Adapt the DetectorRuntime to the SCRUM-63 AnalyticalPipeline protocol."""

    __slots__ = ("_last_result", "_runtime")

    def __init__(self, runtime: DetectorRuntime) -> None:
        self._runtime = runtime
        self._last_result: DetectorRuntimeResult | None = None

    @property
    def last_result(self) -> DetectorRuntimeResult | None:
        return self._last_result

    def process_bar(self, view: ObservableBars) -> None:
        """Advance every component and detector exactly once for this bar."""
        self._last_result = self._runtime.process_bar(view.current_bar)

    def reset(self) -> None:
        self._runtime.reset()


class ReplayPipeline:
    """Drive one pinned ReplayRun bar by bar through the shared runtime.

    Build pipelines with :meth:`from_run`; the constructor assumes every
    pinned input has already been verified and bound.
    """

    __slots__ = (
        "_clock",
        "_component_parameters",
        "_component_generations",
        "_context",
        "_cursor",
        "_failure_context",
        "_pattern_definitions",
        "_processor",
        "_results",
        "_run_id",
        "_runtime",
        "_runtime_generation",
        "_processing_generation",
        "_visible_bars",
        "_warmup_bars",
    )

    def __init__(
        self,
        context: ReplayRunContext,
        runtime: DetectorRuntime,
        sequence: BarSequence,
        *,
        component_parameters: ParameterSpecs | None,
        pattern_definitions: PatternDefinitions | None,
    ) -> None:
        if context.lifecycle.cursor_index != -1:
            raise ReplayPipelineError(
                "advanced replay runs require a new run id and fresh analytical state"
            )
        self._context = context
        self._run_id = UUID(context.lifecycle.run_id)
        self._runtime = runtime
        self._runtime_generation = runtime.reset_generation
        self._processing_generation = runtime.processing_generation
        self._component_generations = tuple(
            component.reset_generation for component in runtime.aggregator.components
        )
        self._processor = _RuntimePipeline(runtime)
        self._clock = SimulationClock(
            sequence,
            selected_start=context.lifecycle.selected_start,
            selected_end=context.lifecycle.selected_end,
        )
        _validate_replay_clock(context, self._clock)
        self._cursor = ReplayCursor(self._clock, self._processor)
        self._component_parameters = component_parameters
        self._pattern_definitions = pattern_definitions
        self._failure_context: str | None = None
        self._results: list[ReplayStep] = []
        self._warmup_bars = 0
        self._visible_bars = 0

    @classmethod
    def from_run(
        cls,
        connection: Connection,
        run_id: UUID,
        *,
        bars: Iterable[Bar] | None = None,
        dataset_store: DatasetStore | None = None,
        bindings_factory: BindingsFactory,
        component_parameters: ParameterSpecs | None = None,
        pattern_definitions: PatternDefinitions | None = None,
        calendar_resolver: CalendarResolver | None = None,
    ) -> ReplayPipeline:
        """Verify a persisted run and wire it to the shared analytical runtime.

        Exactly one source is required: ``dataset_store`` resolves the pinned
        immutable Parquet revision offline, while ``bars`` supports seeded
        fixtures and is authenticated against the same persisted lineage.
        ``calendar_resolver`` resolves the pinned calendar version through
        the SCRUM-71 calendar path.

        ``bindings_factory`` is invoked once and must create fresh detector
        instances exclusively owned by this run. It must never return a
        detector owned by another pipeline. The constructed runtime resets
        these fresh detectors before execution, so their initial state is
        established through the detector protocol rather than assumed.
        """
        context = load_replay_context(
            connection,
            run_id,
            component_parameters=component_parameters,
            pattern_definitions=pattern_definitions,
        )
        if context is None:
            raise ReplayPipelineError("replay run does not exist")
        if context.lifecycle.cursor_index != -1:
            raise ReplayPipelineError(
                "advanced replay runs require a new run id and fresh analytical state"
            )
        cls._verify_pinned_hash(context, component_parameters, pattern_definitions)
        if (bars is None) == (dataset_store is None):
            raise ReplayPipelineError("provide exactly one replay bar source")
        if dataset_store is not None:
            sequence = dataset_store.load_sequence(
                connection, context.snapshot.dataset_revision_id,
                context.detection_config.instrument_id,
                context.detection_config.timeframe,
            )
        else:
            assert bars is not None
            sequence = load_bar_sequence(
                connection, context.snapshot.dataset_revision_id,
                context.detection_config.instrument_id,
                context.detection_config.timeframe, bars,
            )
        calendar = cls._resolve_calendar(context, calendar_resolver)
        runtime = DetectorRuntime(
            context.detection_config,
            tuple(bindings_factory()),
            run_id=context.snapshot.run_id,
            dataset_revision_id=context.snapshot.dataset_revision_id,
            calendar=calendar,
            pinned_calendar_version=(
                context.snapshot.calendar_version if calendar is not None else None
            ),
            pinned_config_hash=context.snapshot.detection_config_hash,
        )
        runtime.reset()
        return cls(
            context,
            runtime,
            sequence,
            component_parameters=component_parameters,
            pattern_definitions=pattern_definitions,
        )

    @staticmethod
    def _verify_pinned_hash(
        context: ReplayRunContext,
        component_parameters: ParameterSpecs | None,
        pattern_definitions: PatternDefinitions | None,
    ) -> None:
        recomputed = detection_config_hash(
            context.detection_config,
            component_parameters=component_parameters,
            pattern_definitions=pattern_definitions,
        )
        if recomputed != context.snapshot.detection_config_hash:
            raise ReplayPipelineError(
                "replay resolved configuration hash differs from the pinned snapshot"
            )

    @staticmethod
    def _resolve_calendar(
        context: ReplayRunContext,
        resolver: CalendarResolver | None,
    ) -> TradingCalendar:
        if resolver is None:
            raise ReplayPipelineError("replay execution requires a pinned calendar resolver")
        calendar = resolver(
            context.detection_config.calendar_id, context.snapshot.calendar_version
        )
        if (
            calendar.calendar_id != context.detection_config.calendar_id
            or calendar.version != context.snapshot.calendar_version
            or calendar.instrument_id != context.detection_config.instrument_id
        ):
            raise ReplayPipelineError(
                "resolved calendar identity or version differs from the pinned run"
            )
        return calendar

    @property
    def run_id(self) -> str:
        return self._context.lifecycle.run_id

    @property
    def snapshot(self) -> RunSnapshotRecord:
        return self._context.snapshot

    @property
    def detection_config(self) -> DetectionAnalysisConfig:
        return self._context.detection_config

    @property
    def runtime(self) -> DetectorRuntime:
        return self._runtime

    @property
    def cursor_index(self) -> int:
        return self._clock.index

    @property
    def has_next(self) -> bool:
        return self._cursor.has_next

    @property
    def steps(self) -> tuple[ReplayStep, ...]:
        """Every committed step in canonical bar order."""
        return tuple(self._results)

    @property
    def warmup_bars(self) -> int:
        """Processed bars before the visible interval."""
        return self._warmup_bars

    @property
    def visible_bars(self) -> int:
        """Processed bars inside the selected visible interval."""
        return self._visible_bars

    @property
    def failure_context(self) -> str | None:
        """Why the pipeline latched, or ``None`` while it remains runnable."""
        return self._failure_context

    def start(self, connection: Connection, *, at: datetime) -> ReplayRunRecord:
        """Move a created run to RUNNING after re-verifying the pinned context."""
        self._require_healthy()
        context = self._verified_context(connection)
        if context.lifecycle.status is not ReplayStatus.CREATED:
            raise ReplayPipelineError("only a created replay run can start")
        try:
            self._validate_continuity(context)
        except Exception as exc:
            self._latch(exc)
            raise
        record = transition_replay_run(connection, self._run_id, ReplayStatus.RUNNING, at=at)
        self._log().info(
            "replay run started",
            extra={
                "phase": "execution",
                "selected_start": context.lifecycle.selected_start.isoformat(),
                "selected_end": context.lifecycle.selected_end.isoformat(),
                **self._component_context(),
            },
        )
        return record

    def resume(self, connection: Connection, *, at: datetime) -> ReplayRunRecord:
        """Resume a paused run with its intact in-memory analytical state."""
        self._require_healthy()
        context = self._verified_context(connection)
        if context.lifecycle.status is not ReplayStatus.PAUSED:
            raise ReplayPipelineError("only a paused replay run can resume")
        try:
            self._validate_continuity(context)
        except Exception as exc:
            self._record_failure(connection, context, exc)
            raise
        record = transition_replay_run(connection, self._run_id, ReplayStatus.RUNNING, at=at)
        self._log().info(
            "replay run resumed",
            extra={
                "phase": "execution",
                "bar_index": self._clock.index,
                **self._component_context(),
            },
        )
        return record

    def step(self, connection: Connection) -> ReplayStep:
        """Process exactly one completed bar and persist the cursor advance.

        The analytical chain runs first; only a successful step advances the
        persisted cursor inside the same savepoint. Any failure keeps the DB
        cursor at the last processed bar, latches the pipeline, and re-raises
        the original error after logging the structured SCRUM-112 record.
        """
        self._require_healthy()
        context = self._verified_context(connection)
        if context.lifecycle.status is not ReplayStatus.RUNNING:
            raise ReplayPipelineError("replay run is not running")
        if not self._cursor.has_next:
            raise ReplayPipelineError("replay cursor has no next observable bar")
        try:
            self._validate_continuity(context)
            with connection.begin_nested():
                view = self._cursor.step_one()
                advance_replay_cursor(connection, self._run_id, view.index)
        except Exception as exc:
            # The failed step's savepoint has rolled back before recording
            # terminal status. The caller owns the outer transaction and must
            # commit it after catching the error to retain this failure record.
            self._record_failure(connection, context, exc)
            raise
        result = self._processor.last_result
        assert result is not None
        recorded = ReplayStep(view=view, result=result)
        self._results.append(recorded)
        if view.is_visible:
            self._visible_bars += 1
        else:
            self._warmup_bars += 1
        self._log().info(
            "replay bar processed",
            extra={
                "phase": "execution",
                "bar_index": view.index,
                "bar_timestamp": view.timestamp.isoformat(),
                "is_visible": view.is_visible,
                "completed_bars": result.frame.completed_bars,
                "events_committed": len(result.events),
                **self._component_context(),
            },
        )
        return recorded

    def run_to_completion(self, connection: Connection, *, at: datetime) -> ReplayCompletion:
        """Start if needed, step every remaining bar, then complete the run.

        Warm-up bars before the visible interval are processed as ordinary
        steps; the returned totals report how many of each were processed.
        Completion reuses ``complete_replay`` so only an exhausted selected
        interval can complete the run.
        """
        self._require_healthy()
        context = self._verified_context(connection)
        if context.lifecycle.status not in (ReplayStatus.CREATED, ReplayStatus.RUNNING):
            raise ReplayPipelineError("replay run is not running")
        if context.lifecycle.status is ReplayStatus.CREATED:
            self.start(connection, at=at)
            context = self._verified_context(connection)
        try:
            self._validate_continuity(context)
        except Exception as exc:
            self._record_failure(connection, context, exc)
            raise
        while self._cursor.has_next:
            self.step(connection)
        record = complete_replay(
            connection,
            self._run_id,
            self._clock,
            at=at,
            component_parameters=self._component_parameters,
            pattern_definitions=self._pattern_definitions,
        )
        return ReplayCompletion(
            record=record,
            warmup_bars=self._warmup_bars,
            visible_bars=self._visible_bars,
        )

    def run_until_event(
        self,
        connection: Connection,
        event_filter: DetectorEventFilter | None = None,
        *,
        at: datetime,
    ) -> ReplayUntilResult:
        """Step sequentially to the first matching visible event or end of data.

        The current bar's committed event tuple is the only evidence examined.
        A matching event pauses the persisted run after that bar, so the next
        call resumes at the next unprocessed bar. Warm-up bars still execute
        but cannot stop the visible walkthrough.
        """
        if event_filter is None:
            event_filter = DetectorEventFilter()
        if not isinstance(event_filter, DetectorEventFilter):
            raise ReplayPipelineError("run-until requires a DetectorEventFilter")
        self._require_healthy()
        context = self._verified_context(connection)
        if context.lifecycle.status is ReplayStatus.CREATED:
            self.start(connection, at=at)
        elif context.lifecycle.status is ReplayStatus.PAUSED:
            self.resume(connection, at=at)
        elif context.lifecycle.status is not ReplayStatus.RUNNING:
            raise ReplayPipelineError("only a created, running, or paused replay can run until")
        context = self._verified_context(connection)
        try:
            self._validate_continuity(context)
        except Exception as exc:
            self._record_failure(connection, context, exc)
            raise
        processed = 0
        while self._cursor.has_next:
            step = self.step(connection)
            processed += 1
            if not step.is_visible:
                continue
            matched = next(
                (event for event in step.result.events if event_filter.matches(event)), None
            )
            if matched is None:
                continue
            try:
                record = transition_replay_run(
                    connection, self._run_id, ReplayStatus.PAUSED, at=at
                )
            except Exception as exc:
                self._record_failure(connection, context, exc)
                raise
            return ReplayUntilResult(record, processed, step, matched)
        record = complete_replay(
            connection,
            self._run_id,
            self._clock,
            at=at,
            component_parameters=self._component_parameters,
            pattern_definitions=self._pattern_definitions,
        )
        return ReplayUntilResult(record, processed, None, None)

    def reset(self) -> None:
        """Refuse an in-place reset: SCRUM-61 reset means a fresh run id.

        :class:`ReplayCursor` offers reset plus a full replay as cursor-level
        recovery, but a persisted ReplayRun has no cursor-reset path
        (``replay_runs`` only advances) and rewriting or re-zeroing an old
        snapshot would break the SCRUM-61 design that reset means a new run id
        with fresh analytical state over the same pinned inputs. Recovery from
        a failed bar is therefore: create a new run over the same dataset
        revision and configuration and replay it from the beginning.
        """
        raise ReplayPipelineError(
            "replay reset requires a new run id and fresh analytical state over the "
            "same pinned inputs; a persisted run is never rewritten or re-zeroed"
        )

    def _verified_context(self, connection: Connection) -> ReplayRunContext:
        context = load_replay_context(
            connection,
            self._run_id,
            component_parameters=self._component_parameters,
            pattern_definitions=self._pattern_definitions,
        )
        if context is None:
            raise ReplayPipelineError("replay run does not exist")
        if (
            context.snapshot != self._context.snapshot
            or context.lineage != self._context.lineage
            or context.detection_config != self._context.detection_config
        ):
            raise ReplayPipelineError("replay pinned identity changed during execution")
        return context

    def _require_healthy(self) -> None:
        if self._failure_context is not None:
            raise ReplayPipelineError(
                f"replay pipeline is latched ({self._failure_context}); "
                "recovery requires a new run id over the same pinned inputs"
            )

    def _validate_continuity(self, context: ReplayRunContext) -> None:
        _validate_replay_clock(context, self._clock)
        last_bar = None if self._clock.index == -1 else self._clock.current.current_bar
        if (
            self._runtime.reset_generation != self._runtime_generation
            or self._runtime.aggregator.completed_bars != self._clock.index + 1
            or self._runtime.processing_generation
            != self._processing_generation + self._clock.index + 1
            or self._runtime.aggregator.last_completed_bar != last_bar
            or any(
                component.reset_generation != generation
                or component.state.completed_bars != self._clock.index + 1
                or component.last_completed_bar != last_bar
                for component, generation in zip(
                    self._runtime.aggregator.components,
                    self._component_generations,
                    strict=True,
                )
            )
        ):
            raise ReplayPipelineError("replay analytical state changed outside the pipeline")

    def _record_failure(
        self, connection: Connection, context: ReplayRunContext, exc: Exception,
    ) -> None:
        self._latch(exc)
        try:
            transition_replay_run(
                connection, self._run_id, ReplayStatus.FAILED,
                at=max(datetime.now(UTC), context.lifecycle.created_at),
                failure_reason=self._failure_context,
            )
        except Exception as persistence_error:
            # An unavailable/invalid outer transaction cannot retain a
            # terminal row. Preserve the original failure and report this
            # secondary persistence failure explicitly; the latch still holds.
            exc.add_note(f"replay FAILED status could not be persisted: {persistence_error}")
            self._log().error(
                "replay failure status could not be persisted",
                extra={"error": str(persistence_error), "phase": "execution"},
            )

    def _latch(self, exc: Exception) -> None:
        """Latch after any failed step and log the structured error record."""
        detector_id: str | None = None
        extra: dict[str, object] = {
            "phase": "execution",
            "bar_index": self._clock.index,
            "bar_timestamp": (
                None if self._clock.timestamp is None else self._clock.timestamp.isoformat()
            ),
            "error": str(exc),
            "recovery": "reset means a new run id over the same pinned inputs",
            **self._component_context(),
        }
        if isinstance(exc, DetectorRuntimeError):
            self._failure_context = str(exc)
            match = _RUNTIME_FAILURE.search(str(exc))
            if match is not None:
                detector_id = f"{match['pattern_id']}@{match['pattern_version']}"
                extra["detector_instance_id"] = match["instance"].strip("'")
        else:
            self._failure_context = f"{type(exc).__name__}: {exc}"
        self._failure_context = self._failure_context[:2048]
        self._log(detector_id).error("replay analytical step failed", extra=extra)

    def _log(self, detector_id: str | None = None) -> ResearchLogger:
        return research_logger(
            run_id=self.run_id,
            dataset_id=self._context.snapshot.dataset_revision_id,
            instrument=self._context.detection_config.instrument_id,
            component=COMPONENT,
            detector_id=detector_id,
            build_id=self._context.snapshot.build_id,
        )

    def _component_context(self) -> dict[str, object]:
        components = sorted(
            item.effective_instance_id
            for item in self._context.detection_config.components
            if item.enabled
        )
        if self._runtime.aggregator.session is not None:
            components.append("session")
        components.sort()
        return {
            "components": components,
            "detectors": [
                f"{binding.definition.pattern_id}@{binding.definition.pattern_version}:"
                f"{binding.effective_instance_id}"
                for binding in self._runtime.bindings
            ],
            "binding_fingerprint": self._runtime.binding_fingerprint,
        }
