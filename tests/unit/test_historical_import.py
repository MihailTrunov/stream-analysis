from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from market_analysis.application.historical_import import advance_import
from market_analysis.domain import (
    Bar,
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalSource,
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
    fail_import_job,
    interrupt_running_imports,
    list_import_batches,
    load_import_job,
    resume_import_job,
)
from market_analysis.persistence.market_data import load_dataset_revision, register_instrument
from market_analysis.persistence.runs import metadata
from market_analysis.persistence.verify_datasets import main as verify_restored_datasets
from market_analysis.providers.memory import InMemoryHistoricalDataSource

START = datetime(2026, 9, 29, 12, tzinfo=UTC)


def _instrument() -> Instrument:
    return Instrument(
        "US30",
        "US 30",
        "calendar",
        1,
        Decimal("1"),
        (ProviderSymbolMapping("fixture", "TEST", "test"),),
    )


def _bar(minute: int, close: str = "101") -> Bar:
    return Bar(
        "US30",
        Timeframe.M1,
        START + timedelta(minutes=minute),
        Decimal("100"),
        Decimal("102"),
        Decimal("99"),
        Decimal(close),
        source_id="fixture",
    )


def _calendar() -> TradingCalendar:
    return TradingCalendar(
        "calendar",
        "cal-v1",
        "fixture",
        "account",
        "US30",
        "UTC",
        time(0),
        (SessionWindow("full", time(0), time(23, 59)),),
    )


def _request() -> ImportRequest:
    return ImportRequest(
        "study",
        "US30",
        Timeframe.M1,
        START,
        START + timedelta(minutes=3),
        "fixture",
        "test",
        "TEST",
        "cal-v1",
    )


def _setup(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        register_instrument(connection, _instrument())
        job = create_import_job(
            connection,
            _request(),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START,
        )
        assert claim_import_job(connection, at=START) is not None
    return engine, DatasetStore(tmp_path), ImportBatchStore(tmp_path), job


def test_explicit_resume_from_last_durable_checkpoint_and_idempotent_rerun(tmp_path: Path) -> None:
    engine, dataset_store, batch_store, job = _setup(tmp_path)
    bars = (_bar(0), _bar(1), _bar(2))
    first_source = InMemoryHistoricalDataSource(
        bars,
        provider="fixture",
        environment="test",
        page_size=1,
    )
    first = advance_import(
        engine,
        first_source,
        dataset_store,
        batch_store,
        job.job_id,
        _calendar(),
        at=START + timedelta(minutes=1),
    )
    assert first.status is ImportStatus.RUNNING
    assert first.pages_committed == first.bar_count == 1
    assert first.next_page_token is not None
    with engine.connect() as connection:
        assert load_dataset_revision(connection, job.revision_id) is None
    engine.dispose()  # Simulate process exit; checkpoint remains in SQLite and on disk.

    restarted = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}")
    with restarted.begin() as connection:
        assert interrupt_running_imports(connection, at=START + timedelta(minutes=2)) == 1
        interrupted = load_import_job(connection, job.job_id)
        assert interrupted is not None and interrupted.status is ImportStatus.INTERRUPTED
    with pytest.raises(ImportJobError, match="claimed"):
        advance_import(restarted, first_source, dataset_store, batch_store, job.job_id, _calendar())
    with restarted.begin() as connection:
        resume_import_job(connection, job.job_id, at=START + timedelta(minutes=3))
        assert claim_import_job(connection, at=START + timedelta(minutes=3)) is not None

    class RecordingSource:
        def __init__(self) -> None:
            self.tokens: list[str | None] = []

        def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage:
            self.tokens.append(request.page_token)
            return first_source.get_bars(request)

    resumed_source = RecordingSource()
    changed = advance_import(
        restarted,
        resumed_source,
        dataset_store,
        batch_store,
        job.job_id,
        _calendar(),
        at=START + timedelta(minutes=4),
    )
    assert changed.bar_count == 2 and changed.status is ImportStatus.RUNNING
    completed = advance_import(
        restarted,
        resumed_source,
        dataset_store,
        batch_store,
        job.job_id,
        _calendar(),
        at=START + timedelta(minutes=5),
    )
    assert resumed_source.tokens[0] == first.next_page_token
    assert completed.status is ImportStatus.COMPLETED
    assert completed.bar_count == 3
    assert completed.actual_start == START
    assert completed.actual_end == START + timedelta(minutes=3)
    report = json.loads(completed.gap_report_json)
    assert report["missing_intervals"] == []
    assert report["unexpected_intervals"] == []
    with restarted.begin() as connection:
        assert (
            dataset_store.load_sequence(connection, job.revision_id, "US30", Timeframe.M1).bars
            == bars
        )
        same = create_import_job(
            connection,
            _request(),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START + timedelta(minutes=6),
        )
        assert same.job_id == job.job_id
        fresh = create_import_job(
            connection,
            _request(),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START + timedelta(minutes=7),
            fresh_attempt=True,
        )
        assert fresh.job_id != job.job_id and fresh.status is ImportStatus.QUEUED
        assert claim_import_job(connection, at=START + timedelta(minutes=8)).job_id == fresh.job_id
        fail_import_job(connection, fresh.job_id, at=START + timedelta(minutes=9), reason="test")
        prior = create_import_job(
            connection,
            _request(),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START + timedelta(minutes=10),
        )
        assert prior.job_id == job.job_id
    restarted.dispose()


