"""Real PostgreSQL metadata and local Parquet revision round trip."""

from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from alembic import command
from alembic.config import Config
from market_analysis.application.replay_pipeline import ReplayPipeline
from market_analysis.application.replay_run import create_replay_run
from market_analysis.config import DetectionAnalysisConfig
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
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_dataset_revision_publication_and_offline_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    unique = uuid4().hex
    instrument_id = f"instrument-{unique}"
    revision_id = f"revision-{unique}"
    start = datetime(2026, 9, 27, 12, tzinfo=UTC)
    bars = (
        Bar(instrument_id, Timeframe.M1, start, Decimal("100"), Decimal("102"),
            Decimal("99"), Decimal("101"), source_id="fixture"),
        Bar(instrument_id, Timeframe.M1, start + timedelta(minutes=1), Decimal("100"),
            Decimal("102"), Decimal("99"), Decimal("101"), source_id="fixture"),
    )
    revision = DatasetRevision(
        revision_id, f"study-{unique}", f"import-{unique}", "fixture",
        start + timedelta(hours=2), start + timedelta(hours=2, seconds=1),
        "normalizer-v1", "calendar-v1", "parquet-v1",
        (DatasetMembership(instrument_id, Timeframe.M1, start,
                           start + timedelta(hours=1), len(bars)),),
        f"datasets/{revision_id}/manifest.json",
    )
    lineage = DatasetLineage(
        revision_id, f"import-{unique}", instrument_id, Timeframe.M1,
        start, start + timedelta(hours=1), bars[0].timestamp,
        bars[-1].timestamp + timedelta(minutes=1), len(bars),
        start + timedelta(hours=2), ValidationStatus.PASS,
        '{"price":"M","symbol":"TEST"}', "a" * 64,
        canonical_bar_checksum(bars), BAR_CHECKSUM_VERSION, "parquet-v1",
    )
    store = DatasetStore(tmp_path)
    run_id = uuid4()
    calendar = TradingCalendar(
        "calendar-v1", "calendar-v1", "fixture", "local", instrument_id,
        "Europe/London", time(0), (SessionWindow("all-day", time(0), time(23, 59)),),
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            register_instrument(connection, Instrument(
                instrument_id, "Integration fixture", "calendar-v1", 2,
                Decimal("0.01"), (ProviderSymbolMapping("fixture", f"symbol-{unique}"),),
            ))
            store.publish(connection, revision, lineage, bars)
            create_replay_run(
                connection, run_id=run_id, dataset_revision_id=revision_id,
                detection_config=DetectionAnalysisConfig(
                    instrument_id=instrument_id, calendar_id="calendar-v1",
                ),
                selected_start=start, selected_end=start + timedelta(minutes=2),
                created_at=start + timedelta(hours=3), build_id="integration-build",
            )
        with engine.connect() as connection:
            assert store.load_sequence(connection, revision_id, instrument_id,
                                       Timeframe.M1).bars == bars
            store.verify_revision(connection, revision_id)
            assert tuple(store.iter_verified_bars(
                connection, revision_id, instrument_id, Timeframe.M1,
            )) == bars
            replay = ReplayPipeline.from_run(
                connection, run_id, dataset_store=store, bindings_factory=lambda: (),
                calendar_resolver=lambda calendar_id, version: calendar,
            )
            assert replay.has_next
    finally:
        engine.dispose()
