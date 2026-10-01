"""Deterministic replay cursor driving one analytical pipeline over observable bars."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .simulation_clock import ObservableBars, SimulationClock, SimulationClockError


class ReplayCursorError(ValueError):
    """A replay boundary or per-bar pipeline invariant was violated."""


@runtime_checkable
class AnalyticalPipeline(Protocol):
    """Consume each immutable observable view exactly once, in canonical order."""

    def process_bar(self, view: ObservableBars) -> None: ...

    def reset(self) -> None: ...


class ReplayCursor:
    """Advance a clock one bar at a time, invoking the pipeline once per bar.

    The cursor starts before bar zero. Each uninterrupted pass processes every
    bar exactly once until the observable range is exhausted; seeking resets
    and replays the canonical prefix when already partway through a pass.
    Replaying identical data through an identical pipeline is reproducible.
    A pipeline exception mid-step may leave that bar consumed and analytical state partly
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

    def seek_to_index(self, target: int) -> ObservableBars | None:
        """Reconstruct state through an exact zero-based index, including warm-up.

        ``-1`` selects the initial state before bar zero. Invalid targets are
        rejected before any reset or processing. A seek to the current index
        is a no-op; all other non-initial seeks from a processed position reset
        and replay from bar zero. A processing/reset failure latches the cursor.
        """
        self._require_healthy()
        if isinstance(target, bool) or not isinstance(target, int):
            raise ReplayCursorError("seek index must be an integer")
        if not -1 <= target < self.bar_count:
            raise ReplayCursorError(
                f"seek index must be between -1 and {self.bar_count - 1}"
            )
        if target == self.index:
            return None if target == -1 else self._clock.current
        if self.index != -1:
            self.reset()
        if target == -1:
            return None
        result: ObservableBars | None = None
        while self.index < target:
            result = self.step_one()
        return result

    def seek_to_time(self, timestamp: datetime) -> ObservableBars:
        """Seek to a completed bar at this exact aware timestamp; never clamp."""
        self._require_healthy()
        try:
            target = self._clock.index_for_timestamp(timestamp)
        except SimulationClockError as exc:
            raise ReplayCursorError(str(exc)) from exc
        result = self.seek_to_index(target)
        assert result is not None
        return result

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
