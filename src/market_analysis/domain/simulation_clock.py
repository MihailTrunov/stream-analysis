"""Causal market-time clock over a validated immutable BarSequence."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .dataset_lineage import BarSequence
from .market_data import Bar


class SimulationClockError(ValueError):
    """A clock boundary or observable-history invariant was violated."""


@dataclass(frozen=True, slots=True)
class ObservableBars:
    """A persistent past-only view with no reference to the clock's future bars."""

    current_bar: Bar
    index: int
    dataset_revision_id: str
    visible_start: datetime
    _previous: ObservableBars | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.index < 0 or (
            self._previous is None and self.index != 0
        ) or (
            self._previous is not None and self._previous.index != self.index - 1
        ):
            raise SimulationClockError("observable index is not a contiguous past-only prefix")

    @property
    def timestamp(self) -> datetime:
        return self.current_bar.timestamp

    @property
    def visible_count(self) -> int:
        return self.index + 1

    @property
    def is_visible(self) -> bool:
        return self.timestamp >= self.visible_start

    def bar_at(self, index: int) -> Bar:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= self.index:
            raise SimulationClockError("future or invalid bar index is not observable")
        node: ObservableBars = self
        while node.index != index:
            assert node._previous is not None
            node = node._previous
        return node.current_bar

    def timestamp_at(self, index: int) -> datetime:
        return self.bar_at(index).timestamp

    def bar_at_time(self, timestamp: datetime) -> Bar:
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise SimulationClockError("requested timestamp must be timezone-aware")
        selected = timestamp.astimezone(UTC)
        if selected > self.timestamp:
            raise SimulationClockError("future timestamp is not observable")
        node: ObservableBars | None = self
        while node is not None and node.timestamp > selected:
            node = node._previous
        if node is not None and node.timestamp == selected:
            return node.current_bar
        raise SimulationClockError("timestamp is not a visible bar")

    def history(self) -> tuple[Bar, ...]:
        """Return a read-only prefix; callers never receive future bars."""
        result = []
        node: ObservableBars | None = self
        while node is not None:
            result.append(node.current_bar)
            node = node._previous
        result.reverse()
        return tuple(result)


class SimulationClock:
    """Advance only one canonical completed bar at a time in market time."""

    __slots__ = ("_sequence", "_bars", "_index", "_current", "_selected_start", "_selected_end")

    def __init__(
        self,
        sequence: BarSequence,
        *,
        selected_start: datetime | None = None,
        selected_end: datetime | None = None,
    ) -> None:
        if not isinstance(sequence, BarSequence) or not sequence.bars:
            raise SimulationClockError("simulation clock requires a nonempty BarSequence")
        if (selected_start is None) != (selected_end is None):
            raise SimulationClockError("selected range requires both boundaries")
        start = sequence.bars[0].timestamp if selected_start is None else selected_start
        end = sequence.lineage.actual_end if selected_end is None else selected_end
        if (
            not isinstance(start, datetime) or start.tzinfo is None
            or start.utcoffset() is None
            or not isinstance(end, datetime) or end.tzinfo is None
            or end.utcoffset() is None
        ):
            raise SimulationClockError("selected boundaries must be timezone-aware")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start:
            raise SimulationClockError("selected end must follow start")
        bars = sequence.bars
        first_visible = bisect_left(bars, start, key=lambda bar: bar.timestamp)
        stop = bisect_left(bars, end, key=lambda bar: bar.timestamp)
        if first_visible >= stop:
            raise SimulationClockError("selected interval has no observable bars")
        self._sequence = sequence
        self._bars = bars[:stop]
        self._index = -1
        self._current: ObservableBars | None = None
        self._selected_start = start
        self._selected_end = end

    @property
    def dataset_revision_id(self) -> str:
        return self._sequence.lineage.dataset_revision_id

    @property
    def source_dataset_id(self) -> str:
        return self._sequence.lineage.source_dataset_id

    @property
    def canonical_checksum(self) -> str:
        return self._sequence.lineage.canonical_checksum

    @property
    def selected_start(self) -> datetime:
        return self._selected_start

    @property
    def selected_end(self) -> datetime:
        return self._selected_end

    @property
    def source_bar_count(self) -> int:
        return self._sequence.lineage.bar_count

    @property
    def bar_count(self) -> int:
        return len(self._bars)

    @property
    def index(self) -> int:
        return self._index

    @property
    def timestamp(self) -> datetime | None:
        return None if self._index < 0 else self._bars[self._index].timestamp

    @property
    def has_next(self) -> bool:
        return self._index + 1 < self.bar_count

    @property
    def current(self) -> ObservableBars:
        if self._current is None:
            raise SimulationClockError("no bar is observable before the first step")
        return self._current

    def step(self) -> ObservableBars:
        if not self.has_next:
            raise SimulationClockError("simulation clock is at the final bar")
        self._index += 1
        self._current = ObservableBars(
            self._bars[self._index], self._index, self.dataset_revision_id,
            self._selected_start, self._current,
        )
        return self.current

    def reset(self) -> None:
        """Return to before bar zero; analytical components reset separately."""
        self._index = -1
        self._current = None
