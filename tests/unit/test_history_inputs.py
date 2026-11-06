"""Composite publication pins acquired minutes for ordinary replay execution."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from market_analysis.api.app import app
from market_analysis.application.historical_import import advance_import
from market_analysis.application.history_inputs import (
    HistoryInputConflict,
    publish_history_input,
    read_history_input,
)
from market_analysis.application.history_ranges import (
    HistoryFeed,
    history_coverage,
    request_history,
)
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import Bar, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.import_batches import ImportBatchStore
from market_analysis.persistence.import_jobs import (
    ImportRequest,
    claim_import_job,
    create_import_job,
)
from market_analysis.persistence.market_data import (
    dataset_revisions,
    load_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 28, 14, tzinfo=UTC)
NOW = datetime(2026, 11, 4, tzinfo=UTC)
ACCOUNT = "test-account"


@pytest.fixture
def history(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'inputs.sqlite'}")
    metadata.create_all(engine)
    store = DatasetStore(tmp_path / "data")
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account=ACCOUNT,
        provider_symbol="US30_USD",
    )
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
    feed = HistoryFeed(
        instrument_id="US30",
        provider_symbol="US30_USD",
        calendar_version=calendar.version,
        account_fingerprint=sha256(ACCOUNT.encode()).hexdigest(),
    )
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(store.root))
    from market_analysis.api import analysis
    from market_analysis.api import app as api
    from market_analysis.application.analysis_sessions import AnalysisSessionManager

    manager = AnalysisSessionManager()
    monkeypatch.setattr(analysis, "analysis_sessions", manager)
    monkeypatch.setattr(api, "analysis_sessions", manager)
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


def _parent(history, start, end, *, close="101", missing=()):
    engine, store, feed = history
    with engine.begin() as connection:
        job = create_import_job(
            connection,
            ImportRequest(
                str(uuid4()),
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
        )
        assert claim_import_job(connection, at=NOW) is not None

    class Source:
        def get_bars(self, selected):
            return HistoricalDataPage(
                selected,
                tuple(_bar(i, close) for i in range(start, end) if i not in missing),
                HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live"),
            )

    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account=ACCOUNT,
        provider_symbol="US30_USD",
        calendar_version=feed.calendar_version,
    )
    advance_import(
        engine, Source(), store, ImportBatchStore(store.root), job.job_id, calendar, at=NOW
    )
    return job.revision_id


def _reviewed(history, start=0, end=6, *, feed=None):
    engine, store, selected = history
    with engine.begin() as connection:
        request = request_history(
            connection,
            feed or selected,
            START + timedelta(minutes=start),
            START + timedelta(minutes=end),
            at=NOW,
        )
        coverage = history_coverage(connection, store, request.request_id)
    return {request.request_id: coverage["source_manifest_hash"]}


def _compose(history, requests):
    engine, store, _ = history
    with engine.begin() as connection:
        return publish_history_input(connection, store, requests)


def test_composition_publishes_ordered_exact_prices_with_parent_lineage_and_overlap_deduplication(
    history,
):
    engine, store, _ = history
    first, second = _parent(history, 0, 3), _parent(history, 3, 6, close="102")
    requests = {**_reviewed(history, 0, 4), **_reviewed(history, 2, 6)}
    result = _compose(history, requests)
    assert result["bar_count"] == 6
    with engine.connect() as connection:
        sequence = store.load_sequence(
            connection, result["dataset_revision_id"], "US30", Timeframe.M1
        )
        assert sequence.bars == tuple(_bar(i, "101" if i < 3 else "102") for i in range(6))
        assert (
            load_dataset_revision(connection, result["dataset_revision_id"]).provider
            == "history-composite"
        )
        assert {p["revision_id"] for p in result["source_manifest"]["parents"]} == {first, second}
        assert all(len(p["source_checksum"]) == 64 for p in result["source_manifest"]["parents"])
        assert sequence.lineage.source_checksum == result["source_manifest_hash"]
        assert (
            sha256(sequence.lineage.provider_request_json.encode()).hexdigest()
            == result["source_manifest_hash"]
        )
    assert _compose(history, dict(reversed(list(requests.items())))) == result
    # A composite is not fed back into acquisition coverage as a competing source.
    assert _reviewed(history, 0, 4) == dict(list(requests.items())[:1])


def test_restarted_offline_read_is_self_contained_even_without_parent_price_files(history):
    engine, store, _ = history
    parents = [_parent(history, 0, 3), _parent(history, 3, 6)]
    result = _compose(history, _reviewed(history))
    engine.dispose()
    for parent in parents:
        shutil.rmtree(store.root / "datasets" / parent)
    restarted = create_engine(str(engine.url))
    try:
        with restarted.connect() as connection:
            assert (
                read_history_input(
                    connection, DatasetStore(store.root), result["dataset_revision_id"]
                )
                == result
            )
            assert store.load_sequence(
                connection, result["dataset_revision_id"], "US30", Timeframe.M1
            ).bars == tuple(_bar(i) for i in range(6))
    finally:
        restarted.dispose()


def test_new_corrections_do_not_change_an_already_published_input(history):
    engine, store, _ = history
    _parent(history, 0, 6)
    requests = _reviewed(history)
    result = _compose(history, requests)
    _parent(history, 0, 6, close="102")
    with pytest.raises(HistoryInputConflict):
        _compose(history, requests)
    with engine.connect() as connection:
        assert read_history_input(connection, store, result["dataset_revision_id"]) == result
        assert store.load_sequence(
            connection, result["dataset_revision_id"], "US30", Timeframe.M1
        ).bars == tuple(_bar(i) for i in range(6))


@pytest.mark.parametrize("mode", ["pending", "gap", "stale", "missing_between"])
def test_composition_rejects_incomplete_stale_or_uncovered_open_minutes(history, mode):
    engine, store, _ = history
    if mode == "pending":
        requests = _reviewed(history)
    elif mode == "missing_between":
        _parent(history, 0, 2)
        _parent(history, 3, 6)
        requests = {**_reviewed(history, 0, 2), **_reviewed(history, 3, 6)}
    else:
        _parent(history, 0, 6, missing=(2,) if mode == "gap" else ())
        requests = _reviewed(history)
        if mode == "stale":
            requests = {key: "0" * 64 for key in requests}
    with pytest.raises(HistoryInputConflict):
        _compose(history, requests)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(dataset_revisions)
                .where(dataset_revisions.c.provider == "history-composite")
            )
            == 0
        )
    assert not list((store.root / "datasets").glob(".staging-*"))


def test_mixed_account_feeds_are_rejected_even_when_both_windows_are_available(history):
    from dataclasses import replace

    from market_analysis.persistence.market_data import load_dataset_lineage

    engine, store, feed = history
    parent = _parent(history, 0, 6)
    requests = _reviewed(history)
    other = feed.model_copy(update={"account_fingerprint": sha256(b"other").hexdigest()})
    # Publish an independently attributed, valid retained source under another account.
    with engine.begin() as connection:
        original = load_dataset_revision(connection, parent)
        lineage = load_dataset_lineage(connection, parent, "US30", Timeframe.M1)
        provenance = json.loads(lineage.provider_request_json)
        provenance["account_fingerprint"] = other.account_fingerprint
        revision_id = str(uuid4())
        changed = replace(
            original,
            dataset_revision_id=revision_id,
            manifest_ref=f"datasets/{revision_id}/manifest.json",
        )
        changed_lineage = replace(
            lineage,
            dataset_revision_id=revision_id,
            provider_request_json=json.dumps(provenance, sort_keys=True, separators=(",", ":")),
        )
        store.publish(connection, changed, changed_lineage, tuple(_bar(i) for i in range(6)))
    with pytest.raises(ValueError, match="same provider"):
        _compose(history, {**requests, **_reviewed(history, feed=other)})


def test_parent_corruption_cannot_be_published(history):
    _, store, _ = history
    parent = _parent(history, 0, 6)
    requests = _reviewed(history)
    with (store.root / "datasets" / parent / "bars.parquet").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(HistoryInputConflict, match="available"):
        _compose(history, requests)


def test_publication_rollback_leaves_no_registered_input_and_matching_retry_recovers_files(history):
    engine, store, _ = history
    _parent(history, 0, 6)
    requests = _reviewed(history)
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN")
        first = publish_history_input(connection, store, requests)
        connection.rollback()
        assert load_dataset_revision(connection, first["dataset_revision_id"]) is None
    assert (store.root / "datasets" / first["dataset_revision_id"]).is_dir()
    assert _compose(history, requests) == first


def test_existing_analysis_replays_across_parent_boundary_with_pinned_composite_input(history):
    engine, store, _ = history
    _parent(history, 0, 15)
    _parent(history, 15, 30, close="102")
    result = _compose(history, _reviewed(history, 0, 30))
    with TestClient(app) as client:
        response = client.post(
            "/analysis",
            json={
                "dataset_revision_id": result["dataset_revision_id"],
                "instrument_id": "US30",
                "selected_start": (START + timedelta(minutes=12)).isoformat(),
                "selected_end": (START + timedelta(minutes=25)).isoformat(),
                "streams": [{"config": demo_detection_config("US30").model_dump(mode="json")}],
            },
        )
        assert response.status_code == 201, response.text
        state = response.json()
        run_id = state["run_id"]
        for _ in range(4):
            step = client.post(f"/analysis/{run_id}/step")
            assert step.status_code == 200, step.text
            state = step.json()
        assert state["visible_bars"] == 4
        assert state["initialization"][0]["processed"] == 16
        view = client.get(
            f"/analysis/{run_id}/view",
            params={
                "start": START.isoformat(),
                "end": (START + timedelta(minutes=25)).isoformat(),
            },
        ).json()
        assert view["bars"][-1]["timestamp"] == _bar(15).timestamp.isoformat().replace(
            "+00:00", "Z"
        )
        assert Decimal(view["bars"][-1]["close"]) == Decimal("102")
        assert state["dataset_revision_id"] == result["dataset_revision_id"]
        assert view["events"]
        assert client.post(f"/analysis/{run_id}/stop").status_code == 200
        catalog = client.get("/replay/sources").json()["sources"]
        assert any(
            source["dataset_revision_id"] == result["dataset_revision_id"] for source in catalog
        )


def test_publication_and_metadata_api_require_reviewed_hashes_and_no_provider_credentials(
    history, monkeypatch
):
    _parent(history, 0, 6)
    requests = _reviewed(history)
    monkeypatch.delenv("OANDA_KEY", raising=False)
    monkeypatch.delenv("OANDA_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT", raising=False)
    with TestClient(app) as client:
        response = client.post("/history/inputs", json={"requests": requests})
        assert response.status_code == 201, response.text
        result = response.json()
        assert client.get(f"/history/inputs/{result['dataset_revision_id']}").json() == result
        assert client.post("/history/inputs", json={"requests": requests}).json() == result
        assert (
            client.post(
                "/history/inputs", json={"requests": {key: "0" * 64 for key in requests}}
            ).status_code
            == 409
        )
        assert client.post("/history/inputs", json={"requests": {}}).status_code == 422
        assert client.get("/history/inputs/unknown").status_code == 404


def test_scheduled_break_between_complete_requests_adds_no_synthetic_candles(history):
    engine, store, _ = history
    # 21:00–22:00 UTC is the verified US30 daily break on this date.
    _parent(history, 419, 420)
    _parent(history, 480, 481)
    requests = {**_reviewed(history, 419, 420), **_reviewed(history, 480, 481)}
    result = _compose(history, requests)
    assert result["bar_count"] == 2
    with engine.connect() as connection:
        assert store.load_sequence(
            connection,
            result["dataset_revision_id"],
            "US30",
            Timeframe.M1,
        ).bars == (_bar(419), _bar(480))


def test_composition_crops_requested_source_ranges_without_materializing_parent_sequences(
    history, monkeypatch
):
    engine, store, _ = history
    _parent(history, 0, 10)
    requests = _reviewed(history, 2, 8)

    def disallow_sequence(*_args):
        raise AssertionError("composition must stream selected ranges")

    with monkeypatch.context() as guard:
        guard.setattr(DatasetStore, "load_sequence", disallow_sequence)
        result = _compose(history, requests)
    assert result["bar_count"] == 6
    with engine.connect() as connection:
        assert store.load_sequence(
            connection,
            result["dataset_revision_id"],
            "US30",
            Timeframe.M1,
        ).bars == tuple(_bar(i) for i in range(2, 8))


def test_closed_only_windows_cannot_be_published_as_empty_replay_inputs(history):
    requests = _reviewed(history, 420, 480)
    with pytest.raises(ValueError, match="closed-only"):
        _compose(history, requests)


def test_explicit_gap_repair_produces_complete_validated_input(history):
    engine, store, _ = history
    _parent(history, 0, 6, missing=(2,))
    _parent(history, 2, 3)
    result = _compose(history, _reviewed(history))
    assert result["bar_count"] == 6
    with engine.connect() as connection:
        sequence = store.load_sequence(
            connection, result["dataset_revision_id"], "US30", Timeframe.M1
        )
        assert sequence.lineage.validation_status.value == "pass"
        assert sequence.bars == tuple(_bar(i) for i in range(6))


def test_parent_lineage_change_during_publication_aborts_without_registering_input(
    history, monkeypatch
):
    from market_analysis.persistence.market_data import (
        dataset_content_lineage,
        load_dataset_lineage,
    )

    engine, store, _ = history
    parent = _parent(history, 0, 6)
    requests = _reviewed(history)
    original = DatasetStore.publish

    def change_parent_before_publication(self, connection, revision, lineage, bars):
        connection.execute(
            dataset_content_lineage.update()
            .where(
                dataset_content_lineage.c.dataset_revision_id == parent,
            )
            .values(source_checksum="0" * 64)
        )
        return original(self, connection, revision, lineage, bars)

    with monkeypatch.context() as fault:
        fault.setattr(DatasetStore, "publish", change_parent_before_publication)
        with pytest.raises(HistoryInputConflict, match="lineage changed"):
            _compose(history, requests)
    with engine.connect() as connection:
        assert (
            load_dataset_lineage(connection, parent, "US30", Timeframe.M1).source_checksum
            != "0" * 64
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(dataset_revisions)
                .where(
                    dataset_revisions.c.provider == "history-composite",
                )
            )
            == 0
        )
    assert not list((store.root / "datasets").glob(".staging-*"))
