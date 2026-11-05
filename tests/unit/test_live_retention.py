"""Completed minutes survive restart/publication failure and remain immutable."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, update

from market_analysis.application.history_inputs import publish_history_input
from market_analysis.application.history_ranges import (
    HistoryFeed,
    history_coverage,
    request_history,
)
from market_analysis.application.live_retention import (
    LiveRetentionConflict,
    feed_key,
    package_live_hour,
    package_pending_live_hours,
    read_live_history,
    retain_completed_minute,
    verify_live_retention,
)
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.persistence.backup_archive import (
    create_archive,
    extract_verified_archive,
    verify_archive,
)
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.live_candles import live_candles, live_segments
from market_analysis.persistence.market_data import register_instrument
from market_analysis.persistence.runs import metadata

START = datetime(2026, 9, 28, 14, tzinfo=UTC)
NOW = START + timedelta(hours=4)


@pytest.fixture
def retained(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'live.sqlite'}")
    metadata.create_all(engine)
    store = DatasetStore(tmp_path / "data")
    calendar = build_oanda_uk_calendar(provider="oanda", region="UK", environment="live",
                                     account="test", provider_symbol="US30_USD")
    feed = HistoryFeed(instrument_id="US30", provider_symbol="US30_USD",
                       calendar_version=calendar.version,
                       account_fingerprint=sha256(b"test").hexdigest())
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(store.root))
    yield engine, store, feed
    engine.dispose()


def bar(minute=0):
    return Bar("US30", Timeframe.M1, START + timedelta(minutes=minute),
               Decimal("100"), Decimal("103"), Decimal("99"), Decimal("101"),
               source_id="oanda-midpoint")


def capture(retained, first=0, last=60, missing=()):
    engine, _, feed = retained
    for minute in range(first, last):
        if minute not in missing:
            retain_completed_minute(engine, feed, bar(minute), at=NOW)


def read(retained, start=START, end=START + timedelta(hours=1), limit=500):
    engine, _, feed = retained
    with engine.connect() as connection:
        return read_live_history(connection, feed_key(feed), start, end, at=NOW, limit=limit)


def test_ack_is_committed_before_publication_and_survives_engine_restart(retained):
    engine, store, feed = retained
    receipt = retain_completed_minute(engine, feed, bar(), at=NOW)
    assert receipt["duplicate"] is False
    assert not store.root.exists()
    engine.dispose()
    restarted = create_engine(str(engine.url))
    try:
        with restarted.connect() as connection:
            result = read_live_history(connection, feed_key(feed), START,
                                       START + timedelta(minutes=1), at=NOW)
        assert result["status"] == "available"
        assert result["bars"] == [dict(bar().to_canonical_dict())]
    finally:
        restarted.dispose()


def test_exact_redelivery_is_idempotent_and_conflicting_close_is_rejected(retained):
    engine, _, feed = retained
    retain_completed_minute(engine, feed, bar(), at=NOW)
    assert retain_completed_minute(engine, feed, bar(), at=NOW)["duplicate"] is True
    with pytest.raises(LiveRetentionConflict, match="conflicts"):
        retain_completed_minute(engine, feed, replace(bar(), close=Decimal("102")), at=NOW)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_candles)) == 1
    assert read(retained, end=START + timedelta(minutes=1))["bars"][0]["close"] == "101"


@pytest.mark.parametrize("candidate,at", [
    (replace(bar(), is_complete=False), NOW),
    (replace(bar(), timeframe=Timeframe.M5), NOW),
    (replace(bar(), timestamp=START + timedelta(seconds=1)), NOW),
    (replace(bar(), source_id="other-feed"), NOW),
    (replace(bar(), instrument_id="DAX"), NOW),
    (bar(), START + timedelta(seconds=59)),
    (bar(), NOW.replace(tzinfo=None)),
    (replace(bar(), timestamp=datetime(2026, 9, 27, 14, tzinfo=UTC)), NOW),
])
def test_forming_future_misaligned_wrong_feed_and_closed_minutes_rejected(retained, candidate, at):
    engine, _, feed = retained
    with pytest.raises(ValueError):
        retain_completed_minute(engine, feed, candidate, at=at)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_candles)) == 0


def test_feed_identity_isolates_accounts(retained):
    engine, _, feed = retained
    retain_completed_minute(engine, feed, bar(), at=NOW)
    other = feed.model_copy(update={"account_fingerprint": sha256(b"another").hexdigest()})
    retain_completed_minute(engine, other, replace(bar(), close=Decimal("102")), at=NOW)
    assert feed_key(other) != feed_key(feed)
    assert read(retained, end=START + timedelta(minutes=1))["bars"][0]["close"] == "101"


def test_packaging_is_hourly_idempotent_and_available_to_shared_composition(retained):
    engine, store, feed = retained
    capture(retained)
    before = read(retained)
    revision = package_live_hour(engine, store, feed, START, at=NOW)
    assert revision
    assert package_live_hour(engine, store, feed, START, at=NOW) == revision
    assert read(retained) == before
    with engine.begin() as connection:
        assert store.load_sequence(connection, revision, "US30", Timeframe.M1).bars == tuple(
            bar(minute) for minute in range(60)
        )
        request = request_history(connection, feed, START, START + timedelta(hours=1), at=NOW)
        coverage = history_coverage(connection, store, request.request_id)
        assert coverage["status"] == "available"
        composite = publish_history_input(connection, store, {
            request.request_id: coverage["source_manifest_hash"],
        })
        assert composite["dataset_revision_id"] != revision
    assert package_pending_live_hours(engine, store, at=NOW) == []


def test_gap_blocks_publication_until_repaired_and_surfaces_in_reads(retained):
    engine, store, feed = retained
    capture(retained, missing=(12,))
    assert read(retained)["missing_minutes"] == ["2026-09-28T14:12:00Z"]
    assert package_live_hour(engine, store, feed, START, at=NOW) is None
    retain_completed_minute(engine, feed, bar(12), at=NOW)
    assert read(retained)["status"] == "available"
    assert package_live_hour(engine, store, feed, START, at=NOW)


def test_partial_first_hour_is_explicit_and_cannot_be_extended_after_sealing(retained):
    engine, store, feed = retained
    capture(retained, first=30)
    revision = package_live_hour(engine, store, feed, START, at=NOW)
    assert revision
    with engine.connect() as connection:
        assert len(store.load_sequence(connection, revision, "US30", Timeframe.M1).bars) == 30
    with pytest.raises(LiveRetentionConflict, match="sealed"):
        retain_completed_minute(engine, feed, bar(29), at=NOW)
    assert retain_completed_minute(engine, feed, bar(30), at=NOW)["duplicate"] is True


def test_failure_after_file_publication_leaves_acknowledged_candles_and_reuses_files(
    retained, monkeypatch,
):
    engine, store, feed = retained
    capture(retained)
    before = read(retained)
    publish = DatasetStore.publish

    def fail_after_publish(self, *args, **kwargs):
        publish(self, *args, **kwargs)
        raise RuntimeError("forced crash before transaction commit")

    with monkeypatch.context() as patch:
        patch.setattr(DatasetStore, "publish", fail_after_publish)
        with pytest.raises(RuntimeError, match="forced crash"):
            package_live_hour(engine, store, feed, START, at=NOW)
    assert read(retained) == before
    with engine.connect() as connection:
        assert connection.scalar(select(live_segments.c.revision_id)) is None
    assert package_live_hour(engine, store, feed, START, at=NOW)
    assert len(list((store.root / "datasets").iterdir())) == 1


def test_budget_rotates_blocked_hours_instead_of_starving_complete_hours(retained):
    engine, store, _ = retained
    capture(retained, first=0, last=1)
    capture(retained, first=60, last=120)
    assert package_pending_live_hours(engine, store, at=NOW, limit=1) == []
    assert len(package_pending_live_hours(engine, store, at=NOW, limit=1)) == 1


def test_api_reads_recent_candles_before_packaging_and_enforces_limits(retained):
    from market_analysis.api.app import app

    _, _, feed = retained
    capture(retained, last=3)
    with TestClient(app) as client:
        endpoint = f"/history/live/{feed_key(feed)}/bars"
        params = {"start": START.isoformat(), "end": (START + timedelta(minutes=3)).isoformat()}
        result = client.get(endpoint, params={**params, "limit": 2})
        assert result.status_code == 200, result.text
        assert [item["timestamp"] for item in result.json()["bars"]] == [
            "2026-09-28T14:01:00Z", "2026-09-28T14:02:00Z",
        ]
        assert client.get(endpoint, params={**params, "limit": 501}).status_code == 422
        assert client.get(endpoint, params={**params, "end": START.isoformat()}).status_code == 422


def test_journal_corruption_fails_closed(retained):
    engine, store, feed = retained
    capture(retained)
    with engine.begin() as connection:
        connection.execute(update(live_candles).where(live_candles.c.timestamp == START).values(
            bar_json=json.dumps(dict(replace(bar(), close=Decimal("102")).to_canonical_dict())),
        ))
    with pytest.raises(LiveRetentionConflict, match="checksum"):
        read(retained)
    with pytest.raises(LiveRetentionConflict, match="checksum"):
        package_live_hour(engine, store, feed, START, at=NOW)


def test_no_current_or_unverified_hour_is_packaged(retained):
    engine, store, feed = retained
    capture(retained)
    with pytest.raises(ValueError, match="elapsed"):
        package_live_hour(engine, store, feed, START, at=START + timedelta(minutes=59))
    assert package_pending_live_hours(engine, store, at=START + timedelta(minutes=59)) == []


def test_commit_failure_never_returns_an_acknowledgment(retained):
    engine, _, feed = retained

    def fail_commit(connection):
        raise RuntimeError("forced commit failure")

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="forced commit"):
            retain_completed_minute(engine, feed, bar(), at=NOW)
    finally:
        event.remove(engine, "commit", fail_commit)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(live_candles)) == 0
    assert retain_completed_minute(engine, feed, bar(), at=NOW)["duplicate"] is False


def test_orphan_publication_can_be_retried_after_earlier_context_arrives(retained, monkeypatch):
    engine, store, feed = retained
    capture(retained, first=30)
    original = DatasetStore.publish

    def fail_after_publish(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("forced rollback")

    with monkeypatch.context() as patch:
        patch.setattr(DatasetStore, "publish", fail_after_publish)
        with pytest.raises(RuntimeError):
            package_live_hour(engine, store, feed, START, at=NOW)
    capture(retained, first=0, last=30)
    revision = package_live_hour(engine, store, feed, START, at=NOW)
    with engine.connect() as connection:
        assert len(store.load_sequence(connection, revision, "US30", Timeframe.M1).bars) == 60
    assert len(list((store.root / "datasets").iterdir())) == 2


def test_backup_archive_roundtrip_preserves_recent_and_packaged_candles(retained, tmp_path):
    engine, store, feed = retained
    capture(retained)
    revision = package_live_hour(engine, store, feed, START, at=NOW)
    capture(retained, first=60, last=62)
    # Exercise the archive with a portable SQLite dump in place of pg_dump;
    # PostgreSQL transaction/concurrency verification is a separate integration test.
    dump = tmp_path / "journal.sql"
    with sqlite3.connect(engine.url.database) as database:
        dump.write_text("\n".join(database.iterdump()))
    archive = create_archive(store.root, dump, tmp_path / "backup.zip")
    restored_root = tmp_path / "restored-data"
    restored_root.mkdir()
    restored_dump = extract_verified_archive(archive, restored_root, verify_archive(archive))
    restored_database = tmp_path / "restored.sqlite"
    with sqlite3.connect(restored_database) as database:
        database.executescript(restored_dump.read_text())
    restored_engine = create_engine(f"sqlite:///{restored_database}")
    try:
        with restored_engine.connect() as connection:
            assert verify_live_retention(connection) == 62
            assert len(DatasetStore(restored_root).load_sequence(
                connection, revision, "US30", Timeframe.M1,
            ).bars) == 60
            recent = read_live_history(connection, feed_key(feed), START + timedelta(hours=1),
                                       START + timedelta(minutes=62), at=NOW)
            assert len(recent["bars"]) == 2
    finally:
        restored_engine.dispose()


def test_restore_verification_rejects_corrupt_recent_content(retained):
    engine, _, _ = retained
    capture(retained, last=1)
    with engine.begin() as connection:
        connection.execute(update(live_candles).values(sha256="f" * 64))
    with engine.connect() as connection, pytest.raises(LiveRetentionConflict, match="checksum"):
        verify_live_retention(connection)


def test_live_migration_roundtrip_preserves_published_revision(retained, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    _, store, feed = retained
    url = f"sqlite:///{tmp_path / 'migrated.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261104_13")
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
        capture((engine, store, feed))
        revision = package_live_hour(engine, store, feed, START, at=NOW)
        command.downgrade(config, "20261104_13")
        with engine.connect() as connection:
            assert len(store.load_sequence(connection, revision, "US30", Timeframe.M1).bars) == 60
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert verify_live_retention(connection) == 0
            assert len(store.load_sequence(connection, revision, "US30", Timeframe.M1).bars) == 60
    finally:
        engine.dispose()


def test_worker_packages_durable_minutes_without_provider_credentials(retained, monkeypatch):
    from market_analysis.application import worker

    engine, store, _ = retained
    capture(retained)
    completed = []
    monkeypatch.setattr(worker, "_clock", lambda: NOW)
    monkeypatch.setattr(worker, "_import_credentials", lambda: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _: completed.append(True))
    worker._run_import_worker(store.root, lambda: bool(completed))
    with engine.connect() as connection:
        revision = connection.scalar(select(live_segments.c.revision_id))
        assert revision is not None
        assert len(store.load_sequence(connection, revision, "US30", Timeframe.M1).bars) == 60
