"""One deterministic detector runtime for every pattern detector.

The runtime owns the per-bar sequence of the analytical pipeline: the owned
:class:`MarketStateAggregator` finalizes the canonical
:class:`MarketStateFrame`, every bound detector observes the SAME immutable
:class:`DetectorInput`, emitted :class:`TransitionIntent` values are validated
against the SCRUM-79 lifecycle module, and the instance updates plus
committed :class:`DetectorEvent` records apply atomically at the runtime's
in-memory boundary. SCRUM-61 owns replay wiring and SCRUM-81 owns
persistence; neither concern lives here.

Detector execution order (deterministic; there are no detector-specific
branches): bindings sort by ``(pattern_id, pattern_version)`` in ascending
codepoint order — the same canonical pattern order the DetectionConfigHash
uses — so two configs carrying identical selections in different tuple order
execute detectors identically under one hash. Several registered versions of
one selected pattern may execute together; each resolves the selection's
parameter overrides against its own definition. Duplicate
``(pattern_id, pattern_version)`` bindings are rejected.

Causality: the runtime never exposes future bars, OutcomeObservations or
evaluation results. An intent's ``event_time`` may cite an earlier extreme
only when a canonical market event carrying that ``event_time`` is already
visible: the runtime keeps a committed event-time ledger across frames and
rejects otherwise-unseen earlier citations, so no look-ahead can enter
through detector rationales. Same-bar citations (``event_time ==``
``detection_time``) are accepted because the closed bar itself is observable.

Fail-closed recovery: a detector exception, an illegal intent or a rejected
lifecycle sequence fails the whole bar's step and leaves the committed
instance/event boundary exactly at the prior bar, and LATCHES the runtime:
every later ``process_bar``/``process_frame`` is rejected until ``reset()``.
The finalized frame is consumed but unprocessed, so the only sound recovery
is ``reset()`` followed by a full replay from the pinned inputs — the same
contract as :class:`~market_analysis.domain.replay_cursor.ReplayCursor`.
There is no partial commit and no resume from partial state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from market_analysis.config import (
    ConfigurationError,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.domain import Bar, TradingCalendar
from market_analysis.indicators import MarketStateAggregator, MarketStateFrame
from market_analysis.patterns import (
    LifecycleRunner,
    LifecycleTransition,
    PatternDefinition,
    PatternDefinitionError,
)

from .inputs import DetectorInput
from .records import (
    DetectorEvent,
    DetectorOutput,
    PatternInstance,
    TransitionIntent,
    json_value,
)


class DetectorRuntimeError(ValueError):
    """A detector step, emitted intent, or runtime input violated the contract."""


@runtime_checkable
class PatternDetector(Protocol):
    """Consume each immutable detector input exactly once, in configured order."""

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DetectorBinding:
    """One registered detector implementation bound to one definition version.

    ``instance_id`` names the in-memory occurrence the binding advances; it
    defaults to the pattern id. Multiple bindings may share a pattern id when
    they bind different registered versions.
    """

    definition: PatternDefinition
    detector: PatternDetector
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, PatternDefinition):
            raise DetectorRuntimeError("DetectorBinding requires a PatternDefinition")
        if self.instance_id is not None and not self.instance_id.strip():
            raise DetectorRuntimeError("instance_id must be a non-empty string when given")

    @property
    def effective_instance_id(self) -> str:
        """The occurrence identity this binding advances."""
        return self.definition.pattern_id if self.instance_id is None else self.instance_id


@dataclass(frozen=True, slots=True)
class DetectorRuntimeResult:
    """Committed outcome of one completed bar's detector step.

    ``events`` lists this bar's committed DetectorEvent intents in execution
    order and ``instances`` the post-step occurrence state per binding in the
    same deterministic order.
    """

    frame: MarketStateFrame
    events: tuple[DetectorEvent, ...]
    instances: tuple[PatternInstance, ...]


class _Slot:
    """Internal per-binding execution state; the runtime owns the lifecycle runner."""

    __slots__ = ("binding", "definition", "detector", "instance_id", "parameters", "runner")

    def __init__(
        self,
        binding: DetectorBinding,
        definition: PatternDefinition,
        instance_id: str,
    ) -> None:
        self.binding = binding
        self.definition = definition
        self.detector = binding.detector
        self.instance_id = instance_id
        self.parameters: Mapping[str, object] = MappingProxyType({})
        self.runner = LifecycleRunner(definition)


class DetectorRuntime:
    """Drive every bound detector through one deterministic per-bar path.

    The runtime resolves the run config against the bindings' registered
    definitions, owns the canonical aggregator, and advances one
    :class:`LifecycleRunner` per binding so SCRUM-79 stays the sole authority
    for lifecycle state. Detector inputs are built from the shared frame and
    the pre-bar instances, so no detector can observe another detector's
    outputs within the same bar.

    The detection config hash covers the SELECTED pattern versions only;
    runtimes bound with different registered version sets (for example an
    extra registered v2 alongside a v1-only selection) share one hash. Use
    :attr:`binding_fingerprint` to distinguish the executed binding set;
    SCRUM-81 persistence must pin it alongside the config hash.
    """

    _LATCHED_MESSAGE = (
        "runtime is latched after a failed step; only reset() followed by a "
        "full replay can continue (no resume from partial state)"
    )

    __slots__ = (
        "_aggregator",
        "_bindings",
        "_config",
        "_dataset_revision_id",
        "_detection_config_hash",
        "_events",
        "_failed",
        "_instances",
        "_last_detection_time",
        "_ledger",
        "_processing_generation",
        "_reset_generation",
        "_run_id",
        "_sequences",
    )

    def __init__(
        self,
        config: DetectionAnalysisConfig,
        bindings: Sequence[DetectorBinding],
        *,
        run_id: str,
        dataset_revision_id: str,
        calendar: TradingCalendar | None = None,
        pinned_calendar_version: str | None = None,
        pinned_config_hash: str | None = None,
    ) -> None:
        """Resolve the config, order the bindings, and build the aggregator."""
        if not isinstance(config, DetectionAnalysisConfig):
            raise DetectorRuntimeError("config must be a frozen DetectionAnalysisConfig")
        for name, value in (("run_id", run_id), ("dataset_revision_id", dataset_revision_id)):
            if not isinstance(value, str) or not value.strip():
                raise DetectorRuntimeError(f"{name} must be a non-empty string")
        slots = [self._slot(binding, position) for position, binding in enumerate(tuple(bindings))]
        definitions = {slot.definition.identity: slot.definition for slot in slots}
        self._validate_selections(config, slots)
        try:
            resolved = resolve_detection_config(config, pattern_definitions=definitions)
        except ConfigurationError as exc:
            raise DetectorRuntimeError(f"invalid detector runtime config: {exc}") from exc
        self._config = resolved
        self._run_id = run_id
        self._dataset_revision_id = dataset_revision_id
        self._detection_config_hash = detection_config_hash(
            resolved, pattern_definitions=definitions
        )
        self._aggregator = MarketStateAggregator(
            resolved,
            run_id=run_id,
            dataset_revision_id=dataset_revision_id,
            calendar=calendar,
            pinned_calendar_version=pinned_calendar_version,
            pinned_config_hash=pinned_config_hash,
            pattern_definitions=definitions,
        )
        self._bindings = self._ordered(slots)
        for slot in self._bindings:
            selection = next(
                item
                for item in config.patterns
                if item.pattern_id == slot.definition.pattern_id
            )
            overrides = {parameter.name: parameter.value for parameter in selection.parameters}
            try:
                slot.parameters = slot.definition.resolve_parameters(overrides)
            except PatternDefinitionError as exc:
                raise DetectorRuntimeError(
                    f"pattern {slot.definition.pattern_id}@"
                    f"{slot.definition.pattern_version} cannot resolve parameters: {exc}"
                ) from exc
        self._reset_generation = 0
        self._processing_generation = 0
        self._reset_state()

    @staticmethod
    def _slot(binding: DetectorBinding, position: int) -> _Slot:
        if not isinstance(binding, DetectorBinding):
            raise DetectorRuntimeError(f"binding {position} must be a DetectorBinding")
        if not isinstance(binding.detector, PatternDetector):
            raise DetectorRuntimeError(
                f"binding {position} detector must satisfy the PatternDetector protocol"
            )
        return _Slot(binding, binding.definition, binding.effective_instance_id)

    @staticmethod
    def _validate_selections(
        config: DetectionAnalysisConfig,
        slots: list[_Slot],
    ) -> None:
        identities = [slot.definition.identity for slot in slots]
        if len(identities) != len(set(identities)):
            raise DetectorRuntimeError(
                "duplicate detector binding for one pattern_id/pattern_version"
            )
        bound = {slot.definition.pattern_id for slot in slots}
        for selection in config.patterns:
            if selection.enabled and selection.pattern_id not in bound:
                raise DetectorRuntimeError(
                    f"enabled pattern selection {selection.pattern_id}@"
                    f"{selection.pattern_version} has no detector binding"
                )
        selected = {item.pattern_id for item in config.patterns if item.enabled}
        for slot in slots:
            if slot.definition.pattern_id not in selected:
                raise DetectorRuntimeError(
                    f"detector binding pattern {slot.definition.pattern_id}@"
                    f"{slot.definition.pattern_version} has no enabled pattern selection"
                )

    @staticmethod
    def _ordered(slots: list[_Slot]) -> tuple[_Slot, ...]:
        """Sort by the canonical pattern order the config hash uses, then version.

        Hashing canonicalizes pattern selections into sorted (pattern_id,
        version) form; deriving execution order from the same sort guarantees
        that identical hashes imply identical detector order and event commit
        order, including after ``from_canonical_json`` rehydration re-sorts a
        hand-authored config tuple.
        """
        return tuple(
            sorted(
                slots,
                key=lambda slot: (
                    slot.definition.pattern_id,
                    slot.definition.pattern_version,
                ),
            )
        )

    @property
    def config(self) -> DetectionAnalysisConfig:
        """The resolved run config, including the pattern selections."""
        return self._config

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def dataset_revision_id(self) -> str:
        return self._dataset_revision_id

    @property
    def detection_config_hash(self) -> str:
        return self._detection_config_hash

    @property
    def aggregator(self) -> MarketStateAggregator:
        """The owned canonical frame producer; reset it through the runtime."""
        return self._aggregator

    @property
    def binding_fingerprint(self) -> str:
        """Fingerprint the executed binding set: order plus identities.

        Unlike the detection config hash (which covers only the selected
        pattern versions), this digest distinguishes runtimes that execute
        different registered version sets under one config, so replay
        verification and SCRUM-81 persistence can pin what actually ran.
        """
        payload = json.dumps(
            [
                [slot.definition.pattern_id, slot.definition.pattern_version, slot.instance_id]
                for slot in self._bindings
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @property
    def bindings(self) -> tuple[DetectorBinding, ...]:
        """The detector bindings in deterministic execution order."""
        return tuple(slot.binding for slot in self._bindings)

    @property
    def instances(self) -> tuple[PatternInstance, ...]:
        """The current occurrence state per binding in execution order."""
        return tuple(self._instances[self._key(slot)] for slot in self._bindings)

    @property
    def events(self) -> tuple[DetectorEvent, ...]:
        """Every committed DetectorEvent intent, in deterministic commit order."""
        return self._events

    @property
    def reset_generation(self) -> int:
        """Monotonic reset identity for consumers enforcing stream continuity."""
        return self._reset_generation

    @property
    def processing_generation(self) -> int:
        """Monotonic accepted processing attempts, including failed attempts.

        Drivers use this read-only identity to detect processing through
        either public entry point outside their own completed-bar sequence.
        Reset does not erase this history; rejected type or latched-runtime
        calls never enter processing and do not increment it.
        """
        return self._processing_generation

    def reset(self) -> None:
        """Restore detectors, instances, ledger and the aggregator to the initial state."""
        self._aggregator.reset()
        for slot in self._bindings:
            slot.runner = LifecycleRunner(slot.definition)
            slot.detector.reset()
        self._reset_state()

    def process_bar(self, bar: Bar) -> DetectorRuntimeResult:
        """Finalize the canonical frame for one completed bar, then run the step.

        The aggregator advances first; a later detector-phase failure leaves
        the frame consumed but unprocessed and latches the runtime, and
        recovery is ``reset()`` plus a full replay.
        """
        if self._failed:
            raise DetectorRuntimeError(self._LATCHED_MESSAGE)
        if not isinstance(bar, Bar):
            raise DetectorRuntimeError("process_bar requires a canonical Bar")
        self._processing_generation += 1
        frame = self._aggregator.update(bar)
        return self._step(frame)

    def process_frame(self, frame: MarketStateFrame) -> DetectorRuntimeResult:
        """Run the detector step for an already-finalized frame.

        Live and replay drivers that finalize frames themselves use this
        entry point; the frame must carry the runtime's pinned lineage. A
        detector-phase failure latches the runtime (no silent skip of the
        failed step, no re-processing of the same frame) and recovery is
        ``reset()`` plus a full replay.
        """
        if self._failed:
            raise DetectorRuntimeError(self._LATCHED_MESSAGE)
        if not isinstance(frame, MarketStateFrame):
            raise DetectorRuntimeError("process_frame requires a MarketStateFrame")
        self._processing_generation += 1
        return self._step(frame)

    def debug_json(self) -> str:
        """Serialize committed events and instance state with stable key order."""
        payload = {
            "run_id": self._run_id,
            "dataset_revision_id": self._dataset_revision_id,
            "detection_config_hash": self._detection_config_hash,
            "config": json.loads(self._config.canonical_json()),
            "bindings": [
                {
                    "pattern_id": slot.definition.pattern_id,
                    "pattern_version": slot.definition.pattern_version,
                    "instance_id": slot.instance_id,
                }
                for slot in self._bindings
            ],
            "instances": [
                {
                    "pattern_id": instance.pattern_id,
                    "pattern_version": instance.pattern_version,
                    "instance_id": instance.instance_id,
                    "state": instance.state,
                    "context": json_value(instance.context),
                }
                for instance in self.instances
            ],
            "events": [event.to_canonical_dict() for event in self._events],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _reset_state(self) -> None:
        self._failed = False
        self._instances = {
            self._key(slot): PatternInstance(
                slot.definition.pattern_id,
                slot.definition.pattern_version,
                slot.instance_id,
                slot.definition.lifecycle_states[0],
            )
            for slot in self._bindings
        }
        self._sequences = {self._key(slot): 0 for slot in self._bindings}
        self._events: tuple[DetectorEvent, ...] = ()
        self._ledger: dict[datetime, datetime] = {}
        self._last_detection_time: datetime | None = None
        self._reset_generation += 1

    def _step(self, frame: MarketStateFrame) -> DetectorRuntimeResult:
        self._validate_frame(frame)
        detection_time = frame.bar.timestamp
        if self._last_detection_time is not None and detection_time <= self._last_detection_time:
            raise DetectorRuntimeError(
                f"frame detection_time {detection_time.isoformat()} must be strictly increasing"
            )
        ledger = dict(self._ledger)
        for event in frame.market_events_this_bar:
            known = ledger.get(event.event_time)
            if known is None or event.detection_time < known:
                ledger[event.event_time] = event.detection_time
        prepared = [self._prepare(slot, frame, ledger, detection_time) for slot in self._bindings]
        events: list[DetectorEvent] = []
        instances = dict(self._instances)
        sequences = dict(self._sequences)
        for slot, output, transitions in prepared:
            key = self._key(slot)
            self._advance(slot, frame, transitions)
            for intent in transitions:
                sequence = sequences[key]
                sequences[key] = sequence + 1
                events.append(
                    DetectorEvent(
                        run_id=self._run_id,
                        dataset_revision_id=self._dataset_revision_id,
                        instrument_id=self._config.instrument_id,
                        timeframe=self._config.timeframe.value,
                        detection_config_hash=self._detection_config_hash,
                        pattern_id=intent.pattern_id,
                        pattern_version=intent.pattern_version,
                        instance_id=intent.instance_id,
                        sequence=sequence,
                        from_state=intent.from_state,
                        to_state=intent.to_state,
                        trigger_id=intent.trigger_id,
                        event_time=intent.event_time,
                        detection_time=intent.detection_time,
                        rationale=intent.rationale,
                    )
                )
            instances[key] = PatternInstance(
                slot.definition.pattern_id,
                slot.definition.pattern_version,
                slot.instance_id,
                slot.runner.state,
                output.instance.context,
            )
        self._instances = instances
        self._sequences = sequences
        self._events = (*self._events, *events)
        self._ledger = ledger
        self._last_detection_time = detection_time
        return DetectorRuntimeResult(
            frame=frame,
            events=tuple(events),
            instances=tuple(instances[self._key(slot)] for slot in self._bindings),
        )

    def _validate_frame(self, frame: MarketStateFrame) -> None:
        if not frame.bar.is_complete:
            raise DetectorRuntimeError("frame bar must be completed")
        if frame.run_id != self._run_id:
            raise DetectorRuntimeError("frame run_id does not match the runtime run identity")
        if frame.dataset_revision_id != self._dataset_revision_id:
            raise DetectorRuntimeError("frame dataset revision does not match the runtime")
        if frame.detection_config_hash != self._detection_config_hash:
            raise DetectorRuntimeError("frame config hash does not match the runtime config hash")
        if frame.bar.instrument_id != self._config.instrument_id:
            raise DetectorRuntimeError("frame instrument does not match the run config")
        if frame.bar.timeframe != self._config.timeframe:
            raise DetectorRuntimeError("frame timeframe does not match the run config")

    def _prepare(
        self,
        slot: _Slot,
        frame: MarketStateFrame,
        ledger: Mapping[datetime, datetime],
        detection_time: datetime,
    ) -> tuple[_Slot, DetectorOutput, tuple[TransitionIntent, ...]]:
        """Run one detector and validate its output without mutating any state."""
        instance = self._instances[self._key(slot)]
        bar_input = DetectorInput(
            frame=frame,
            definition=slot.definition,
            parameters=slot.parameters,
            config=self._config,
            detection_config_hash=self._detection_config_hash,
            run_id=self._run_id,
            dataset_revision_id=self._dataset_revision_id,
            instance=instance,
        )
        try:
            output = slot.detector.process_bar(bar_input)
        except Exception as exc:
            detail = f"detector raised {type(exc).__name__}: {exc}"
            raise self._failure(slot, frame, detail) from exc
        if not isinstance(output, DetectorOutput):
            raise self._failure(slot, frame, "detector step must return a DetectorOutput")
        self._validate_intents(slot, frame, instance, output, ledger, detection_time)
        return slot, output, output.transitions

    def _validate_intents(
        self,
        slot: _Slot,
        frame: MarketStateFrame,
        instance: PatternInstance,
        output: DetectorOutput,
        ledger: Mapping[datetime, datetime],
        detection_time: datetime,
    ) -> None:
        definition = slot.definition
        expected = (definition.pattern_id, definition.pattern_version, slot.instance_id)
        # NOTE: instance context and intent rationale are frozen and identity-
        # checked here, but validated against the definition's context_schema
        # and rationale_condition_ids only at the SCRUM-81 persistence
        # boundary (deferred there by design; detectors may carry richer
        # debug context than the persisted schema allows).
        if output.instance.identity != expected:
            raise self._failure(
                slot,
                frame,
                f"output instance identity {output.instance.identity} does not match the binding",
            )
        declared = {
            (item.from_state, item.to_state, item.trigger_id) for item in definition.transitions
        }
        used: set[str] = set()
        for position, intent in enumerate(output.transitions):
            if intent.identity != expected:
                raise self._failure(
                    slot,
                    frame,
                    f"intent {position} identity {intent.identity} does not match the binding",
                )
            edge = (intent.from_state, intent.to_state, intent.trigger_id)
            if edge not in declared:
                raise self._failure(
                    slot,
                    frame,
                    f"transition {intent.from_state}->{intent.to_state} via "
                    f"{intent.trigger_id!r} is not declared by the pattern lifecycle",
                )
            if intent.trigger_id in used:
                raise self._failure(
                    slot, frame, f"trigger {intent.trigger_id!r} already participates in this bar"
                )
            used.add(intent.trigger_id)
            if intent.detection_time != detection_time:
                raise self._failure(
                    slot,
                    frame,
                    f"intent {position} detection_time "
                    f"{intent.detection_time.isoformat()} is not the current bar",
                )
            if intent.event_time > detection_time:
                raise self._failure(
                    slot,
                    frame,
                    f"intent {position} event_time "
                    f"{intent.event_time.isoformat()} is not observable yet",
                )
            if intent.event_time != detection_time:
                visible_at = ledger.get(intent.event_time)
                if visible_at is None or visible_at > detection_time:
                    raise self._failure(
                        slot,
                        frame,
                        f"intent {position} event_time {intent.event_time.isoformat()} cites "
                        f"no market event visible by {detection_time.isoformat()}",
                    )
        probe = LifecycleRunner(definition, initial_state=instance.state)
        try:
            derived = probe.advance(frame.bar, [item.trigger_id for item in output.transitions])
        except PatternDefinitionError as exc:
            raise self._failure(
                slot, frame, f"emitted sequence rejected by the pattern lifecycle: {exc}"
            ) from exc
        if _triples(derived) != _triples(output.transitions):
            derived_text = [
                (item.from_state, item.to_state, item.trigger_id) for item in derived
            ]
            raise self._failure(
                slot,
                frame,
                f"emitted transitions do not match the lifecycle-derived sequence {derived_text}",
            )
        if output.instance.state != probe.state:
            raise self._failure(
                slot,
                frame,
                f"output instance state {output.instance.state!r} does not match the "
                f"lifecycle state {probe.state!r}",
            )

    def _advance(
        self,
        slot: _Slot,
        frame: MarketStateFrame,
        transitions: tuple[TransitionIntent, ...],
    ) -> tuple[LifecycleTransition, ...]:
        """Commit the validated sequence through the binding's own lifecycle runner."""
        try:
            derived = slot.runner.advance(frame.bar, [item.trigger_id for item in transitions])
        except PatternDefinitionError as exc:  # pragma: no cover - guarded by the probe
            raise self._failure(
                slot, frame, f"lifecycle runner rejected a validated sequence: {exc}"
            ) from exc
        if _triples(derived) != _triples(transitions):  # pragma: no cover - probe guarantees
            raise self._failure(slot, frame, "committed sequence diverged from the probe")
        return derived

    def _failure(
        self,
        slot: _Slot,
        frame: MarketStateFrame,
        detail: str,
    ) -> DetectorRuntimeError:
        self._failed = True
        return DetectorRuntimeError(
            f"run_id={self._run_id!r} dataset_revision_id={self._dataset_revision_id!r} "
            f"pattern={slot.definition.pattern_id}@{slot.definition.pattern_version} "
            f"instance={slot.instance_id!r} bar={frame.bar.timestamp.isoformat()}: {detail}"
        )

    @staticmethod
    def _key(slot: _Slot) -> tuple[str, str, str]:
        return (slot.definition.pattern_id, slot.definition.pattern_version, slot.instance_id)


def _triples(
    transitions: Sequence[LifecycleTransition] | tuple[TransitionIntent, ...],
) -> list[tuple[str, str, str]]:
    return [(item.from_state, item.to_state, item.trigger_id) for item in transitions]
