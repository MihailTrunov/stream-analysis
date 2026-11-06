"""Shared range intent reuses immutable imports without widening worker concurrency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from market_analysis.api.app import app
from market_analysis.application.historical_import import advance_import
from market_analysis.application.history_ranges import (
    HistoryFeed,
    history_coverage,
    history_prices,
    request_history,
    schedule_history_import,
)
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import Bar, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.history_ranges import history_range_requests
from market_analysis.persistence.import_batches import ImportBatchStore
from market_analysis.persistence.import_jobs import (
    ImportRequest,
    ImportStatus,
    claim_import_job,
    create_import_job,
    fail_import_job,
    import_jobs,
    interrupt_running_imports,
)
from market_analysis.persistence.market_data import register_instrument
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 28, 14, tzinfo=UTC)
NOW = datetime(2026, 11, 4, tzinfo=UTC)
ACCOUNT = "test-account"


@pytest.fixture
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ranges.sqlite'}")
    metadata.create_all(engine)
    store = DatasetStore(tmp_path)
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account=ACCOUNT,
        provider_symbol="US30_USD",
    )
    feed = HistoryFeed(
        instrument_id="US30",
        provider_symbol="US30_USD",
        calendar_version=calendar.version,
        account_fingerprint=sha256(ACCOUNT.encode()).hexdigest(),
    )
    yield engine, store, feed
    engine.dispose()


def _bar(minute, close="101"):
    return Bar(
        "US30",
        Timeframe.M1,
        START + timedelta(minutes=minute),
        Decimal("100"),
        Decimal("103"),
        Decimal("99"),
        Decimal(close),
        source_id="oanda-midpoint",
    )


def _request(engine, feed, start=0, end=6, at=NOW):
    with engine.begin() as connection:
        return request_history(
            connection,
            feed,
            START + timedelta(minutes=start),
            START + timedelta(minutes=end),
            at=at,
        )


def _schedule(engine, store):
    with engine.begin() as connection:
        return schedule_history_import(
            connection, store, account=ACCOUNT, environment="live", at=NOW
        )


def _coverage(engine, store, request):
    with engine.connect() as connection:
        return history_coverage(connection, store, request.request_id)


def _import(engine, feed, start, end, *, fresh=False):
    with engine.begin() as connection:
        return create_import_job(
            connection,
            ImportRequest(
                "manual-study",
                "US30",
                Timeframe.M1,
                START + timedelta(minutes=start),
                START + timedelta(minutes=end),
                "oanda",
                "live",
                "US30_USD",
                feed.calendar_version,
                feed.account_fingerprint,
            ),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=NOW,
            fresh_attempt=fresh,
        )


def _finish(engine, store, job, bars):
    class Source:
        def get_bars(self, request):
            return HistoricalDataPage(
                request,
                tuple(bars),
                HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
            )

    with engine.begin() as connection:
        claimed = claim_import_job(connection, at=NOW)
        assert claimed is not None and claimed.job_id == job.job_id
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account=ACCOUNT,
        provider_symbol="US30_USD",
    )
    completed = advance_import(
        engine, Source(), store, ImportBatchStore(store.root), job.job_id, calendar, at=NOW
    )
    assert completed.status is ImportStatus.COMPLETED
    return completed


def test_overlapping_tabs_reuse_local_and_pending_ranges_and_fetch_only_missing(setup):
    engine, store, feed = setup
    retained = _import(engine, feed, 2, 4)
    _finish(engine, store, retained, [_bar(2), _bar(3)])
    first = _request(engine, feed)
    assert _request(engine, feed).request_id == first.request_id
    coverage = _coverage(engine, store, first)
    assert [s["status"] for s in coverage["spans"]] == ["missing", "available", "missing"]
    before = store.load_sequence
    with engine.connect() as connection:
        pinned = before(connection, retained.revision_id, "US30", Timeframe.M1)
    first_job = _schedule(engine, store)
    assert (first_job.request.start, first_job.request.end) == (START, START + timedelta(minutes=2))
    second = _request(engine, feed, -1, 5, at=NOW + timedelta(seconds=1))
    assert _schedule(engine, store) is None
    pending = [s for s in _coverage(engine, store, second)["spans"] if s["status"] == "pending"]
    assert pending[0]["job_id"] == first_job.job_id
    _finish(engine, store, first_job, [_bar(0), _bar(1)])
    next_job = _schedule(engine, store)
    assert (next_job.request.start, next_job.request.end) == (
        START + timedelta(minutes=4),
        START + timedelta(minutes=6),
    )
    _finish(engine, store, next_job, [_bar(4), _bar(5)])
    last_job = _schedule(engine, store)
    assert (last_job.request.start, last_job.request.end) == (START - timedelta(minutes=1), START)
    _finish(engine, store, last_job, [_bar(-1)])
    assert _schedule(engine, store) is None
    with engine.connect() as connection:
        result = history_prices(connection, store, first.request_id, limit=3)
        assert result["status"] == "available"
        assert [b["timestamp"] for b in result["bars"]] == [
            _bar(i).timestamp.isoformat().replace("+00:00", "Z") for i in (3, 4, 5)
        ]
        assert len(result["source_manifest"]["sources"]) == 3
        assert store.load_sequence(connection, retained.revision_id, "US30", Timeframe.M1) == pinned
        assert connection.scalar(select(func.count()).select_from(import_jobs)) == 4


def test_waiting_requests_and_published_coverage_survive_restart(setup):
    engine, store, feed = setup
    request = _request(engine, feed, 0, 2)
    job = _schedule(engine, store)
    url = str(engine.url)
    engine.dispose()
    restarted = create_engine(url)
    try:
        assert _schedule(restarted, DatasetStore(store.root)) is None  # Reuses queued job.
        assert _coverage(restarted, store, request)["spans"][0]["job_id"] == job.job_id
        _finish(restarted, store, job, [_bar(0), _bar(1)])
        assert _coverage(restarted, store, request)["status"] == "available"
        assert _request(restarted, feed, 0, 2).request_id == request.request_id
        assert _schedule(restarted, store) is None
    finally:
        restarted.dispose()


@pytest.mark.parametrize(
    "bars,status", [([], "unavailable"), ([_bar(0), _bar(2)], "unexpected_gap")]
)
def test_empty_and_gapped_provider_results_are_distinct_and_do_not_retry_forever(
    setup, bars, status
):
    engine, store, feed = setup
    request = _request(engine, feed, 0, 3)
    _finish(engine, store, _schedule(engine, store), bars)
    assert _coverage(engine, store, request)["status"] == status
    assert _schedule(engine, store) is None
    with engine.connect() as connection:
        result = history_prices(connection, store, request.request_id)
        assert len(result["bars"]) == len(bars)


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_or_interrupted_import_is_explicit_and_needs_explicit_recovery(setup, interrupted):
    engine, store, feed = setup
    request = _request(engine, feed, 0, 3)
    job = _schedule(engine, store)
    with engine.begin() as connection:
        claim_import_job(connection, at=NOW)
        if interrupted:
            interrupt_running_imports(connection, at=NOW)
        else:
            fail_import_job(connection, job.job_id, at=NOW, reason="provider unavailable")
    result = _coverage(engine, store, request)
    assert result["status"] == "failed"
    assert result["spans"][0]["job_id"] == job.job_id
    assert _schedule(engine, store) is None


def test_account_identity_and_normalization_prevent_silent_feed_reuse(setup):
    engine, store, feed = setup
    job = _import(engine, feed, 0, 3)
    _finish(engine, store, job, [_bar(0), _bar(1), _bar(2)])
    other = feed.model_copy(update={"account_fingerprint": sha256(b"other-account").hexdigest()})
    request = _request(engine, other, 0, 3)
    assert _coverage(engine, store, request)["spans"][0]["status"] == "missing"
    assert _schedule(engine, store) is None
    assert _coverage(engine, store, request)["status"] == "failed"
    with pytest.raises(ValueError, match="provenance"):
        _request(engine, feed.model_copy(update={"normalization_version": "unknown"}))


@pytest.mark.parametrize("close,expected", [("101", "available"), ("102", "conflict")])
def test_overlapping_retained_revisions_are_equivalent_or_explicitly_conflicting(
    setup, close, expected
):
    engine, store, feed = setup
    _finish(engine, store, _import(engine, feed, 0, 2), [_bar(0), _bar(1)])
    _finish(engine, store, _import(engine, feed, 0, 2, fresh=True), [_bar(0), _bar(1, close)])
    request = _request(engine, feed, 0, 2)
    assert _coverage(engine, store, request)["status"] == expected
    with engine.connect() as connection:
        if expected == "conflict":
            with pytest.raises(ValueError, match="conflicting"):
                history_prices(connection, store, request.request_id)
        else:
            assert len(history_prices(connection, store, request.request_id)["bars"]) == 2


def test_scheduled_closed_minutes_do_not_require_a_provider_job(setup):
    engine, store, feed = setup
    start = datetime(2026, 9, 27, 12, tzinfo=UTC)
    with engine.begin() as connection:
        request = request_history(connection, feed, start, start + timedelta(hours=1), at=NOW)
    assert _coverage(engine, store, request)["status"] == "available"
    assert _coverage(engine, store, request)["spans"][0]["status"] == "closed"
    assert _schedule(engine, store) is None


@pytest.mark.parametrize(
    "start,end",
    [
        (START.replace(tzinfo=None), START + timedelta(minutes=1)),
        (START, START + timedelta(days=32)),
        (START, START + timedelta(seconds=90)),
        (NOW, NOW + timedelta(minutes=1)),
        (datetime(2026, 12, 1, tzinfo=UTC), datetime(2026, 12, 2, tzinfo=UTC)),
    ],
)
def test_invalid_or_unverified_ranges_are_rejected_before_persisting(setup, start, end):
    engine, _, feed = setup
    with engine.begin() as connection:
        with pytest.raises(ValueError):
            request_history(connection, feed, start, end, at=NOW)
        assert connection.scalar(select(func.count()).select_from(history_range_requests)) == 0


def test_history_api_retains_private_context_only_as_a_fingerprint(setup, monkeypatch):
    engine, store, _ = setup
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(store.root))
    monkeypatch.setenv("OANDA_KEY", "private-token")
    monkeypatch.setenv("OANDA_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("OANDA_REGION", "UK")
    monkeypatch.setenv("OANDA_ENV", "live")
    payload = {
        "instrument_id": "US30",
        "start": START.isoformat(),
        "end": (START + timedelta(minutes=2)).isoformat(),
    }
    with TestClient(app) as client:
        response = client.post("/history/requests", json=payload)
        assert response.status_code == 202, response.text
        request_id = response.json()["request_id"]
        assert client.post("/history/requests", json=payload).json()["request_id"] == request_id
        assert ACCOUNT not in response.text and "private-token" not in response.text
        assert client.get(f"/history/requests/{request_id}").json()["status"] == "pending"
        _finish(engine, store, _schedule(engine, store), [_bar(0), _bar(1)])
        monkeypatch.delenv("OANDA_KEY")
        result = client.get(f"/history/requests/{request_id}/bars?limit=1")
        assert result.status_code == 200
        assert result.json()["status"] == "available"
        assert len(result.json()["bars"]) == 1
        assert client.get(f"/history/requests/{request_id}/bars?limit=501").status_code == 422
        assert client.get("/history/requests/absent").status_code == 404
        assert client.post("/history/requests", json=payload).json()["request_id"] == request_id
        monkeypatch.delenv("OANDA_ACCOUNT")
        assert client.post("/history/requests", json=payload).status_code == 503


def test_simultaneous_equivalent_windows_share_one_durable_request(setup):
    from concurrent.futures import ThreadPoolExecutor

    engine, _, feed = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: _request(engine, feed).request_id, range(8)))
    assert len(set(ids)) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(history_range_requests)) == 1


def test_manual_import_holds_the_single_slot_without_losing_waiting_range_intent(setup):
    engine, store, feed = setup
    manual = _import(engine, feed, 10, 11)
    request = _request(engine, feed, 0, 2)
    assert _schedule(engine, store) is None
    assert _coverage(engine, store, request)["status"] == "pending"
    _finish(engine, store, manual, [_bar(10)])
    queued = _schedule(engine, store)
    assert queued.request.start == START
    assert queued.request.end == START + timedelta(minutes=2)


def test_source_corruption_is_failed_coverage_and_never_a_no_data_result(setup):
    engine, store, feed = setup
    request = _request(engine, feed, 0, 2)
    job = _schedule(engine, store)
    _finish(engine, store, job, [_bar(0), _bar(1)])
    assert _coverage(engine, store, request)["status"] == "available"
    path = store.root / "datasets" / job.revision_id / "bars.parquet"
    with path.open("ab") as stream:
        stream.write(b"corrupted")
    assert _coverage(engine, store, request)["status"] == "failed"
    assert _schedule(engine, store) is None
    with engine.connect() as connection:
        assert history_prices(connection, store, request.request_id)["bars"] == []


def test_import_worker_schedules_durable_intent_and_publishes_through_fake_provider(
    setup,
    monkeypatch,
):
    from market_analysis.application import worker

    engine, store, feed = setup
    request = _request(engine, feed, 0, 2)
    calls = []

    class FakeOanda:
        def __init__(self, account, token, environment):
            assert (account, token, environment) == (ACCOUNT, "fake-token", "live")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def verify_account_instrument(self, symbol):
            assert symbol == "US30_USD"

        def get_bars(self, selected):
            calls.append(selected)
            return HistoricalDataPage(
                selected,
                (_bar(0), _bar(1)),
                HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
            )

    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setattr(worker, "_import_credentials", lambda: ("fake-token", ACCOUNT, "live"))
    monkeypatch.setattr(worker, "OandaHistoricalDataSource", FakeOanda)
    worker._run_import_worker(store.root, lambda: bool(calls))
    assert len(calls) == 1
    assert _coverage(engine, store, request)["status"] == "available"


def test_range_migration_roundtrip_preserves_existing_published_inputs(setup, monkeypatch):
    from alembic import command
    from alembic.config import Config

    engine, store, feed = setup
    # Use a fresh migrated database rather than stamping tables created by the fixture.
    url = f"sqlite:///{store.root / 'migrated.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261104_12")
    migrated = create_engine(url)
    try:
        with migrated.begin() as connection:
            register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
        command.upgrade(config, "head")
        request = _request(migrated, feed, 0, 2)
        job = _schedule(migrated, store)
        _finish(migrated, store, job, [_bar(0), _bar(1)])
        assert _coverage(migrated, store, request)["status"] == "available"
        command.downgrade(config, "20261104_12")
        with migrated.connect() as connection:
            assert store.load_sequence(connection, job.revision_id, "US30", Timeframe.M1).bars == (
                _bar(0),
                _bar(1),
            )
        command.upgrade(config, "head")
        reused = _request(migrated, feed, 0, 2)
        assert _coverage(migrated, store, reused)["status"] == "available"
        assert _schedule(migrated, store) is None
    finally:
        migrated.dispose()


def test_retired_calendar_request_does_not_block_other_waiting_windows(setup):
    engine, store, feed = setup
    retired = _request(engine, feed, 0, 2)
    current = _request(engine, feed, 3, 4, at=NOW + timedelta(seconds=1))
    obsolete = feed.model_copy(update={"calendar_version": "retired-calendar"})
    with engine.begin() as connection:
        connection.execute(
            history_range_requests.update()
            .where(
                history_range_requests.c.request_id == retired.request_id,
            )
            .values(feed_json=obsolete.canonical_json)
        )
    job = _schedule(engine, store)
    assert job.request.start == current.start
    with engine.connect() as connection:
        failed = (
            connection.execute(
                select(history_range_requests).where(
                    history_range_requests.c.request_id == retired.request_id,
                )
            )
            .mappings()
            .one()
        )
        assert failed["settled"] is True
        assert "calendar" in failed["failure_reason"]


def test_unexpected_closed_session_prices_are_reported_and_not_returned(setup):
    engine, store, feed = setup
    # Sunday before the verified provider opening.
    minute = -1560
    job = _import(engine, feed, minute, minute + 1)
    _finish(engine, store, job, [_bar(minute)])
    request = _request(engine, feed, minute, minute + 1)
    assert _coverage(engine, store, request)["status"] == "unexpected_gap"
    with engine.connect() as connection:
        assert history_prices(connection, store, request.request_id)["bars"] == []


def test_old_normalization_import_does_not_block_fetching_the_current_feed(setup, monkeypatch):
    from market_analysis.application import historical_import

    engine, store, feed = setup
    original = _import(engine, feed, 0, 2)
    with monkeypatch.context() as previous:
        previous.setattr(historical_import, "NORMALIZATION_VERSION", "previous-normalizer")
        _finish(engine, store, original, [_bar(0), _bar(1)])
    request = _request(engine, feed, 0, 2)
    assert _coverage(engine, store, request)["spans"][0]["status"] == "missing"
    replacement = _schedule(engine, store)
    assert replacement.revision_id != original.revision_id
    _finish(engine, store, replacement, [_bar(0), _bar(1)])
    result = _coverage(engine, store, request)
    assert result["status"] == "available"
    assert result["spans"][0]["revision_id"] == replacement.revision_id
    with engine.connect() as connection:
        assert store.load_sequence(connection, original.revision_id, "US30", Timeframe.M1).bars == (
            _bar(0),
            _bar(1),
        )


def test_navigation_plans_verified_context_and_fixed_replay_end_without_calling_provider(setup):
    from market_analysis.application.analysis_sessions import StreamSettings
    from market_analysis.application.history_navigation import plan_history_navigation
    from market_analysis.application.history_prices import completed_buckets
    from market_analysis.application.replay_catalog import demo_detection_config

    engine, store, feed = setup
    original = _import(engine, feed, 0, 30)
    _finish(engine, store, original, [_bar(i) for i in range(30)])
    target, end = START + timedelta(minutes=15), START + timedelta(minutes=30)
    settings = StreamSettings(
        config=demo_detection_config("US30").model_copy(update={"timeframe": Timeframe.D1})
    )
    with engine.begin() as connection:
        result = plan_history_navigation(
            connection,
            feed,
            original.revision_id,
            target,
            Timeframe.M5,
            (settings,),
            "replay",
            at=NOW,
            end=end,
        )
        first = datetime.fromisoformat(result["start"])
        assert len(list(completed_buckets(feed.calendar(), Timeframe.D1, first, target))) >= 11
        assert result["end"] == end.isoformat().replace("+00:00", "Z")
        assert 1 <= len(result["request_ids"]) <= 32
        assert connection.scalar(select(func.count()).select_from(import_jobs)) == 1
        for request_id in result["request_ids"]:
            row = (
                connection.execute(
                    select(history_range_requests).where(
                        history_range_requests.c.request_id == request_id,
                    )
                )
                .mappings()
                .one()
            )
            assert row["range_end"] - row["range_start"] <= timedelta(days=31)


@pytest.mark.parametrize("mode", ["account", "calendar", "initialization", "budget"])
def test_navigation_rejects_invalid_feed_dates_or_initialization_before_queueing(setup, mode):
    from market_analysis.application.analysis_sessions import StreamSettings
    from market_analysis.application.history_navigation import plan_history_navigation
    from market_analysis.application.replay_catalog import demo_detection_config

    engine, store, feed = setup
    original = _import(engine, feed, 0, 2)
    _finish(engine, store, original, [_bar(0), _bar(1)])
    selected = (
        feed.model_copy(update={"account_fingerprint": sha256(b"different").hexdigest()})
        if mode == "account"
        else feed
    )
    target = {
        "calendar": datetime(2026, 12, 1, 14, tzinfo=UTC),
        "initialization": datetime(2023, 9, 27, 22, tzinfo=UTC),
        "budget": datetime(2023, 10, 2, 14, tzinfo=UTC),
    }.get(mode, START)
    with engine.begin() as connection:
        with pytest.raises(ValueError):
            plan_history_navigation(
                connection,
                selected,
                original.revision_id,
                target,
                Timeframe.M1,
                (StreamSettings(config=demo_detection_config("US30")),),
                "replay",
                at=NOW,
            )
        assert connection.scalar(select(func.count()).select_from(history_range_requests)) == 0


def test_navigation_api_uses_account_context_without_requiring_provider_token(setup, monkeypatch):
    from market_analysis.application.replay_catalog import demo_detection_config

    engine, store, feed = setup
    original = _import(engine, feed, 0, 2)
    _finish(engine, store, original, [_bar(0), _bar(1)])
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(store.root))
    monkeypatch.setenv("OANDA_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("OANDA_ENV", "live")
    monkeypatch.setenv("OANDA_REGION", "UK")
    monkeypatch.delenv("OANDA_KEY", raising=False)
    monkeypatch.delenv("OANDA_TOKEN", raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/history/navigation",
            json={
                "source_revision_id": original.revision_id,
                "instrument_id": "US30",
                "target": START.isoformat(),
                "timeframe": "1m",
                "intent": "move",
                "streams": [{"config": demo_detection_config("US30").model_dump(mode="json")}],
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["request_ids"]
        assert ACCOUNT not in response.text


def test_navigation_acquires_initialization_publishes_and_replays_the_prepared_input(
    setup, monkeypatch
):
    from market_analysis.application.analysis_sessions import (
        AnalysisRequest,
        AnalysisSessionManager,
        StreamSettings,
    )
    from market_analysis.application.history_inputs import publish_history_input
    from market_analysis.application.history_navigation import plan_history_navigation
    from market_analysis.application.replay_catalog import demo_detection_config

    engine, store, feed = setup
    original = _import(engine, feed, 0, 5)
    _finish(engine, store, original, [_bar(i) for i in range(5)])
    settings = StreamSettings(
        config=demo_detection_config("US30").model_copy(update={"timeframe": Timeframe.M5})
    )
    with engine.begin() as connection:
        plan = plan_history_navigation(
            connection,
            feed,
            original.revision_id,
            START,
            Timeframe.M5,
            (settings,),
            "replay",
            at=NOW,
            end=START + timedelta(minutes=5),
        )
    job = _schedule(engine, store)
    first = int((job.request.start - START).total_seconds() / 60)
    last = int((job.request.end - START).total_seconds() / 60)
    assert first < -55 and last == 0
    _finish(engine, store, job, [_bar(i) for i in range(first, last)])
    with engine.begin() as connection:
        reviewed = {}
        for request_id in plan["request_ids"]:
            coverage = history_coverage(connection, store, request_id)
            assert coverage["status"] == "available"
            reviewed[request_id] = coverage["source_manifest_hash"]
        source = publish_history_input(connection, store, reviewed)
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(store.root))
    manager = AnalysisSessionManager()
    state = manager.launch(
        AnalysisRequest(
            dataset_revision_id=source["dataset_revision_id"],
            instrument_id="US30",
            chart_timeframe=Timeframe.M5,
            selected_start=START,
            selected_end=START + timedelta(minutes=5),
            streams=(settings,),
        )
    )
    assert state["status"] == "paused" and state["visible_bars"] == 0
    stepped = manager.command(state["run_id"], "step", Timeframe.M5)
    assert stepped["visible_bars"] == 5
    assert stepped["status"] == "completed"
    assert stepped["initialization"][0]["processed"] >= 12
