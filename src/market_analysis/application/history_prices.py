"""Completed chart candles from a bounded, authenticated source window."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from sqlalchemy import Connection

from market_analysis.domain import DatasetLineage, Timeframe, TradingCalendar
from market_analysis.domain.aggregation import (
    MINUTE,
    AnalyticalCandle,
    CandleAggregator,
    bucket_bounds,
)
from market_analysis.persistence.dataset_store import DatasetStore

from .analysis_clock import verify_closed_interval


def completed_source_window(
    calendar: TradingCalendar,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: int,
) -> tuple[datetime, datetime] | None:
    """Select the last nonempty completed calendar buckets without reading prices."""
    cursor = end
    first: datetime | None = None
    last: datetime | None = None
    count = 0
    while cursor > start and count < limit:
        left, right = bucket_bounds(cursor - timedelta(microseconds=1), timeframe, calendar)
        cursor = left
        if left < start:
            break
        if right > end:
            continue  # A partially elapsed chart candle cannot be returned.
        minute = left
        while minute < right and not calendar.derive(minute).is_open:
            minute += MINUTE
        if minute >= right:
            continue
        first = left
        last = last or right
        count += 1
    return (first, last) if first is not None and last is not None else None


def load_completed_candles(
    connection: Connection,
    store: DatasetStore,
    lineage: DatasetLineage,
    calendar: TradingCalendar,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: int,
) -> tuple[AnalyticalCandle, ...]:
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start >= end
        or isinstance(limit, bool)
        or not 1 <= limit <= 500
    ):
        raise ValueError("history needs ordered aware bounds and limit 1..500")
    if start < lineage.requested_start or end > lineage.requested_end:
        raise ValueError("history lies outside the retained revision")
    window = completed_source_window(calendar, timeframe, start, end, limit)
    if window is None:
        # Even an empty/partially elapsed view must authenticate its pinned input.
        store.read_range(
            connection,
            lineage.dataset_revision_id,
            lineage.instrument_id,
            Timeframe.M1,
            start,
            end,
            limit=1,
        )
        return ()
    first, last = window
    aggregator = CandleAggregator(timeframe, calendar)
    candles: deque[AnalyticalCandle] = deque(maxlen=limit)
    last_close = first
    for bar in store.iter_verified_range(
        connection,
        lineage.dataset_revision_id,
        lineage.instrument_id,
        Timeframe.M1,
        first,
        last,
    ):
        # The aggregator checks interior gaps. Check the leading interval too,
        # so a missing first minute cannot look like a truncated revision origin.
        if last_close == first:
            verify_closed_interval(calendar, first, bar.timestamp)
        candles.extend(aggregator.push(bar))
        last_close = bar.timestamp + MINUTE
    verify_closed_interval(calendar, last_close, last)
    candles.extend(aggregator.advance(last))
    return tuple(candles)
