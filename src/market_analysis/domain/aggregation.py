"""Versioned completed-candle construction, shared by every analytical mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .market_data import Bar, Timeframe
from .session_calendar import TradingCalendar

AGGREGATION_VERSION = "m1-aggregation-v1"
MINUTES = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}
MINUTE = timedelta(minutes=1)


class AggregationError(ValueError):
    """Source coverage, finalization or stream identity is invalid."""


def bucket_bounds(
    timestamp: datetime, timeframe: Timeframe, calendar: TradingCalendar
) -> tuple[datetime, datetime]:
    day_start, day_end = calendar.trading_day_bounds(timestamp)
    if timeframe is Timeframe.D1:
        return day_start, day_end
    seconds = MINUTES[timeframe] * 60
    start = datetime.fromtimestamp(int(timestamp.timestamp()) // seconds * seconds, UTC)
    return max(start, day_start), min(start + timedelta(seconds=seconds), day_end)


@dataclass(frozen=True, slots=True)
class AnalyticalCandle:
    bar: Bar
    available_at: datetime
    source_start: datetime
    source_end: datetime
    source_count: int
    aggregation_version: str = AGGREGATION_VERSION


class CandleAggregator:
    """Consume finalized M1 in order; return only watermark-complete buckets.

    A revision beginning mid-bucket skips that truncated first bucket. Unexpected
    open-slot gaps fail closed; caller must reinitialize after a failed input.
    """

    def __init__(self, timeframe: Timeframe, calendar: TradingCalendar) -> None:
        self.timeframe = timeframe
        self.calendar = calendar
        self._origin: datetime | None = None
        self._previous: datetime | None = None
        self._watermark: datetime | None = None
        self._pending: dict[tuple[datetime, datetime], list[Bar]] = {}
        self._failed = False

    @property
    def pending_end(self) -> datetime | None:
        return min((end for _, end in self._pending), default=None)

    def push(self, bar: Bar) -> tuple[AnalyticalCandle, ...]:
        if self._failed:
            raise AggregationError("aggregation failed; reinitialize from pinned inputs")
        try:
            if (
                bar.timeframe is not Timeframe.M1
                or not bar.is_complete
                or bar.instrument_id != self.calendar.instrument_id
                or bar.timestamp.second
                or bar.timestamp.microsecond
                or not self.calendar.derive(bar.timestamp).is_open
            ):
                raise AggregationError("aggregation requires finalized, open-session canonical M1")
            if self._previous is not None:
                if bar.timestamp <= self._previous:
                    raise AggregationError("source minutes must be strictly increasing")
                missing = self._previous + MINUTE
                while missing < bar.timestamp:
                    if self.calendar.derive(missing).is_open:
                        raise AggregationError(
                            f"missing expected-open source minute {missing.isoformat()}"
                        )
                    missing += MINUTE
            completed = self.advance(bar.timestamp)
            if self._origin is None:
                self._origin = bar.timestamp
            bounds = bucket_bounds(bar.timestamp, self.timeframe, self.calendar)
            self._pending.setdefault(bounds, []).append(bar)
            self._previous = bar.timestamp
            return completed + self.advance(bar.timestamp + MINUTE)
        except ValueError:
            self._failed = True
            raise

    def advance(self, watermark: datetime) -> tuple[AnalyticalCandle, ...]:
        if self._failed or watermark.tzinfo is None:
            raise AggregationError("invalid or failed finalized-source watermark")
        if self._watermark is not None and watermark < self._watermark:
            raise AggregationError("finalized-source watermark cannot move backwards")
        self._watermark = watermark
        completed = []
        for (start, end), bars in tuple(self._pending.items()):
            if end > watermark:
                continue
            expected = []
            minute = start
            while minute < end:
                if self.calendar.derive(minute).is_open:
                    expected.append(minute)
                minute += MINUTE
            del self._pending[start, end]
            if not expected or self._origin is None or expected[0] < self._origin:
                continue
            if [bar.timestamp for bar in bars] != expected:
                self._failed = True
                raise AggregationError(f"incomplete source coverage for bucket {start.isoformat()}")
            flags = frozenset().union(*(bar.quality_flags for bar in bars))
            if (
                self.timeframe is not Timeframe.D1
                and end - start != MINUTES[self.timeframe] * MINUTE
            ):
                flags |= {"boundary_shortened"}
            volume = (
                None
                if any(bar.volume is None for bar in bars)
                else sum(
                    (bar.volume for bar in bars if bar.volume is not None), start=bars[0].open * 0
                )
            )
            completed.append(
                AnalyticalCandle(
                    bars[0]
                    if self.timeframe is Timeframe.M1
                    else Bar(
                        bars[0].instrument_id,
                        self.timeframe,
                        start,
                        bars[0].open,
                        max(bar.high for bar in bars),
                        min(bar.low for bar in bars),
                        bars[-1].close,
                        volume=volume,
                        source_id=AGGREGATION_VERSION,
                        quality_flags=flags,
                    ),
                    end,
                    bars[0].timestamp,
                    bars[-1].timestamp + MINUTE,
                    len(bars),
                )
            )
        return tuple(completed)
