"""Live acquisition is shared, causal, durable and independent of rendering clients."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_ingress import LiveIngress
from market_analysis.application.live_retention import feed_key, retain_completed_minute
from market_analysis.config.oanda_uk_calendars import build_oanda_uk_calendar
from market_analysis.domain import Bar, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.domain.live_data import LiveCandleUpdate
from market_analysis.persistence.live_candles import live_candles
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 28, 14, tzinfo=UTC)


def bar(minute, *, complete=True, close="101"):
    return Bar("US30", Timeframe.M1, START + timedelta(minutes=minute),
               Decimal("100"), Decimal("103"), Decimal("99"), Decimal(close),
               source_id="oanda-midpoint", is_complete=complete)


class Source:
    def __init__(self):
        self.bars = [bar(0), bar(1)]
        self.forming = bar(2, complete=False)
        self.calls = []
        self.closed = False
        self.failure = None

    def poll_live(self, request, *, at):
        self.calls.append(request)
        if self.failure:
            raise RuntimeError(self.failure)
        return LiveCandleUpdate(HistoricalDataPage(
            request, tuple(item for item in self.bars
                           if request.start <= item.timestamp < request.end),
            HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
        ), at, self.forming)

    def close(self):
        self.closed = True


@pytest.fixture
def ingress(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ingress.sqlite'}")
    metadata.create_all(engine)
    now = [START + timedelta(minutes=2, seconds=30)]
    sources = []
    calendar = build_oanda_uk_calendar(provider="oanda", region="UK", environment="live",
                                     account="test", provider_symbol="US30_USD")
    feed = HistoryFeed(instrument_id="US30", provider_symbol="US30_USD",
                       calendar_version=calendar.version,
                       account_fingerprint=sha256(b"test").hexdigest())

    def factory(_feed):
        source = Source()
        sources.append(source)
        return source

    hub = LiveIngress(engine=lambda: engine, source=factory, clock=lambda: now[0], threaded=False)
    yield hub, engine, feed, now, sources
    hub.close()
    engine.dispose()


def subscribe(ingress, client="tab-one", start=START):
    hub, _, feed, _, _ = ingress
    hub.subscribe(client, feed, start)
    return feed_key(feed)


def test_two_subscribers_share_acquisition_and_only_completed_minutes_are_durable(ingress):
    hub, engine, _, _, sources = ingress
    key = subscribe(ingress)
    subscribe(ingress, "tab-two")
    hub.poll(key)
    assert len(sources) == 1
    assert len(sources[0].calls) == 1
    assert hub.state("tab-one")["subscribers"] == 2
    assert hub.state("tab-one")["available_at"] == "2026-09-28T14:02:00Z"
    assert hub.state("tab-one")["forming"]["is_complete"] is False
    assert hub.state("tab-two")["available_at"] == hub.state("tab-one")["available_at"]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_candles)) == 2


def test_ingestion_continues_without_client_reads_and_forming_never_advances_close(ingress):
    hub, _, _, now, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    sources[0].bars.append(bar(2))
    sources[0].forming = bar(3, complete=False)
    now[0] += timedelta(minutes=1)
    hub.poll(key)  # No state/view request is needed to trigger ingestion.
    state = hub.state("tab-one")
    assert state["available_at"] == "2026-09-28T14:03:00Z"
    assert state["forming"]["timestamp"] == "2026-09-28T14:03:00Z"
    prices = hub.bars("tab-one", START, START + timedelta(minutes=3), 500)
    assert len(prices["bars"]) == 3
    assert all(item["is_complete"] for item in prices["bars"])
    with pytest.raises(ValueError, match="confirmed prefix"):
        hub.bars("tab-one", START, START + timedelta(minutes=4), 500)


def test_gap_caps_visible_prefix_until_missing_completed_minute_is_repaired(ingress):
    hub, engine, _, _, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    # Restart at the same start using a new manager and an out-of-order arrival gap.
    source = sources[0]
    hub.close()
    source.bars = [bar(1)]
    restarted = LiveIngress(engine=lambda: engine, source=lambda _: source,
                            clock=lambda: START + timedelta(minutes=2, seconds=30), threaded=False)
    try:
        restarted.subscribe("restored", ingress[2], START)
        restarted.poll(key)
        state = restarted.state("restored")
        assert state["status"] == "gap"
        assert state["available_at"] == "2026-09-28T14:00:00Z"
        assert restarted.bars("restored", START, START, 500)["bars"] == []
        source.bars = [bar(0), bar(1)]
        restarted.poll(key)
        assert restarted.state("restored")["status"] == "connected"
        assert restarted.state("restored")["available_at"] == "2026-09-28T14:02:00Z"
    finally:
        restarted.close()


def test_conflicting_retained_data_stops_the_feed_without_advancing_watermark(ingress):
    hub, engine, feed, now, sources = ingress
    key = subscribe(ingress)
    retain_completed_minute(engine, feed, bar(0, close="102"), at=now[0])
    hub.poll(key)
    assert hub.state("tab-one")["status"] == "conflict"
    assert hub.state("tab-one")["available_at"] == "2026-09-28T14:00:00Z"
    hub.poll(key)
    assert len(sources[0].calls) == 1


def test_overlapping_repoll_detects_a_changed_previously_completed_minute(ingress):
    hub, _, _, _, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    sources[0].bars[1] = bar(1, close="102")
    hub.poll(key)
    assert hub.state("tab-one")["status"] == "conflict"
    assert hub.state("tab-one")["available_at"] == "2026-09-28T14:02:00Z"


def test_provider_errors_are_redacted_and_retried_without_losing_confirmed_data(ingress):
    hub, _, _, now, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    sources[0].failure = "secret-token-account-and-url"
    hub.poll(key)
    state = hub.state("tab-one")
    assert state["status"] == "failed"
    assert "secret-token" not in str(state)
    assert state["available_at"] == "2026-09-28T14:02:00Z"
    hub.poll(key)
    assert len(sources) == 2
    assert hub.state("tab-one")["status"] == "connected"
    now[0] += timedelta(seconds=16)
    assert hub.state("tab-one")["status"] == "stale"


def test_last_disconnect_closes_source_and_restart_does_not_reconnect_clients(ingress):
    hub, engine, feed, now, sources = ingress
    key = subscribe(ingress)
    subscribe(ingress, "tab-two")
    hub.poll(key)
    hub.unsubscribe("tab-one")
    assert not sources[0].closed
    hub.unsubscribe("tab-two")
    assert sources[0].closed
    restarted = LiveIngress(engine=lambda: engine, source=lambda _: pytest.fail("auto reconnect"),
                            clock=lambda: now[0], threaded=False)
    with pytest.raises(ValueError, match="reconnect explicitly"):
        restarted.state("tab-two")
    restarted.close()


def test_unverified_current_dates_fail_before_provider_contact(ingress):
    hub, _, feed, now, sources = ingress
    now[0] = datetime(2026, 12, 5, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="coverage"):
        hub.subscribe("tab-one", feed, now[0] - timedelta(minutes=1))
    assert sources == []


def test_status_reads_do_not_wait_for_slow_provider_io(ingress):
    hub, _, _, _, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    entered, release = Event(), Event()
    original = sources[0].poll_live

    def blocking(request, *, at):
        entered.set()
        assert release.wait(5)
        return original(request, at=at)

    sources[0].poll_live = blocking
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(hub.poll, key)
        try:
            assert entered.wait(2)
            state = pool.submit(hub.state, "tab-one").result(timeout=1)
            assert state["status"] == "connected"
        finally:
            release.set()
        pending.result(timeout=2)


def test_real_background_loop_is_shared_and_shutdown_closes_its_source(ingress):
    _, engine, feed, now, _ = ingress
    source = Source()
    polled = Event()
    original = source.poll_live

    def notify(request, *, at):
        result = original(request, at=at)
        polled.set()
        return result

    source.poll_live = notify
    created = []

    def factory(_feed):
        created.append(True)
        return source

    hub = LiveIngress(engine=lambda: engine, source=factory, clock=lambda: now[0])
    try:
        first = hub.subscribe("one", feed, START)
        hub.subscribe("two", feed, START)
        assert polled.wait(2)
        # Taking the polling lock waits for its current transaction to commit.
        with hub._feeds[first["feed_key"]].poll_lock:
            assert hub.state("two")["available_at"] == "2026-09-28T14:02:00Z"
        assert len(created) == 1
    finally:
        hub.close()
    assert source.closed


def test_catchup_work_and_client_ownership_are_bounded(ingress):
    hub, _, feed, now, _ = ingress
    now[0] = START + timedelta(hours=3)
    source = Source()
    source.bars = [bar(minute) for minute in range(180)]
    source.forming = bar(180, complete=False)
    hub._source = lambda _: source
    key = subscribe(ingress)
    hub.poll(key)
    assert source.calls[0].end - source.calls[0].start <= timedelta(hours=1)
    assert hub.state("tab-one")["status"] == "catching_up"
    assert hub.state("tab-one")["available_at"] == "2026-09-28T15:00:00Z"
    # Exact resubscription is idempotent; changing this client's ownership is explicit.
    assert hub.subscribe("tab-one", feed, START)["subscription_id"] == "tab-one"
    with pytest.raises(ValueError, match="disconnect"):
        hub.subscribe("tab-one", feed, START + timedelta(minutes=1))
    with pytest.raises(ValueError, match="previous day"):
        hub.subscribe("old", feed, START - timedelta(days=2))
    with pytest.raises(ValueError, match="1..500"):
        hub.bars("tab-one", START, START, 501)


@pytest.mark.parametrize("change", [
    {"forming": bar(1, complete=False)},
    {"forming": bar(2)},
    {"forming": replace(bar(2, complete=False), instrument_id="DAX")},
    {"observed_at": START},
])
def test_invalid_forming_or_availability_is_rejected(ingress, change):
    hub, _, _, now, sources = ingress
    key = subscribe(ingress)
    hub.poll(key)
    request = sources[0].calls[0]
    valid = LiveCandleUpdate(HistoricalDataPage(request, (bar(0), bar(1)),
                           HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live")),
                           now[0], bar(2, complete=False))
    with pytest.raises(ValueError):
        replace(valid, **change)


def test_subscription_api_requires_server_configuration_and_explicit_reconnect(
    ingress, monkeypatch,
):
    from market_analysis.api import live
    from market_analysis.api.app import app

    hub, _, _, _, _ = ingress
    monkeypatch.setattr(live, "live_ingress", hub)
    for name in ("OANDA_KEY", "OANDA_TOKEN", "OANDA_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as client:
        identity = str(uuid4())
        request = {"subscription_id": identity, "instrument_id": "US30",
                   "start": START.isoformat()}
        assert client.post("/live/subscriptions", json=request).status_code == 503
        monkeypatch.setenv("OANDA_TOKEN", "test-secret")
        monkeypatch.setenv("OANDA_ACCOUNT", "test")
        monkeypatch.setenv("OANDA_ENV", "live")
        monkeypatch.setenv("OANDA_REGION", "UK")
        response = client.post("/live/subscriptions", json=request)
        assert response.status_code == 201, response.text
        assert "test-secret" not in response.text
        hub.poll(response.json()["feed_key"])
        assert client.get(f"/live/subscriptions/{identity}").json()["status"] == "connected"
        view = client.get(f"/live/subscriptions/{identity}/bars", params={
            "start": START.isoformat(), "end": (START + timedelta(minutes=2)).isoformat(),
        })
        assert view.status_code == 200, view.text
        assert len(view.json()["bars"]) == 2
        assert client.delete(f"/live/subscriptions/{identity}").status_code == 200
        assert client.get(f"/live/subscriptions/{identity}").status_code == 404
