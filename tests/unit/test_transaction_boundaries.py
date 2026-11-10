"""Engine write boundaries must roll back nested savepoint writes on SQLite.

Regressions for the 2026-11-08 transaction-boundaries review P2: a plain
``engine.begin()`` transaction whose first write flows through a repository
``begin_nested()`` savepoint could leak that write on SQLite's legacy
transaction control, because the savepoint became the real transaction and its
release committed early. Production boundaries now use ``atomic_transaction``;
each test reproduces a first-write-then-failure composition per affected family
and asserts zero partial state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select

from market_analysis.application import live_retention
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_retention import retain_completed_minute
from market_analysis.application.replay_catalog import (
    PATTERN_DEFINITIONS,
    demo_detection_config,
)
from market_analysis.application.replay_run import create_replay_run
from market_analysis.code_version import CodeCaptureStatus, CodeVersion
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.demo.replay_seed import (
    DEMO_END,
    DEMO_SELECTED_START,
    seed_replay_datasets,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.domain.market_data import Instrument, ProviderSymbolMapping
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.live_candles import live_candles, live_feeds
from market_analysis.persistence.market_data import instruments, register_instrument
from market_analysis.persistence.replay_runs import replay_runs
from market_analysis.persistence.runs import metadata, run_snapshots
from market_analysis.persistence.transactions import atomic_transaction
from market_analysis.persistence.validation_annotations import (
    create_missed_pattern_annotation,
    validation_annotations,
)

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
RETENTION_START = datetime(2026, 9, 28, 14, tzinfo=UTC)
RETENTION_NOW = RETENTION_START + timedelta(hours=4)


def clean_build(build: str) -> CodeVersion:
    return CodeVersion(build, "a" * 40, False, CodeCaptureStatus.AVAILABLE, "fixture")


def scratch_engine(tmp_path, monkeypatch, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    metadata.create_all(engine)
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path / "data"))
    return engine


def seeded_engine(tmp_path, monkeypatch):
    engine = scratch_engine(tmp_path, monkeypatch, "seeded.sqlite")
    store = DatasetStore(tmp_path / "data")
    with atomic_transaction(engine) as connection:
        seed_replay_datasets(connection, store)
    return engine


def table_count(engine, table) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def demo_instrument() -> Instrument:
    return Instrument(
        instrument_id="US30",
        display_name="US30",
        calendar_id="oanda-uk-v2",
        price_precision=1,
        point_size=Decimal("1"),
        provider_symbols=(ProviderSymbolMapping(provider="oanda", symbol="US30_USD"),),
    )


def test_register_instrument_boundary_rolls_back_nested_savepoint_writes(
    tmp_path, monkeypatch
):
    engine = scratch_engine(tmp_path, monkeypatch, "instruments.sqlite")
    with pytest.raises(RuntimeError, match="failure after savepoint"):
        with atomic_transaction(engine) as connection:
            register_instrument(connection, demo_instrument())
            raise RuntimeError("failure after savepoint")
    assert table_count(engine, instruments) == 0
    with atomic_transaction(engine) as connection:
        register_instrument(connection, demo_instrument())
    assert table_count(engine, instruments) == 1
    engine.dispose()


def test_missed_pattern_annotation_boundary_rolls_back(tmp_path, monkeypatch):
    engine = seeded_engine(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="failure after savepoint"):
        with atomic_transaction(engine) as connection:
            create_missed_pattern_annotation(
                connection,
                dataset_revision_id="offline-replay-us30-v1",
                instrument_id="US30",
                timeframe=Timeframe.M1,
                interval_start=DEMO_SELECTED_START,
                interval_end=DEMO_SELECTED_START + timedelta(minutes=1),
                pattern_id="RANGE_COMPRESSION_V1",
                pattern_version="1",
            )
            raise RuntimeError("failure after savepoint")
    assert table_count(engine, validation_annotations) == 0
    engine.dispose()


def test_retained_minute_boundary_rolls_back_feed_registration(tmp_path, monkeypatch):
    engine = scratch_engine(tmp_path, monkeypatch, "retention.sqlite")
    calendar = build_oanda_uk_calendar(
        provider="oanda", region="UK", environment="live",
        account="test", provider_symbol="US30_USD",
    )
    feed = HistoryFeed(
        instrument_id="US30",
        provider_symbol="US30_USD",
        calendar_version=calendar.version,
        account_fingerprint=sha256(b"test").hexdigest(),
    )
    with atomic_transaction(engine) as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
    bar = Bar(
        "US30", Timeframe.M1, RETENTION_START,
        Decimal("100"), Decimal("103"), Decimal("99"), Decimal("101"),
        source_id="oanda-midpoint",
    )
    real_lock = live_retention._lock_feed

    def lock_then_fail(connection, feed):  # noqa: ANN001
        real_lock(connection, feed)
        raise RuntimeError("failure after feed registration")

    monkeypatch.setattr(live_retention, "_lock_feed", lock_then_fail)
    with pytest.raises(RuntimeError, match="failure after feed registration"):
        retain_completed_minute(engine, feed, bar, at=RETENTION_NOW)
    assert table_count(engine, live_feeds) == 0
    assert table_count(engine, live_candles) == 0
    engine.dispose()


def test_replay_launch_boundary_rolls_back_run_snapshot(tmp_path, monkeypatch):
    engine = seeded_engine(tmp_path, monkeypatch)
    config = demo_detection_config("US30")
    with pytest.raises(RuntimeError, match="failure after run snapshot"):
        with atomic_transaction(engine) as connection:
            create_replay_run(
                connection,
                run_id=uuid4(),
                dataset_revision_id="offline-replay-us30-v1",
                detection_config=config,
                selected_start=DEMO_SELECTED_START,
                selected_end=DEMO_END,
                created_at=NOW,
                build_id="fixture",
                pattern_definitions=PATTERN_DEFINITIONS,
                revision_provider=clean_build,
            )
            raise RuntimeError("failure after run snapshot")
    assert table_count(engine, replay_runs) == 0
    assert table_count(engine, run_snapshots) == 0
    engine.dispose()
