"""PostgreSQL serializes duplicate writers and concurrent hourly publication."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from alembic import command
from alembic.config import Config
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_retention import (
    LiveRetentionConflict,
    feed_key,
    package_live_hour,
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
from market_analysis.persistence.market_data import register_instrument


@pytest.mark.skipif(not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
                    reason="PostgreSQL integration URL is not configured")
def test_postgres_duplicate_writers_packagers_and_restart(tmp_path, monkeypatch):
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    other_engine = create_engine(url)
    start = datetime(2026, 9, 28, 14, tzinfo=UTC)
    at = start + timedelta(hours=2)
    calendar = build_oanda_uk_calendar(provider="oanda", region="UK", environment="live",
                                     account="test", provider_symbol="US30_USD")
    feed = HistoryFeed(instrument_id="US30", provider_symbol="US30_USD",
                       calendar_version=calendar.version,
                       account_fingerprint=sha256(uuid4().bytes).hexdigest())
    store = DatasetStore(tmp_path / "data")

    def bar(minute=0, close="101"):
        return Bar("US30", Timeframe.M1, start + timedelta(minutes=minute),
                   Decimal("100"), Decimal("103"), Decimal("99"), Decimal(close),
                   source_id="oanda-midpoint")

    try:
        with engine.begin() as connection:
            register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda index: retain_completed_minute(
                engine if index % 2 else other_engine, feed, bar(), at=at,
            ), range(4)))
        assert sum(not result["duplicate"] for result in receipts) == 1
        with pytest.raises(LiveRetentionConflict):
            retain_completed_minute(other_engine, feed, bar(close="102"), at=at)
        for minute in range(1, 60):
            retain_completed_minute(engine, feed, bar(minute), at=at)
        with ThreadPoolExecutor(max_workers=2) as pool:
            revisions = list(pool.map(lambda candidate: package_live_hour(
                candidate, store, feed, start, at=at,
            ), [engine, other_engine]))
        assert revisions[0] and revisions[0] == revisions[1]
        engine.dispose()
        with other_engine.connect() as connection:
            result = read_live_history(connection, feed_key(feed), start,
                                       start + timedelta(hours=1), at=at)
            assert result["status"] == "available"
            assert len(result["bars"]) == 60
            assert verify_live_retention(connection) >= 60
            assert len(store.load_sequence(
                connection, revisions[0], "US30", Timeframe.M1,
            ).bars) == 60
        retain_completed_minute(other_engine, feed, bar(60), at=at)
        container = os.getenv("STREAM_ANALYSIS_TEST_PG_CONTAINER")
        if container:
            # Opt-in for an isolated container/database owned by this test run.
            # Ordinary shared integration databases must not use this branch.
            dump = tmp_path / "postgres.dump"
            with dump.open("wb") as output:
                subprocess.run(["docker", "exec", container, "pg_dump", "-U", "postgres",
                                "-d", engine.url.database, "-Fc"], stdout=output, check=True)
            archive = create_archive(store.root, dump, tmp_path / "backup.zip")
            target = tmp_path / "restored"
            target.mkdir()
            restored_dump = extract_verified_archive(archive, target, verify_archive(archive))
            destination = f"restore_{uuid4().hex}"
            subprocess.run(["docker", "exec", container, "createdb", "-U", "postgres",
                            destination], check=True)
            restored_engine = create_engine(engine.url.set(database=destination))
            try:
                with restored_dump.open("rb") as source:
                    subprocess.run(["docker", "exec", "-i", container, "pg_restore", "-U",
                                    "postgres", "-d", destination, "--no-owner", "--no-acl",
                                    "--exit-on-error"], stdin=source, check=True)
                with restored_engine.connect() as connection:
                    assert verify_live_retention(connection) == 61
                    assert len(DatasetStore(target).load_sequence(
                        connection, revisions[0], "US30", Timeframe.M1,
                    ).bars) == 60
                    recent = read_live_history(connection, feed_key(feed),
                                               start + timedelta(hours=1),
                                               start + timedelta(minutes=61), at=at)
                    assert len(recent["bars"]) == 1
            finally:
                restored_engine.dispose()
                subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres",
                                destination], check=True)
    finally:
        engine.dispose()
        other_engine.dispose()
