"""Wall-time replay controls over the causal, completed-bar ReplayCursor."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from market_analysis.domain import ObservableBars, ReplayCursor


class PlaybackError(ValueError):
    """A playback mode, speed, or elapsed-time contract was violated."""


class PlaybackMode(StrEnum):
    PAUSED = "paused"
    PACED = "paced"
    MAXIMUM = "maximum"


class PlaybackController:
    """Schedule cursor steps without changing market time or analytical semantics.

    The caller supplies elapsed monotonic wall time; this controller never
    sleeps, owns a timer, or processes a bar outside ReplayCursor. The caller
    owns the cursor and its analytical pipeline for inspecting state/events.
    """

    __slots__ = ("_cursor", "_mode", "_resume_mode", "_rate", "_credit")

    def __init__(self, cursor: ReplayCursor) -> None:
        if not isinstance(cursor, ReplayCursor):
            raise PlaybackError("playback requires a ReplayCursor")
        self._cursor = cursor
        self._mode = PlaybackMode.PAUSED
        self._resume_mode: PlaybackMode | None = None
        self._rate: Decimal | None = None
        self._credit = Decimal(0)

    @property
    def mode(self) -> PlaybackMode:
        return self._mode

    @property
    def bars_per_second(self) -> Decimal | None:
        """The selected paced rate, retained while paused for resume."""
        return self._rate

    @property
    def cursor(self) -> ReplayCursor:
        return self._cursor

    def play(self, bars_per_second: Decimal | int | float) -> None:
        """Start/resume paced playback, or change its rate without moving a bar."""
        if isinstance(bars_per_second, bool) or not isinstance(
            bars_per_second, Decimal | int | float
        ):
            raise PlaybackError("bars_per_second must be a positive finite number")
        rate = Decimal(str(bars_per_second))
        if not rate.is_finite() or rate <= 0:
            raise PlaybackError("bars_per_second must be a positive finite number")
        self._require_runnable()
        if self._mode is PlaybackMode.PACED and self._rate == rate:
            return
        self._mode = PlaybackMode.PACED
        self._resume_mode = PlaybackMode.PACED
        self._rate = rate
        self._credit = Decimal(0)

    def play_maximum(self) -> None:
        """Drain the cursor on the next tick without intentional delay."""
        self._require_runnable()
        if self._mode is PlaybackMode.MAXIMUM:
            return
        self._mode = PlaybackMode.MAXIMUM
        self._resume_mode = PlaybackMode.MAXIMUM
        self._rate = None
        self._credit = Decimal(0)

    def resume(self) -> None:
        """Continue the last selected speed after pause, with fresh wall-time credit."""
        if self._resume_mode is None:
            raise PlaybackError("no playback speed has been selected for resume")
        self._require_runnable()
        if self._mode is self._resume_mode:
            return
        self._mode = self._resume_mode
        self._credit = Decimal(0)

    def pause(self) -> None:
        """Stop scheduling bars, preserving analytical state at the current bar."""
        self._mode = PlaybackMode.PAUSED
        self._credit = Decimal(0)

    def step_one(self) -> ObservableBars:
        """Advance exactly one bar while paused, through the owned cursor."""
        if self._mode is not PlaybackMode.PAUSED:
            raise PlaybackError("manual stepping requires paused playback")
        return self._cursor.step_one()

    def tick(self, elapsed: timedelta = timedelta(0)) -> int:
        """Advance bars due at the current speed; return the number processed.

        Fractional paced credit is accumulated only while playing. Pause,
        resume and rate changes discard it, so idle wall time never catches
        up later. A failed analytical step propagates and pauses playback;
        ReplayCursor's failure latch governs recovery.
        """
        if not isinstance(elapsed, timedelta) or elapsed < timedelta(0):
            raise PlaybackError("elapsed time must be a non-negative timedelta")
        if self._mode is PlaybackMode.PAUSED:
            return 0
        if self._cursor.failed:
            self.pause()
            raise PlaybackError("replay cursor is latched; reset it before playback")
        if not self._cursor.has_next:
            self.pause()
            return 0
        try:
            if self._mode is PlaybackMode.MAXIMUM:
                processed = self._cursor.run_to_end()
            else:
                assert self._rate is not None
                elapsed_microseconds = (
                    (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000
                    + elapsed.microseconds
                )
                self._credit += Decimal(elapsed_microseconds) * self._rate / 1_000_000
                remaining = self._cursor.bar_count - self._cursor.index - 1
                due = remaining if self._credit >= remaining else int(self._credit)
                processed = 0
                while processed < due:
                    self._cursor.step_one()
                    processed += 1
                self._credit -= processed
        except Exception:
            self.pause()
            raise
        if not self._cursor.has_next:
            self.pause()
        return processed

    def _require_runnable(self) -> None:
        if self._cursor.failed:
            raise PlaybackError("replay cursor is latched; reset it before playback")
        if not self._cursor.has_next:
            raise PlaybackError("replay cursor is exhausted; reset or seek before playback")
