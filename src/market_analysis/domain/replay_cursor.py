"""Deterministic replay cursor driving one analytical pipeline over observable bars."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .simulation_clock import ObservableBars, SimulationClock


class ReplayCursorError(ValueError):
    """A replay boundary or per-bar pipeline invariant was violated."""


@runtime_checkable
class AnalyticalPipeline(Protocol):
    """Consume each immutable observable view exactly once, in canonical order."""

    def process_bar(self, view: ObservableBars) -> None: ...

    def reset(self) -> None: ...


class ReplayCursor:
    """Advance a clock one bar at a time, invoking the pipeline once per bar.

    The cursor starts before bar zero and never seeks; every bar is processed
    exactly once until the observable range is exhausted. Replaying identical
    data through an identical pipeline is reproducible after reset. A pipeline
    exception mid-step may leave that bar consumed and analytical state partly
    mutated. Further advancement is prohibited; recovery is reset plus a full
    replay.
    """

    __slots__ = ("_clock", "_pipeline", "_failed")

    def __init__(self, clock: SimulationClock, pipeline: AnalyticalPipeline) -> None:
        """Initialize before bar zero without stepping or processing any bar."""
        if not isinstance(clock, SimulationClock):
            raise ReplayCursorError("replay cursor requires a SimulationClock")
        if not isinstance(pipeline, AnalyticalPipeline):
            raise ReplayCursorError("replay cursor requires an AnalyticalPipeline")
        self._clock = clock
        self._pipeline = pipeline
        self._failed = False

    @property
    def index(self) -> int:
        return self._clock.index

    @property
    def timestamp(self) -> datetime | None:
        return self._clock.timestamp

    @property
    def has_next(self) -> bool:
        """Whether the clock has more bars, regardless of a failure latch."""
        return self._clock.has_next

    @property
    def failed(self) -> bool:
        """Whether a failed advance/reset requires a successful reset before replay."""
        return self._failed

    @property
    def bar_count(self) -> int:
        return self._clock.bar_count

    @property
    def dataset_revision_id(self) -> str:
        return self._clock.dataset_revision_id

    @property
    def source_dataset_id(self) -> str:
        return self._clock.source_dataset_id

    @property
    def canonical_checksum(self) -> str:
        return self._clock.canonical_checksum

    def step_one(self) -> ObservableBars:
        """Advance one bar, process it once, and return the immutable view.

        Past the final bar this raises without mutating anything, so repeated
        calls are an idempotent, explicit end-of-sequence outcome.

        A pipeline exception propagates after the clock has advanced. It may
        leave analytical state partly mutated, so every subsequent advance is
        rejected until reset() succeeds and replay starts again from bar zero.
        """
        self._require_healthy()
        if not self._clock.has_next:
            raise ReplayCursorError(
                "replay cursor has processed the final bar; reset to replay again"
            )
        try:
            view = self._clock.step()
            self._pipeline.process_bar(view)
        except Exception:
            self._failed = True
            raise
        return view

    def step_n(self, count: int) -> tuple[ObservableBars, ...]:
        """Step exactly count bars; the request is validated before any step.

        For a valid count, equivalent to repeated step_one. When count exceeds
        the remaining bars, nothing is processed and the cursor does not
        advance. A pipeline exception propagates mid-batch and latches the
        cursor as in step_one.
        """
        self._require_healthy()
        if isinstance(count, bool) or not isinstance(count, int):
            raise ReplayCursorError("step count must be an integer")
        if count < 0:
            raise ReplayCursorError("step count must be non-negative")
        remaining = self._clock.bar_count - (self._clock.index + 1)
        if count > remaining:
            raise ReplayCursorError(
                f"cannot step {count} bars; only {remaining} remain before the final bar"
            )
        return tuple(self.step_one() for _ in range(count))

    def run_to_end(self) -> int:
        """Process every remaining bar and return how many bars were processed.

        Already exhausting the observable range is a successful no-op of zero
        bars, so repeated calls are idempotent. A pipeline exception propagates
        and latches the cursor as in step_one.
        """
        self._require_healthy()
        processed = 0
        while self._clock.has_next:
            self.step_one()
            processed += 1
        return processed

    def reset(self) -> None:
        """Reset analytical state and return to the initial position before bar zero."""
        try:
            self._pipeline.reset()
            self._clock.reset()
        except Exception:
            self._failed = True
            raise
        self._failed = False

    def _require_healthy(self) -> None:
        if self._failed:
            raise ReplayCursorError(
                "replay cursor is latched after a failed advance or reset; "
                "reset analytical state before replaying from the beginning"
            )
