"""Higher-timeframe prices select only complete calendar buckets and required source rows."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from market_analysis.application.history_prices import load_completed_candles
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    DatasetLineage,
    Instrument,
    ProviderSymbolMapping,
    SessionWindow,
    Timeframe,
    TradingCalendar,
    ValidationStatus,
    canonical_bar_checksum,
)
from market_analysis.domain.aggregation import CandleAggregator
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)
from market_analysis.persistence.runs import metadata

START = datetime(2026, 1, 5, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def _calendar(zone="UTC", windows=None):
    return TradingCalendar(
        "test-calendar",
        "cal-v1",
        "fixture",
        "account",
        "US30",
        zone,
        time(0),
        windows or (SessionWindow("am", time(0), time(12)), SessionWindow("pm", time(12), time(0))),
    )


def _bar(stamp, index):
    price = Decimal("100") + Decimal(index) / 100
    return Bar(
        "US30",
        Timeframe.M1,
        stamp,
        price,
        price + Decimal("0.02"),
        price - Decimal("0.01"),
        price + Decimal("0.01"),
        volume=Decimal("12.5"),
        source_id="fixture",
    )


@pytest.fixture
def setup(tmp_path):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        register_instrument(
            connection,
            Instrument(
                "US30",
                "Prices",
                "test-calendar",
                2,
                Decimal("0.01"),
                (ProviderSymbolMapping("fixture", "US30"),),
            ),
        )
    yield engine, DatasetStore(tmp_path)
    engine.dispose()


def _publish(setup, calendar, start, end, *, missing=()):
    engine, store = setup
    bars = []
    slot = start
    while slot < end:
        if calendar.derive(slot).is_open and slot not in missing:
            bars.append(_bar(slot, len(bars)))
        slot += MINUTE
    bars = tuple(bars)
    lineage = DatasetLineage(
        "prices",
        "source",
        "US30",
        Timeframe.M1,
        start,
        end,
        bars[0].timestamp if bars else None,
        bars[-1].timestamp + MINUTE if bars else None,
        len(bars),
        end,
        ValidationStatus.PASS,
        '{"fixture":true}',
        "a" * 64,
        canonical_bar_checksum(bars),
        BAR_CHECKSUM_VERSION,
        DATASET_FORMAT_VERSION,
    )
    revision = DatasetRevision(
        "prices",
        "study",
        "source",
        "fixture",
        end,
        end,
        "fixture-v1",
        calendar.version,
        DATASET_FORMAT_VERSION,
        (DatasetMembership("US30", Timeframe.M1, start, end, len(bars)),),
        "datasets/prices/manifest.json",
    )
    with engine.begin() as connection:
        store.publish(connection, revision, lineage, bars)
    return lineage, bars


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_indexed_window_matches_full_aggregation_and_streams_only_the_tail(
    setup, monkeypatch, timeframe
):
    engine, store = setup
    calendar = _calendar()
    end = START + timedelta(days=2)
    lineage, bars = _publish(setup, calendar, START, end)
    reference = CandleAggregator(timeframe, calendar)
    expected = [c for bar in bars for c in reference.push(bar)]
    expected.extend(reference.advance(end))
    reads = []
    original = DatasetStore.iter_verified_range

    def tracked(self, *args):
        reads.append((args[-2], args[-1]))
        yield from original(self, *args)

    def no_full_sequence(*_args):
        raise AssertionError("chart history must not materialize a revision")

    monkeypatch.setattr(DatasetStore, "iter_verified_range", tracked)
    monkeypatch.setattr(DatasetStore, "load_sequence", no_full_sequence)
    with engine.connect() as connection:
        result = load_completed_candles(
            connection, store, lineage, calendar, timeframe, START, end, 1
        )
    assert result == tuple(expected[-1:])
    assert reads == [(expected[-1].bar.timestamp, end)]
    assert reads[0][0] > START


@pytest.mark.parametrize(
    "timeframe,minutes,expected_count",
    [
        (Timeframe.M1, 1, 1),
        (Timeframe.M5, 9, 1),
        (Timeframe.M5, 4, 0),
        (Timeframe.M15, 29, 1),
    ],
)
def test_partial_end_never_reveals_a_candle_before_its_close(
    setup, timeframe, minutes, expected_count
):
    engine, store = setup
    calendar = _calendar()
    end = START + timedelta(hours=1)
    lineage, _ = _publish(setup, calendar, START, end)
    cutoff = START + timedelta(minutes=minutes, seconds=30)
    with engine.connect() as connection:
        result = load_completed_candles(
            connection, store, lineage, calendar, timeframe, START, cutoff, 500
        )
    assert len(result) == expected_count
    assert all(c.available_at <= cutoff for c in result)


def test_truncated_origin_and_start_inside_bucket_do_not_create_partial_candles(setup):
    engine, store = setup
    calendar = _calendar()
    start, end = START + timedelta(minutes=2), START + timedelta(minutes=16)
    lineage, _ = _publish(setup, calendar, start, end)
    with engine.connect() as connection:
        result = load_completed_candles(
            connection,
            store,
            lineage,
            calendar,
            Timeframe.M5,
            START + timedelta(minutes=7),
            end,
            500,
        )
    assert [c.bar.timestamp for c in result] == [START + timedelta(minutes=10)]


def test_scheduled_close_finalizes_last_short_open_bucket(setup):
    engine, store = setup
    calendar = _calendar(windows=(SessionWindow("day", time(9), time(10, 2)),))
    start, end = START + timedelta(hours=9), START + timedelta(hours=11)
    lineage, _ = _publish(setup, calendar, start, end)
    with engine.connect() as connection:
        result = load_completed_candles(
            connection, store, lineage, calendar, Timeframe.M5, start, end, 1
        )
    assert len(result) == 1
    assert result[0].bar.timestamp == START + timedelta(hours=10)
    assert result[0].available_at == START + timedelta(hours=10, minutes=5)
    assert result[0].source_count == 2


@pytest.mark.parametrize("minute", [10, 12, 14])
def test_missing_first_interior_or_last_required_source_minute_fails(setup, minute):
    engine, store = setup
    calendar = _calendar()
    end = START + timedelta(minutes=15)
    lineage, _ = _publish(setup, calendar, START, end, missing=(START + minute * MINUTE,))
    with engine.connect() as connection:
        with pytest.raises(ValueError, match="missing expected-open"):
            load_completed_candles(
                connection, store, lineage, calendar, Timeframe.M5, START, end, 1
            )


def test_gap_before_selected_candles_does_not_force_a_full_history_recalculation(setup):
    engine, store = setup
    calendar = _calendar()
    end = START + timedelta(minutes=15)
    lineage, _ = _publish(setup, calendar, START, end, missing=(START + MINUTE,))
    with engine.connect() as connection:
        result = load_completed_candles(
            connection, store, lineage, calendar, Timeframe.M5, START, end, 1
        )
    assert len(result) == 1
    assert result[0].bar.timestamp == START + timedelta(minutes=10)


@pytest.mark.parametrize("date,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_daily_window_uses_actual_uk_day_duration_across_dst(setup, date, hours):
    from zoneinfo import ZoneInfo

    engine, store = setup
    calendar = _calendar("Europe/London")
    local = datetime.fromisoformat(date).replace(tzinfo=ZoneInfo("Europe/London"))
    start, end = local.astimezone(UTC), (local + timedelta(days=1)).astimezone(UTC)
    lineage, _ = _publish(setup, calendar, start, end)
    with engine.connect() as connection:
        result = load_completed_candles(
            connection, store, lineage, calendar, Timeframe.D1, start, end, 1
        )
    assert len(result) == 1
    assert result[0].available_at - result[0].bar.timestamp == timedelta(hours=hours)
    assert result[0].source_count == hours * 60


def test_intraday_window_preserves_shortened_trading_day_boundary_bucket(setup):
    engine, store = setup
    calendar = TradingCalendar(
        "test-calendar",
        "cal-v1",
        "fixture",
        "account",
        "US30",
        "UTC",
        time(17),
        (SessionWindow("overnight", time(17), time(12)), SessionWindow("day", time(12), time(17))),
    )
    start, end = START + timedelta(hours=17), START + timedelta(days=1, hours=17)
    lineage, bars = _publish(setup, calendar, start, end)
    aggregator = CandleAggregator(Timeframe.H4, calendar)
    expected = [c for bar in bars for c in aggregator.push(bar)]
    expected.extend(aggregator.advance(end))
    with engine.connect() as connection:
        result = load_completed_candles(
            connection,
            store,
            lineage,
            calendar,
            Timeframe.H4,
            start,
            end,
            2,
        )
    assert result == tuple(expected[-2:])
    assert result[-1].source_count == 60
    assert "boundary_shortened" in result[-1].bar.quality_flags


def test_empty_partial_window_still_rejects_a_corrupted_pinned_source(setup):
    engine, store = setup
    calendar = _calendar()
    end = START + timedelta(minutes=10)
    lineage, _ = _publish(setup, calendar, START, end)
    with (store.root / "datasets/prices/bars.parquet").open("ab") as changed:
        changed.write(b"corruption")
    with engine.connect() as connection:
        with pytest.raises(ValueError, match="checksum differs"):
            load_completed_candles(
                connection,
                store,
                lineage,
                calendar,
                Timeframe.M5,
                START,
                START + timedelta(minutes=4),
                500,
            )
