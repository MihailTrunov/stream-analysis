from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from market_analysis.domain import Bar, SessionWindow, Timeframe, TradingCalendar
from market_analysis.domain.aggregation import AggregationError, CandleAggregator, bucket_bounds


def calendar(zone: str = "UTC", boundary: time = time(0)) -> TradingCalendar:
    return TradingCalendar(
        "test",
        "1",
        "fake",
        "test",
        "US30",
        zone,
        boundary,
        (SessionWindow("first", boundary, time(12)), SessionWindow("second", time(12), boundary)),
    )


def bar(stamp: datetime, index: int = 0) -> Bar:
    price = Decimal("100.10") + index
    return Bar("US30", Timeframe.M1, stamp, price, price + 2, price - 1, price + 1)


@pytest.mark.parametrize(
    "timeframe,minutes",
    [
        (Timeframe.M1, 1),
        (Timeframe.M5, 5),
        (Timeframe.M15, 15),
        (Timeframe.M30, 30),
        (Timeframe.H1, 60),
        (Timeframe.H4, 240),
        (Timeframe.D1, 1440),
    ],
)
def test_exact_ohlc_and_close_availability(timeframe: Timeframe, minutes: int) -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    aggregator = CandleAggregator(timeframe, calendar())
    for i in range(minutes - 1):
        assert aggregator.push(bar(start + timedelta(minutes=i), i)) == ()
    (result,) = aggregator.push(bar(start + timedelta(minutes=minutes - 1), minutes - 1))
    assert result.bar.timestamp == start
    assert result.bar.open == Decimal("100.10")
    assert result.bar.high == Decimal("101.10") + minutes
    assert result.bar.low == Decimal("99.10")
    assert result.bar.close == Decimal("100.10") + minutes
    assert result.available_at == start + timedelta(minutes=minutes)
    assert result.source_count == minutes


def test_revision_origin_truncation_is_not_a_full_bucket() -> None:
    start = datetime(2026, 1, 5, 10, 2, tzinfo=UTC)
    aggregator = CandleAggregator(Timeframe.M5, calendar())
    assert not any(aggregator.push(bar(start + timedelta(minutes=i))) for i in range(3))
    results = [
        candle for i in range(3, 8) for candle in aggregator.push(bar(start + timedelta(minutes=i)))
    ]
    assert len(results) == 1
    assert results[0].bar.timestamp.minute == 5


def test_missing_open_minutes_fail_closed() -> None:
    start = datetime(2026, 1, 5, 10, tzinfo=UTC)
    aggregator = CandleAggregator(Timeframe.M5, calendar())
    aggregator.push(bar(start))
    with pytest.raises(AggregationError, match="missing expected-open"):
        aggregator.push(bar(start + timedelta(minutes=2)))
    with pytest.raises(AggregationError, match="failed"):
        aggregator.push(bar(start + timedelta(minutes=1)))


def test_scheduled_break_is_not_a_gap_and_incremental_matches_batch() -> None:
    cal = TradingCalendar(
        "test",
        "1",
        "fake",
        "test",
        "US30",
        "UTC",
        time(0),
        (SessionWindow("day", time(9), time(17)),),
        breaks=(SessionWindow("break", time(10, 2), time(10, 4)),),
    )
    start = datetime(2026, 1, 5, 10, tzinfo=UTC)
    source = [bar(start + timedelta(minutes=i), i) for i in (0, 1, 4, 5, 6, 7, 8, 9)]
    one, two = CandleAggregator(Timeframe.M5, cal), CandleAggregator(Timeframe.M5, cal)
    result = tuple(c for b in source for c in one.push(b))
    replayed = tuple(c for part in (source[:3], source[3:]) for b in part for c in two.push(b))
    assert result == replayed
    assert result[0].source_count == 3
    assert result[0].bar.close == source[2].close


def test_daily_dst_uses_real_calendar_day_and_intraday_stays_utc() -> None:
    cal = calendar("Europe/London")
    start, end = bucket_bounds(datetime(2026, 3, 29, 12, tzinfo=UTC), Timeframe.D1, cal)
    assert start == datetime(2026, 3, 29, tzinfo=UTC)
    assert end - start == timedelta(hours=23)
    assert bucket_bounds(datetime(2026, 3, 29, 12, 9, tzinfo=UTC), Timeframe.H4, cal) == (
        datetime(2026, 3, 29, 12, tzinfo=UTC),
        datetime(2026, 3, 29, 16, tzinfo=UTC),
    )


def test_trading_day_boundary_clips_intraday_bucket() -> None:
    cal = calendar(boundary=time(1))
    start, end = bucket_bounds(datetime(2026, 1, 5, 23, tzinfo=UTC), Timeframe.H4, cal)
    assert start.hour == 20
    assert end.hour == 0  # UTC bucket ends before the next 01:00 trading boundary
    start, end = bucket_bounds(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), Timeframe.H4, cal)
    assert start.hour == 0 and end.hour == 1


def test_boundary_shortened_candle_is_labelled_and_observed_at_boundary() -> None:
    cal = calendar(boundary=time(1))
    start = datetime(2026, 1, 6, 0, tzinfo=UTC)
    aggregator = CandleAggregator(Timeframe.H4, cal)
    results = [c for i in range(60) for c in aggregator.push(bar(start + timedelta(minutes=i), i))]
    assert len(results) == 1
    assert results[0].available_at == start + timedelta(hours=1)
    assert results[0].source_count == 60
    assert "boundary_shortened" in results[0].bar.quality_flags


def test_provider_observed_aggregation_uses_actual_minutes_and_marks_uncertainty():
    from dataclasses import replace

    from market_analysis.domain.aggregation import OBSERVED_AGGREGATION_VERSION

    start = datetime(2026, 11, 10, 10, tzinfo=UTC)
    observed = replace(calendar(), observed_only=True, version='provider-observed-v1:1')
    aggregator = CandleAggregator(Timeframe.M5, observed)
    completed = tuple(
        candle for index in (0, 2, 4)
        for candle in aggregator.push(bar(start + index * timedelta(minutes=1), index))
    )
    assert len(completed) == 1
    assert completed[0].source_count == 3
    assert completed[0].available_at == start + timedelta(minutes=5)
    assert completed[0].aggregation_version == OBSERVED_AGGREGATION_VERSION
    assert completed[0].bar.quality_flags >= {
        'calendar_unverified', 'source_coverage_unclassified',
    }