def test_only_one_queued_or_running_import_and_completed_request_is_preferred(
    tmp_path: Path,
) -> None:
    engine, _, _, first = _setup(tmp_path)
    with engine.begin() as connection:
        with pytest.raises(ImportJobError, match="another import is already active"):
            create_import_job(
                connection,
                replace(_request(), dataset_id="other"),
                job_id=str(uuid4()),
                revision_id=str(uuid4()),
                at=START + timedelta(minutes=1),
            )
        assert interrupt_running_imports(connection, at=START + timedelta(minutes=2)) == 1
        second = create_import_job(
            connection,
            replace(_request(), dataset_id="other"),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START + timedelta(minutes=3),
        )
        with pytest.raises(ImportJobError, match="another import is already active"):
            resume_import_job(connection, first.job_id, at=START + timedelta(minutes=4))
        assert claim_import_job(connection, at=START + timedelta(minutes=5)).job_id == second.job_id
        assert fail_import_job(connection, second.job_id, at=START, reason="provider error")
        resumed = resume_import_job(connection, first.job_id, at=START + timedelta(minutes=6))
        assert resumed.status is ImportStatus.QUEUED
    engine.dispose()


def test_restore_verification_checks_checkpointed_import_batches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine, dataset_store, batch_store, job = _setup(tmp_path)
    source = InMemoryHistoricalDataSource(
        (_bar(0), _bar(1), _bar(2)), provider="fixture", environment="test", page_size=1
    )
    advance_import(engine, source, dataset_store, batch_store, job.job_id, _calendar())
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", f"sqlite:///{tmp_path / 'jobs.sqlite'}")
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path))
    verify_restored_datasets()
    with engine.connect() as connection:
        batch = list_import_batches(connection, job.job_id)[0]
    (tmp_path / batch.relative_path).write_text("tampered\n")
    with pytest.raises(ImportJobError, match="checksum differs"):
        verify_restored_datasets()
    engine.dispose()


def test_provider_overlap_collapses_exact_duplicate_and_rejects_conflict(tmp_path: Path) -> None:
    engine, dataset_store, batch_store, job = _setup(tmp_path)

    class OverlapSource:
        def __init__(self, conflict: bool = False) -> None:
            self.conflict = conflict

        def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage:
            origin = HistoricalSource("fixture", "TEST", "fixture", "test")
            if request.page_token is None:
                return HistoricalDataPage(request, (_bar(0),), origin, "next")
            close = "100" if self.conflict else "101"
            return HistoricalDataPage(request, (_bar(0, close), _bar(1), _bar(2)), origin)

    advance_import(engine, OverlapSource(), dataset_store, batch_store, job.job_id, _calendar())
    completed = advance_import(
        engine, OverlapSource(), dataset_store, batch_store, job.job_id, _calendar()
    )
    assert completed.status is ImportStatus.COMPLETED and completed.bar_count == 3
    with engine.connect() as connection:
        assert [batch.bar_count for batch in list_import_batches(connection, job.job_id)] == [1, 2]

    with engine.begin() as connection:
        other = create_import_job(
            connection,
            replace(_request(), dataset_id="other"),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=START,
        )
        claim_import_job(connection, at=START)
    advance_import(engine, OverlapSource(), dataset_store, batch_store, other.job_id, _calendar())
    with pytest.raises(ImportJobError, match="conflicting duplicate"):
        advance_import(
            engine,
            OverlapSource(conflict=True),
            dataset_store,
            batch_store,
            other.job_id,
            _calendar(),
        )
    with engine.connect() as connection:
        assert load_dataset_revision(connection, other.revision_id) is None
    engine.dispose()


def test_uncommitted_stage_file_is_not_progress_and_tampering_is_refused(tmp_path: Path) -> None:
    engine, dataset_store, batch_store, job = _setup(tmp_path)
    orphan = batch_store.write_batch(job.job_id, 0, (_bar(0),))
    with engine.connect() as connection:
        assert list_import_batches(connection, job.job_id) == ()
        assert load_import_job(connection, job.job_id).bar_count == 0
    source = InMemoryHistoricalDataSource(
        (_bar(0), _bar(1), _bar(2)),
        provider="fixture",
        environment="test",
        page_size=1,
    )
    advance_import(engine, source, dataset_store, batch_store, job.job_id, _calendar())
    with engine.connect() as connection:
        assert list_import_batches(connection, job.job_id)[0] == orphan
    path = batch_store.root / orphan.relative_path
    path.write_bytes(path.read_bytes() + b"tampered\n")
    with pytest.raises(ImportJobError, match="checksum differs"):
        advance_import(engine, source, dataset_store, batch_store, job.job_id, _calendar())
    with engine.connect() as connection:
        assert load_dataset_revision(connection, job.revision_id) is None
    engine.dispose()


def test_empty_provider_period_publishes_empty_warning_revision(tmp_path: Path) -> None:
    engine, dataset_store, batch_store, job = _setup(tmp_path)
    source = InMemoryHistoricalDataSource((), provider="fixture", environment="test")
    completed = advance_import(engine, source, dataset_store, batch_store, job.job_id, _calendar())
    assert completed.status is ImportStatus.COMPLETED
    assert completed.bar_count == 0 and completed.pages_committed == 0
    report = json.loads(completed.gap_report_json)
    assert report["missing_intervals"] == [
        {
            "start": "2026-09-29T12:00:00Z",
            "end": "2026-09-29T12:03:00Z",
        }
    ]
    with engine.connect() as connection:
        sequence = dataset_store.load_sequence(connection, job.revision_id, "US30", Timeframe.M1)
        assert sequence.bars == ()
        assert sequence.lineage.validation_status.value == "warning"
    engine.dispose()
