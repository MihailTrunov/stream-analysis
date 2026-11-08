"""Price transport is independent of calendars; analysis inputs stay separately validated."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.provider_prices import (
    ProviderPrices,
    ProviderPriceStore,
    price_key,
)
from market_analysis.config.oanda_uk_calendars import OANDA_UK_LIVE_PROFILES
from market_analysis.domain import Bar, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.domain.historical_data import HistoricalDataRequest

NOW = datetime(2026, 11, 10, 15, 2, 30, tzinfo=UTC)


def feed(instrument="US30"):
    profile = next(p for p in OANDA_UK_LIVE_PROFILES.values() if p.instrument_id == instrument)
    return HistoryFeed(instrument_id=instrument, provider_symbol=profile.provider_symbol,
                       calendar_version=profile.version,
                       account_fingerprint=sha256(b"test").hexdigest())


class Source:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.failure = False

    def price_window(self, instrument, *, end, count):
        self.calls.append((instrument.instrument_id, end, count))
        if self.failure:
            raise RuntimeError("private-account-and-token-must-never-appear")
        start = end - timedelta(minutes=count)
        request = HistoricalDataRequest(instrument, Timeframe.M1, start, end)
        bars = tuple(Bar(instrument.instrument_id, Timeframe.M1, start + timedelta(minutes=i),
                         "100", "103", "99", "101", source_id="oanda-midpoint")
                     for i in range(count))
        return HistoricalDataPage(request, bars, HistoricalSource(
            "oanda", instrument.provider_symbol("oanda", environment="live"),
            "oanda-midpoint", "live"))

    def close(self):
        self.closed = True


@pytest.fixture
def prices(tmp_path):
    sources = []
    clock = [NOW]

    def factory(_feed):
        source = Source()
        sources.append(source)
        return source

    manager = ProviderPrices(root=lambda: tmp_path, source=factory, clock=lambda: clock[0],
                             threaded=False)
    yield manager, sources, clock, tmp_path
    manager.close()


@pytest.mark.parametrize("instrument", ["US30", "DAX"])
def test_missing_calendar_does_not_block_latest_or_historical_fetch(prices, instrument):
    manager, sources, _, _ = prices
    selected = feed(instrument)
    result = manager.window(selected, Timeframe.M1)
    assert len(result["bars"]) == 1440
    assert result["calendar_status"] == "unverified"
    assert result["analysis_ready"] is False
    historical_end = datetime(2022, 2, 1, 15, tzinfo=UTC)
    history = manager.window(selected, Timeframe.M1, historical_end)
    assert len(history["bars"]) == 1440
    assert history["calendar_status"] == "unverified"
    assert len(sources[0].calls) == 2
    assert sources[0].calls[-1][1] == historical_end


def test_raw_cache_survives_restart_and_failed_provider_without_becoming_a_revision(prices):
    manager, sources, clock, root = prices
    selected = feed()
    expected = manager.window(selected, Timeframe.M1)["bars"]
    assert list((root / "provider-prices").rglob("*.parquet"))
    assert not (root / "datasets").exists()
    manager.close()
    source = Source()
    source.failure = True
    restored = ProviderPrices(root=lambda: root, source=lambda _: source,
                              clock=lambda: clock[0], threaded=False)
    try:
        result = restored.window(selected, Timeframe.M1)
        assert result["bars"] == expected
        assert "retained prices" in result["detail"]
        assert "private-account" not in str(result)
    finally:
        restored.close()


def test_shared_polling_and_exact_redelivery_do_not_rewrite_parquet(prices):
    manager, sources, clock, root = prices
    selected = feed()
    manager.subscribe("one", selected)
    manager.subscribe("two", selected)
    manager.subscribe("one", selected)
    manager.window(selected, Timeframe.M1)
    paths = list(root.rglob("*.parquet"))
    mtimes = [p.stat().st_mtime_ns for p in paths]
    manager.window(selected, Timeframe.M1)
    assert len(sources) == len(sources[0].calls) == 1
    clock[0] += timedelta(seconds=3)
    manager.poll(manager.channel(selected))  # No DOM/client read triggers this acquisition.
    assert len(sources[0].calls) == 2
    assert [p.stat().st_mtime_ns for p in paths] == mtimes
    clock[0] += timedelta(minutes=1)
    manager.poll(manager.channel(selected))
    persisted = manager.channel(selected).store.read(clock[0], 1)
    assert persisted[0].timestamp == clock[0].replace(second=0, microsecond=0) - timedelta(
        minutes=1)
    manager.unsubscribe("one")
    assert manager.channel(selected).clients == {"two"}


def test_completed_price_conflict_is_rejected_and_account_cache_is_separate(prices):
    manager, _, _, root = prices
    selected = feed()
    manager.window(selected, Timeframe.M1)
    store = ProviderPriceStore(root, selected)
    bars = store.read(NOW, 1)
    with pytest.raises(ValueError, match="conflicts"):
        store.retain((replace(bars[0], close="102"),))
    assert store.read(NOW, 1) == bars
    assert price_key(selected) != price_key(selected.model_copy(update={
        "account_fingerprint": sha256(b"other").hexdigest()}))
    assert price_key(selected) == price_key(
        selected.model_copy(update={"calendar_version": "next"}))


@pytest.mark.parametrize("timeframe", [Timeframe.M5, Timeframe.H4, Timeframe.D1])
def test_display_aggregates_are_from_m1_and_cannot_claim_analysis_completeness(prices, timeframe):
    manager, sources, _, _ = prices
    result = manager.window(feed(), timeframe)
    assert result["bars"]
    assert all(not b["is_complete"] for b in result["bars"])
    assert result["aggregation"] == "display_only"
    assert result["analysis_ready"] is False
    assert sources[0].calls[0][2] == 1440


def test_price_api_fetches_after_calendar_expiry_and_sanitizes_errors(prices, monkeypatch):
    from market_analysis.api import live
    from market_analysis.api.app import app
    manager, sources, _, _ = prices
    monkeypatch.setenv("OANDA_ACCOUNT", "test")
    monkeypatch.setenv("OANDA_KEY", "secret-test-token")
    monkeypatch.setenv("OANDA_ENV", "live")
    monkeypatch.setenv("OANDA_REGION", "UK")
    monkeypatch.setattr(live, "provider_prices", manager)
    client = TestClient(app)
    response = client.get('/live/prices/US30')
    assert response.status_code == 200
    assert response.json()['calendar_status'] == 'unverified'
    id = str(uuid4())
    assert client.post('/live/price-subscriptions', json={
        'subscription_id': id, 'instrument_id': 'US30'}).status_code == 201
    assert client.delete(f'/live/price-subscriptions/{id}').status_code == 200
    sources[0].failure = True
    failed = client.get('/live/prices/US30?end=2022-01-03T15:00:00Z')
    assert failed.status_code == 503
    assert 'private-account' not in failed.text and 'secret' not in failed.text


def test_bad_price_window_is_rejected_before_provider(prices):
    manager, sources, _, _ = prices
    for end in (NOW, NOW + timedelta(days=1), NOW.replace(tzinfo=None)):
        with pytest.raises(ValueError):
            manager.window(feed(), Timeframe.M1, end)
    assert sources == []


def test_historical_reads_coalesce_and_refresh_failure_preserves_cached_prices(prices):
    manager, sources, clock, _ = prices
    end = NOW.replace(second=0, microsecond=0) - timedelta(days=1)
    first = manager.window(feed(), Timeframe.M1, end)
    assert manager.window(feed(), Timeframe.M1, end)["bars"] == first["bars"]
    assert len(sources[0].calls) == 1
    channel = manager.channel(feed())
    channel.history_reads.clear()
    sources[0].failure = True
    retry = manager.window(feed(), Timeframe.M1, end)
    assert retry["bars"] == first["bars"]
    assert "retained prices" in retry["detail"]
    # Near-current historical ends are rechecked for delayed provider finalization.
    recent_end = NOW.replace(second=0, microsecond=0)
    manager.window(feed(), Timeframe.M1, recent_end)
    recent_source = sources[-1]
    calls = len(recent_source.calls)
    clock[0] += timedelta(seconds=3)
    manager.window(feed(), Timeframe.M1, recent_end)
    assert len(recent_source.calls) == calls + 1


def test_partial_daily_price_preview_includes_requested_day_without_future_availability(prices):
    manager, _, _, _ = prices
    end = NOW.replace(second=0, microsecond=0)
    result = manager.window(feed(), Timeframe.D1, end)
    last = result["bars"][-1]
    assert datetime.fromisoformat(last["timestamp"]) < end
    assert datetime.fromisoformat(last["available_at"]) == end
    assert last["is_complete"] is False
