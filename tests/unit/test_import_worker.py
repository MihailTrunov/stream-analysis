"""The import worker owns the provider, while acquisition stays provider-neutral."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine

from market_analysis.application import worker
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import (
    Bar,
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalSource,
    Timeframe,
)
from market_analysis.domain.historical_data import ProviderError
from market_analysis.persistence.import_jobs import (
    ImportRequest,
    ImportStatus,
    claim_import_job,
    create_import_job,
    load_import_job,
)
from market_analysis.persistence.market_data import register_instrument
from market_analysis.persistence.runs import metadata


def _setup(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    metadata.create_all(engine)
    start = datetime(2026, 9, 28, 14, tzinfo=UTC)
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="test-account",
        provider_symbol="US30_USD",
    )
    with engine.begin() as connection:
        register_instrument(connection, build_oanda_uk_instrument("US30_USD"))
        job = create_import_job(
            connection,
            ImportRequest(
                "worker-test",
                "US30",
                Timeframe.M1,
                start,
                start + timedelta(minutes=1),
                "oanda",
                "live",
                "US30_USD",
                calendar.version,
                sha256(b"test-account").hexdigest(),
            ),
            job_id=str(uuid4()),
            revision_id=str(uuid4()),
            at=start,
        )
        assert claim_import_job(connection, at=start) is not None
    return engine, job, start


def test_worker_verifies_account_then_publishes_provider_page(monkeypatch, tmp_path: Path) -> None:
    engine, job, start = _setup(tmp_path)
    verified = []

    class FakeOanda:
        def __init__(self, account: str, token: str, environment: str) -> None:
            assert (account, token, environment) == ("test-account", "test-token", "live")

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def verify_account_instrument(self, symbol: str) -> None:
            verified.append(symbol)

        def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage:
            assert verified == ["US30_USD"]
            bar = Bar(
                "US30",
                Timeframe.M1,
                start,
                Decimal("100"),
                Decimal("102"),
                Decimal("99"),
                Decimal("101"),
                source_id="oanda-midpoint",
            )
            return HistoricalDataPage(
                request, (bar,), HistoricalSource("oanda", "US30_USD", "oanda-midpoint", "live")
            )

    monkeypatch.setattr(worker, "OandaHistoricalDataSource", FakeOanda)
    try:
        worker._run_import_job(
            engine, tmp_path, job, ("test-token", "test-account", "live"), lambda: False
        )
        with engine.connect() as connection:
            completed = load_import_job(connection, job.job_id)
            assert completed is not None
            assert completed.status is ImportStatus.COMPLETED
            assert completed.bar_count == 1
    finally:
        engine.dispose()


def test_worker_provider_error_is_terminal(monkeypatch, tmp_path: Path) -> None:
    engine, job, _ = _setup(tmp_path)

    class FailingOanda:
        def __init__(self, *_args: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def verify_account_instrument(self, _symbol: str) -> None:
            raise ProviderError("account mapping unavailable")

    monkeypatch.setattr(worker, "OandaHistoricalDataSource", FailingOanda)
    try:
        worker._run_import_job(
            engine, tmp_path, job, ("test-token", "test-account", "live"), lambda: False
        )
        with engine.connect() as connection:
            failed = load_import_job(connection, job.job_id)
            assert failed is not None
            assert failed.status is ImportStatus.FAILED
            assert failed.failure_reason == "account mapping unavailable"
    finally:
        engine.dispose()


def test_worker_rejects_changed_account_before_calling_provider(
    monkeypatch, tmp_path: Path
) -> None:
    engine, job, _ = _setup(tmp_path)

    def unexpected_provider(*_args: object) -> None:
        raise AssertionError("account mismatch must not call the provider")

    monkeypatch.setattr(worker, "OandaHistoricalDataSource", unexpected_provider)
    try:
        worker._run_import_job(
            engine,
            tmp_path,
            job,
            ("test-token", "different-account", "live"),
            lambda: False,
        )
        with engine.connect() as connection:
            failed = load_import_job(connection, job.job_id)
            assert failed is not None
            assert failed.status is ImportStatus.FAILED
            assert (
                failed.failure_reason == "import account differs from its original account context"
            )
    finally:
        engine.dispose()
