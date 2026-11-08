"""Captured close receipts drive the ordinary runtime and remain independently attributable."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, update
from test_history_inputs import START, _bar, _parent, history  # noqa: F401

from market_analysis.application.analysis_sessions import AnalysisRequest, AnalysisSessionManager
from market_analysis.application.history_inputs import publish_history_input
from market_analysis.application.history_ranges import HistoryFeed, history_coverage
from market_analysis.application.live_analysis import LiveAnalysisManager, LiveAnalysisRequest
from market_analysis.application.live_ingress import LiveIngress
from market_analysis.application.live_inputs import read_live_input
from market_analysis.application.live_retention import feed_key
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import DatasetLineage, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.dataset_validation import ValidationStatus
from market_analysis.domain.live_data import LiveCandleUpdate
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION
from market_analysis.persistence.live_analysis import live_analysis_closes, live_analysis_inputs
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)
from market_analysis.persistence.runs import load_run_snapshot


@pytest.fixture
def attached(history):  # noqa: F811
    engine, _, feed = history
    revision = _parent(history, 0, 120)
    now = [START + timedelta(minutes=125, seconds=20)]

    class Source:
        def __init__(self):
            self.last = 125
            self.forming = None

        def poll_live(self, request, *, at):
            return LiveCandleUpdate(
                HistoricalDataPage(
                    request,
                    tuple(
                        _bar(minute)
                        for minute in range(119, self.last)
                        if request.start <= _bar(minute).timestamp < request.end
                    ),
                    HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
                ),
                at,
                self.forming,
            )

        def close(self):
            pass

    hub = LiveIngress(
        engine=lambda: engine, source=lambda _: Source(), clock=lambda: now[0], threaded=False
    )
    manager = LiveAnalysisManager(hub)
    yield manager, hub, history, revision, now
    manager.close()
    hub.close()


def request(revision, *, policies=None, config=None):
    return LiveAnalysisRequest(
        subscription_id=uuid4(),
        bootstrap_revision_id=revision,
        streams=[{"config": config or demo_detection_config("US30"), "policies": policies or {}}],
    )


def test_live_closes_advance_hidden_sessions_and_pin_receipts_and_run_input(attached):
    manager, hub, (engine, _, feed), revision, _ = attached
    first = manager.connect(request(revision), feed)
    second = manager.connect(request(revision), feed)
    assert first["input_id"] != second["input_id"]
    hub.poll(feed_key(feed))  # No UI state/view calls trigger analytical work.
    one = manager.state(first["subscription_id"])
    two = manager.state(second["subscription_id"])
    assert one["available_at"] == two["available_at"] == "2026-09-28T16:05:00Z"
    assert one["status"] == "connected", one
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_inputs)) == 2
        assert connection.scalar(select(func.count()).select_from(live_analysis_closes)) == 10
        for item in one["initialization"]:
            snapshot = load_run_snapshot(connection, UUID(item["run_id"]))
            assert snapshot.dataset_revision_id == one["input_id"]
    view = manager.view(one["subscription_id"], Timeframe.M1)
    assert view["bars"][-1]["timestamp"] == "2026-09-28T16:04:00Z"
    before = [item["processed"] for item in one["initialization"]]
    hub.poll(feed_key(feed))
    assert [
        item["processed"]
        for item in manager.state(
            one["subscription_id"],
        )["initialization"]
    ] == before


def test_captured_live_runtime_matches_replay_from_the_same_initialization(attached):
    manager, hub, historical, revision, _ = attached
    _, _, feed = historical
    live = manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    full_revision = _parent(historical, 0, 125)
    replay = AnalysisSessionManager()
    replay_state = replay.launch(
        AnalysisRequest(
            dataset_revision_id=full_revision,
            instrument_id="US30",
            selected_start=START + timedelta(minutes=119),
            selected_end=START + timedelta(minutes=125),
            streams=[{"config": demo_detection_config("US30")}],
        )
    )
    for _ in range(6):
        replay.command(replay_state["run_id"], "step", Timeframe.M1)
    actual = manager._analysis.current(live["run_id"])
    expected = replay.current(replay_state["run_id"])
    for left, right in zip(actual.streams, expected.streams, strict=True):
        assert left.processed == right.processed

        def semantic(value, *, lineage=False):
            if isinstance(value, dict):
                return {
                    key: semantic(item, lineage=key == "lineage")
                    for key, item in value.items()
                    if not lineage or key not in {"run_id", "dataset_revision_id"}
                }
            if isinstance(value, list):
                return [semantic(item) for item in value]
            return value

        assert semantic([frame["components"] for frame in left.frames]) == semantic(
            [frame["components"] for frame in right.frames]
        )
        assert [
            (event["pattern_id"], event["to_state"], event["available_at"]) for event in left.events
        ] == [
            (event["pattern_id"], event["to_state"], event["available_at"])
            for event in right.events
        ]


def test_manual_policy_does_not_create_automatic_live_detector_runs(attached):
    manager, hub, (_, _, feed), revision, _ = attached
    config = demo_detection_config("US30")
    state = manager.connect(
        request(revision, policies={pattern.pattern_id: "manual" for pattern in config.patterns}),
        feed,
    )
    hub.poll(feed_key(feed))
    assert manager.state(state["subscription_id"])["initialization"] == []
    assert manager.view(state["subscription_id"], Timeframe.M1)["events"] == []


def test_independent_settings_disconnect_and_reconnect_preserve_original_lineage(attached):
    manager, hub, (engine, _, feed), revision, _ = attached
    selected = request(revision)
    first = manager.connect(selected, feed)
    assert manager.connect(selected, feed)["run_id"] == first["run_id"]
    hub.poll(feed_key(feed))
    manager.disconnect(first["subscription_id"])
    with pytest.raises(ValueError, match="disconnected"):
        manager.state(first["subscription_id"])
    again = manager.connect(selected.model_copy(update={"subscription_id": uuid4()}), feed)
    assert again["input_id"] != first["input_id"]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_inputs)) == 2


def test_seed_from_another_account_and_unverified_current_dates_are_rejected(attached):
    manager, _, (_, _, feed), revision, now = attached
    with pytest.raises(ValueError, match="exact provider feed"):
        manager.connect(
            request(revision),
            feed.model_copy(
                update={
                    "account_fingerprint": "a" * 64,
                }
            ),
        )
    now[0] += timedelta(days=60)
    with pytest.raises(ValueError, match="coverage"):
        manager.connect(request(revision), feed)


def test_live_input_can_be_verified_after_disconnect_and_corruption_is_rejected(attached):
    manager, hub, (engine, store, feed), revision, _ = attached
    state = manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    manager.disconnect(state["subscription_id"])
    with engine.connect() as connection:
        result = read_live_input(connection, store, state["input_id"])
    assert result["source_minutes"] == 5
    assert result["available_at"] == "2026-09-28T16:05:00Z"
    assert result["manifest"]["bootstrap_revision_id"] == revision
    with engine.begin() as connection:
        connection.execute(update(live_analysis_closes).values(sha256="f" * 64))
    with engine.connect() as connection, pytest.raises(ValueError, match="receipt differs"):
        read_live_input(connection, store, state["input_id"])


def test_higher_timeframe_forming_view_never_dispatches_detectors_or_changes_receipts(attached):
    manager, hub, (engine, _, feed), revision, _ = attached
    state = manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    source = hub._feeds[feed_key(feed)].source
    source.forming = replace(_bar(125), is_complete=False)
    before = manager.state(state["subscription_id"])
    hub.poll(feed_key(feed))
    view = manager.view(state["subscription_id"], Timeframe.M5)
    assert view["live"]["forming"]["timestamp"] == "2026-09-28T16:05:00Z"
    assert view["live"]["forming"]["is_complete"] is False
    assert manager.state(state["subscription_id"])["initialization"] == before["initialization"]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_closes)) == 5
    past = manager.view(state["subscription_id"], Timeframe.M1, START + timedelta(minutes=121))
    assert past["live"]["forming"] is None
    assert all(item["available_at"] <= "2026-09-28T16:01:00Z" for item in past["bars"])


def test_cancel_before_connect_prevents_late_connection_creation(attached):
    manager, _, (engine, _, feed), revision, _ = attached
    selected = request(revision)
    manager.disconnect(str(selected.subscription_id))
    with pytest.raises(ValueError, match="disconnected"):
        manager.connect(selected, feed)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_inputs)) == 0


def test_analytical_commit_failure_freezes_the_last_committed_view(attached, monkeypatch):
    manager, hub, (engine, _, feed), revision, _ = attached
    state = manager.connect(request(revision), feed)
    before = manager.state(state["subscription_id"])
    monkeypatch.setattr(manager._analysis, "engine", lambda: engine)
    failing = [False]

    def mark_transaction(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO live_analysis_closes"):
            failing[0] = True

    def fail_commit(connection):
        if failing[0]:
            raise RuntimeError("forced analytical transaction failure")

    event.listen(engine, "before_cursor_execute", mark_transaction)
    event.listen(engine, "commit", fail_commit)
    try:
        hub.poll(feed_key(feed))
    finally:
        event.remove(engine, "before_cursor_execute", mark_transaction)
        event.remove(engine, "commit", fail_commit)
    after = manager.state(state["subscription_id"])
    assert after["status"] == "failed"
    assert after["available_at"] == before["available_at"]
    assert after["initialization"] == before["initialization"]
    assert manager.view(state["subscription_id"], Timeframe.M1)["bars"][-1]["timestamp"] == (
        "2026-09-28T15:59:00Z"
    )
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_closes)) == 0


def test_manual_history_snapshot_is_frozen_and_does_not_advance_live_analysis(attached):
    manager, hub, (engine, store, feed), revision, _ = attached
    config = demo_detection_config("US30")
    live = manager.connect(
        request(revision, policies={pattern.pattern_id: "manual" for pattern in config.patterns}),
        feed,
    )
    hub.poll(feed_key(feed))
    before = manager.state(live["subscription_id"])
    start, end = START + timedelta(minutes=120), START + timedelta(minutes=125)
    source = manager.snapshot(live["subscription_id"], start, end)
    assert manager.snapshot(live["subscription_id"], start, end) == source
    calculated = AnalysisSessionManager()
    result = calculated.launch(
        AnalysisRequest(
            dataset_revision_id=source["dataset_revision_id"],
            instrument_id="US30",
            selected_start=start,
            selected_end=end,
            mode="history",
            streams=[{"config": config}],
        )
    )
    assert result["initialization"]
    assert manager.state(live["subscription_id"]) == before
    with engine.connect() as connection:
        assert (
            store.load_sequence(connection, source["dataset_revision_id"], "US30", Timeframe.M1)
            .bars[-1]
            .timestamp
            < end
        )


def test_first_live_connection_can_prepare_and_publish_history_without_a_selected_source(attached):
    manager, _, historical, _, _ = attached
    engine, store, feed = historical
    _parent(historical, 120, 125)
    # Supply enough real trading minutes across the weekend for the price buffer.
    closed = tuple(i for i in range(-4500, 0)
                   if not feed.calendar().derive(START + timedelta(minutes=i)).is_open)
    _parent(historical, -4500, 0, missing=closed)
    selected = LiveAnalysisRequest(
        subscription_id=uuid4(),
        streams=[
            {
                "config": demo_detection_config("US30"),
            }
        ],
    )
    plan = manager.prepare(selected, feed)["plan"]
    assert plan["intent"] == "replay"
    from market_analysis.application.history_prices import completed_buckets

    assert len(list(completed_buckets(
        feed.calendar(), Timeframe.M1, datetime.fromisoformat(plan["start"]),
        datetime.fromisoformat(plan["end"]),
    ))) == 1440
    with engine.begin() as connection:
        reviewed = {
            request_id: history_coverage(connection, store, request_id)["source_manifest_hash"]
            for request_id in plan["request_ids"]
        }
        source = publish_history_input(connection, store, reviewed)
    initialized = manager.connect(
        selected.model_copy(
            update={
                "bootstrap_revision_id": source["dataset_revision_id"],
            }
        ),
        feed,
    )
    assert initialized["available_at"] == "2026-09-28T16:05:00Z"
    assert all(item["processed"] >= item["required"] for item in initialized["initialization"])


def test_apply_reinitializes_with_a_new_input_and_keeps_other_live_tabs_processing(attached):
    manager, hub, (_, _, feed), revision, _ = attached
    first = manager.connect(request(revision), feed)
    other = manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    manager.disconnect(first["subscription_id"])
    changed = demo_detection_config("US30")
    components = tuple(
        item.model_copy(
            update={
                "parameters": tuple(
                    parameter.model_copy(update={"value": 3})
                    if parameter.name == "period"
                    else parameter
                    for parameter in item.parameters
                )
            }
        )
        if item.component_id == "ema"
        else item
        for item in changed.components
    )
    reinitialized = manager.connect(
        request(
            revision,
            config=changed.model_copy(
                update={
                    "components": components,
                }
            ),
        ),
        feed,
    )
    assert reinitialized["input_id"] != first["input_id"]
    assert manager.state(other["subscription_id"])["status"] == "connected"
    hub.poll(feed_key(feed))
    assert manager.state(reinitialized["subscription_id"])["available_at"] == "2026-09-28T16:05:00Z"


def test_us30_and_dax_live_sessions_use_independent_feed_and_detector_contexts(attached):
    manager, hub, (engine, store, us_feed), us_revision, now = attached
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="test-account",
        provider_symbol="DE30_EUR",
    )
    dax_feed = HistoryFeed(
        instrument_id="DAX",
        provider_symbol="DE30_EUR",
        calendar_version=calendar.version,
        account_fingerprint=us_feed.account_fingerprint,
    )
    dax_revision = str(uuid4())
    bars = tuple(replace(_bar(minute), instrument_id="DAX") for minute in range(120))
    end = START + timedelta(minutes=120)
    provenance = json.dumps(
        {
            "provider": "oanda",
            "environment": "live",
            "symbol": "DE30_EUR",
            "source_id": "oanda-midpoint",
            "price_component": "M",
            "smooth": False,
            "account_fingerprint": dax_feed.account_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("DE30_EUR"))
        store.publish(
            connection,
            DatasetRevision(
                dax_revision,
                dax_revision,
                "test-source",
                "oanda",
                now[0],
                now[0],
                dax_feed.normalization_version,
                calendar.version,
                DATASET_FORMAT_VERSION,
                (DatasetMembership("DAX", Timeframe.M1, START, end, 120),),
                f"datasets/{dax_revision}/manifest.json",
            ),
            DatasetLineage(
                dax_revision,
                "test-source",
                "DAX",
                Timeframe.M1,
                START,
                end,
                START,
                end,
                120,
                now[0],
                ValidationStatus.PASS,
                provenance,
                "f" * 64,
                canonical_bar_checksum_ordered(bars),
                BAR_CHECKSUM_VERSION,
                DATASET_FORMAT_VERSION,
            ),
            bars,
        )

    class Source:
        def __init__(self, feed):
            self.feed = feed

        def poll_live(self, request, *, at):
            return LiveCandleUpdate(
                HistoricalDataPage(
                    request,
                    tuple(
                        replace(_bar(minute), instrument_id=self.feed.instrument_id)
                        for minute in range(120, 125)
                        if request.start <= _bar(minute).timestamp < request.end
                    ),
                    HistoricalSource("oanda", self.feed.provider_symbol, "oanda-midpoint", "live"),
                ),
                at,
            )

        def close(self):
            pass

    hub._source = Source
    us = manager.connect(request(us_revision), us_feed)
    dax = manager.connect(request(dax_revision, config=demo_detection_config("DAX")), dax_feed)
    hub.poll(feed_key(us_feed))
    hub.poll(feed_key(dax_feed))
    assert manager.state(us["subscription_id"])["instrument_id"] == "US30"
    assert manager.state(dax["subscription_id"])["instrument_id"] == "DAX"
    assert manager.state(dax["subscription_id"])["available_at"] == "2026-09-28T16:05:00Z"
    assert feed_key(us_feed) != feed_key(dax_feed)


def test_live_input_migration_preserves_lineage_and_refuses_destructive_downgrade(
    attached,
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, text

    from alembic import command
    from alembic.config import Config

    _, old_hub, (_, store, feed), _, now = attached
    url = f"sqlite:///{tmp_path / 'migrated-live.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
    revision = _parent((engine, store, feed), 0, 120)
    hub = LiveIngress(
        engine=lambda: engine, source=old_hub._source, clock=lambda: now[0], threaded=False
    )
    manager = LiveAnalysisManager(hub)
    try:
        state = manager.connect(request(revision), feed)
        hub.poll(feed_key(feed))
        with pytest.raises(RuntimeError, match="detection-time context must be retained"):
            command.downgrade(config, "20261105_14")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT version_num FROM alembic_version",
                    )
                )
                == "20261106_19"
            )
            assert read_live_input(connection, store, state["input_id"])["source_minutes"] == 5
    finally:
        manager.close()
        hub.close()
        engine.dispose()


def test_live_attachment_rejects_conflicting_retained_bootstrap_atomically(attached):
    manager, hub, historical, revision, _ = attached
    engine, _, feed = historical
    manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    conflicting = _parent(historical, 0, 123, close="102")
    with engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(live_analysis_inputs))
    with pytest.raises(ValueError, match="conflicts with retained feed"):
        manager.connect(request(conflicting), feed)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_analysis_inputs)) == before


def test_live_reviews_resolve_durably_with_live_input_attribution(attached):
    from fastapi.testclient import TestClient

    from market_analysis.api.app import app

    manager, hub, (_, _, feed), revision, _ = attached
    state = manager.connect(request(revision), feed)
    hub.poll(feed_key(feed))
    run = state["initialization"][0]["run_id"]
    session = manager._analysis.current(state["run_id"])
    events = session.streams[0].events
    assert events
    manager.disconnect(state["subscription_id"])
    with TestClient(app) as client:
        assert client.get(f"/replay/{run}/annotations").status_code == 200
        assert client.get(f"/analysis/{state['run_id']}/annotations").status_code == 200
        response = client.post(f"/replay/{run}/annotations", json={
            "target_kind": "event", "event_id": events[-1]["event_id"], "label": "correct",
        })
        assert response.status_code == 201, response.text
        assert response.json()["dataset_revision_id"] == state["input_id"]
        response = client.post(f"/replay/{run}/annotations", json={
            "target_kind": "missed_pattern", "pattern_id": "RANGE_COMPRESSION_V1",
            "pattern_version": "1", "label": "missed_pattern",
            "interval_start": "2026-09-28T16:00:00Z", "interval_end": "2026-09-28T16:01:00Z",
        })
        assert response.status_code == 201, response.text
        assert response.json()["dataset_revision_id"] == state["input_id"]
        assert response.json()["analysis_lineage"]["analysis_run_id"] == run
        response = client.post(f"/replay/{run}/annotations", json={
            "target_kind": "missed_pattern", "pattern_id": "RANGE_COMPRESSION_V1",
            "pattern_version": "1", "label": "missed_pattern",
            "interval_start": "2026-09-28T16:05:00Z", "interval_end": "2026-09-28T16:06:00Z",
        })
        assert response.status_code == 422, response.text


def test_old_selected_bootstrap_prepares_a_new_feed_without_relabeling_retained_history(attached):
    from market_analysis.config.oanda_uk_calendars import LEGACY_CALENDAR_VERSION
    from market_analysis.persistence.market_data import load_dataset_revision

    manager, _, (engine, store, feed), _, _ = attached
    legacy_feed = feed.model_copy(update={"calendar_version": LEGACY_CALENDAR_VERSION})
    legacy_revision = _parent((engine, store, legacy_feed), 0, 120)
    prepared = manager.prepare(request(legacy_revision), feed)
    assert prepared["plan"]["request_ids"]
    with engine.connect() as connection:
        revision = load_dataset_revision(connection, legacy_revision)
        assert revision.calendar_version == LEGACY_CALENDAR_VERSION
        for request_id in prepared["plan"]["request_ids"]:
            coverage = history_coverage(connection, store, request_id)
            assert coverage["status"] == "pending"
