"""PostgreSQL + staged file recovery and immutable publication across restart."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from alembic import command
from alembic.config import Config
from market_analysis.application.historical_import import advance_import
from market_analysis.domain import (
    Bar,
    Instrument,
    ProviderSymbolMapping,
    SessionWindow,
    Timeframe,
    TradingCalendar,
)
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.import_batches import ImportBatchStore
from market_analysis.persistence.import_jobs import (
    ImportJobError,
    ImportRequest,
    ImportStatus,
    claim_import_job,
    create_import_job,
    interrupt_running_imports,
    load_import_job,
    resume_import_job,
)
from market_analysis.persistence.market_data import load_dataset_revision, register_instrument
from market_analysis.providers.memory import InMemoryHistoricalDataSource


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_postgres_import_resumes_only_on_explicit_request_and_publishes_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    suffix = uuid4().hex
    instrument_id = f"US30-{suffix}"
    symbol = f"TEST-{suffix}"
    start = datetime(2026, 9, 29, 12, tzinfo=UTC)
    instrument = Instrument(
        instrument_id,
        "Postgres import fixture",
        f"calendar-{suffix}",
        1,
        Decimal("1"),
        (ProviderSymbolMapping("fixture", symbol, "test"),),
    )
    calendar = TradingCalendar(
        f"calendar-{suffix}",
        "cal-v1",
        "fixture",
        "account",
        instrument_id,
        "UTC",
        time(0),
        (SessionWindow("full", time(0), time(23, 59)),),
    )
    bars = tuple(
        Bar(
            instrument_id,
            Timeframe.M1,
            start + timedelta(minutes=minute),
            Decimal("100"),
            Decimal("102"),
            Decimal("99"),
            Decimal("101"),
            source_id="fixture",
        )
        for minute in range(3)
    )
    source = InMemoryHistoricalDataSource(
        bars,
        provider="fixture",
        environment="test",
        page_size=1,
    )
    engine = create_engine(url)
    store, staging = DatasetStore(tmp_path), ImportBatchStore(tmp_path)
    try:
        with engine.begin() as connection:
            register_instrument(connection, instrument)
            job = create_import_job(
                connection,
                ImportRequest(
                    f"study-{suffix}",
                    instrument_id,
                    Timeframe.M1,
                    start,
                    start + timedelta(minutes=3),
                    "fixture",
                    "test",
                    symbol,
                    "cal-v1",
                ),
                job_id=str(uuid4()),
                revision_id=str(uuid4()),
                at=start,
            )
            assert claim_import_job(connection, at=start) is not None
            with pytest.raises(ImportJobError, match="another import is already active"):
                create_import_job(
                    connection,
                    replace(job.request, dataset_id=f"other-{suffix}"),
                    job_id=str(uuid4()),
                    revision_id=str(uuid4()),
                    at=start,
                )
        first = advance_import(engine, source, store, staging, job.job_id, calendar)
        assert first.bar_count == 1 and first.next_page_token is not None
        with engine.begin() as connection:
            assert interrupt_running_imports(connection, at=start + timedelta(minutes=1)) == 1
            assert load_import_job(connection, job.job_id).status is ImportStatus.INTERRUPTED
            assert load_dataset_revision(connection, job.revision_id) is None
            resume_import_job(connection, job.job_id, at=start + timedelta(minutes=2))
            assert claim_import_job(connection, at=start + timedelta(minutes=2)) is not None
        advance_import(engine, source, store, staging, job.job_id, calendar)
        completed = advance_import(engine, source, store, staging, job.job_id, calendar)
        assert completed.status is ImportStatus.COMPLETED
        assert completed.bar_count == 3
        with engine.connect() as connection:
            assert (
                store.load_sequence(connection, job.revision_id, instrument_id, Timeframe.M1).bars
                == bars
            )
            assert load_dataset_revision(connection, job.revision_id) is not None
    finally:
        engine.dispose()
