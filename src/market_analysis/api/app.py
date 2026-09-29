from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine

from market_analysis import __version__
from market_analysis.application.diagnostics import database_status, read_worker_heartbeat
from market_analysis.application.logging import configure_logging, research_logger
from market_analysis.config import (
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.component_registry import component_definition_schema
from market_analysis.config.oanda_uk_calendars import (
    OANDA_UK_LIVE_PROFILES,
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.demo.data import load_demo_bars
from market_analysis.domain import SessionCalendarError, Timeframe
from market_analysis.persistence.import_jobs import (
    ImportJob,
    ImportJobError,
    ImportRequest,
    create_import_job,
    list_import_jobs,
    load_import_job,
    resume_import_job,
)
from market_analysis.persistence.market_data import MetadataConflictError, register_instrument


class DiagnosticsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_version: str
    build_id: str
    database_configured: bool
    database_healthy: bool
    schema_version: str | None
    evaluation_worker_available: bool
    import_worker_available: bool
    oanda_credentials_configured: bool
    oanda_import_available: bool
    data_root: str


class ImportCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=1, max_length=200)
    instrument_id: str
    start: datetime
    end: datetime
    fresh_attempt: bool = False


class ImportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    dataset_id: str
    revision_id: str
    instrument_id: str
    status: str
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    bar_count: int
    pages_committed: int
    fetch_complete: bool
    heartbeat_at: datetime | None
    failure_reason: str | None


def _import_response(job: ImportJob) -> ImportResponse:
    return ImportResponse(
        job_id=job.job_id,
        dataset_id=job.request.dataset_id,
        revision_id=job.revision_id,
        instrument_id=job.request.instrument_id,
        status=job.status.value,
        requested_start=job.request.start,
        requested_end=job.request.end,
        actual_start=job.actual_start,
        actual_end=job.actual_end,
        bar_count=job.bar_count,
        pages_committed=job.pages_committed,
        fetch_complete=job.fetch_complete,
        heartbeat_at=job.heartbeat_at,
        failure_reason=job.failure_reason,
    )


def _import_config() -> tuple[str, str]:
    token = os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN") or ""
    account = os.getenv("OANDA_ACCOUNT") or ""
    environment = (os.getenv("OANDA_ENV") or "live").lower()
    region = (os.getenv("OANDA_REGION") or "UK").upper()
    if not token or not account or environment != "live" or region != "UK":
        raise HTTPException(
            status_code=503,
            detail="OANDA UK/live import credentials and account context are unavailable",
        )
    return account, environment


def _database_url() -> str:
    url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="database is not configured")
    return url


def diagnostics_snapshot() -> DiagnosticsResponse:
    data_root = Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data")).expanduser().resolve()
    database_url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    database_healthy, schema_version = database_status(database_url)
    return DiagnosticsResponse(
        app_version=__version__,
        build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
        database_configured=bool(database_url),
        database_healthy=database_healthy,
        schema_version=schema_version,
        evaluation_worker_available=read_worker_heartbeat(data_root, "evaluation"),
        import_worker_available=read_worker_heartbeat(data_root, "import"),
        oanda_credentials_configured=bool(os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN")),
        oanda_import_available=bool(
            (os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN"))
            and os.getenv("OANDA_ACCOUNT")
            and (os.getenv("OANDA_ENV") or "live").lower() == "live"
            and (os.getenv("OANDA_REGION") or "UK").upper() == "UK"
            and database_healthy
            and read_worker_heartbeat(data_root, "import")
        ),
        data_root=str(data_root),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger = research_logger(
        component="api",
        build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
    )
    logger.info("API started")
    try:
        yield
    finally:
        logger.info("API stopped")


app = FastAPI(title="Stream Analysis", version=__version__, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/diagnostics", response_model=DiagnosticsResponse)
def diagnostics() -> DiagnosticsResponse:
    return diagnostics_snapshot()


@app.get("/demo/bars")
def demo_bars() -> dict[str, object]:
    bars = load_demo_bars()
    research_logger(component="api", instrument="US30").info(
        "seeded demo bars served",
        extra={"bar_count": len(bars), "non_research_grade": True},
    )
    return {
        "non_research_grade": True,
        "bars": [dict(bar.to_canonical_dict()) for bar in bars],
    }


@app.get("/component-definitions")
def component_definitions() -> dict[str, object]:
    """Expose editable schemas; accepting a configuration remains server-authoritative."""
    return {"components": component_definition_schema()}


@app.post("/config/preview")
def preview_detection_config(config: DetectionAnalysisConfig) -> dict[str, object]:
    """Resolve and hash a proposed config; never change a running snapshot."""
    try:
        resolved = resolve_detection_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "detection_config": resolved.canonical_dict(),
        "detection_config_hash": detection_config_hash(resolved),
    }


@app.post("/imports", response_model=ImportResponse, status_code=202)
def create_import(request: ImportCreateRequest) -> ImportResponse:
    """Schedule one local UK/live M1 import without putting credentials in PostgreSQL."""
    account, environment = _import_config()
    profile = next(
        (
            profile
            for profile in OANDA_UK_LIVE_PROFILES.values()
            if profile.instrument_id == request.instrument_id
        ),
        None,
    )
    if profile is None:
        raise HTTPException(status_code=422, detail="unsupported MVP import instrument")
    try:
        instrument = build_oanda_uk_instrument(profile.provider_symbol)
        calendar = build_oanda_uk_calendar(
            provider="oanda",
            region="UK",
            environment=environment,
            account=account,
            provider_symbol=profile.provider_symbol,
        )
        selected = ImportRequest(
            request.dataset_id,
            instrument.instrument_id,
            Timeframe.M1,
            request.start,
            request.end,
            "oanda",
            environment,
            profile.provider_symbol,
            calendar.version,
            sha256(account.encode()).hexdigest(),
        )
        calendar.derive(selected.start)
        calendar.derive(selected.end - timedelta(microseconds=1))
    except (ImportJobError, SessionCalendarError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    engine = create_engine(_database_url())
    try:
        with engine.begin() as connection:
            try:
                register_instrument(connection, instrument)
            except MetadataConflictError as exc:
                raise HTTPException(
                    status_code=409, detail="registered instrument metadata conflicts"
                ) from exc
            try:
                job = create_import_job(
                    connection,
                    selected,
                    job_id=str(uuid4()),
                    revision_id=str(uuid4()),
                    at=datetime.now(UTC),
                    fresh_attempt=request.fresh_attempt,
                )
            except ImportJobError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return _import_response(job)
    finally:
        engine.dispose()


@app.get("/imports", response_model=list[ImportResponse])
def imports() -> list[ImportResponse]:
    engine = create_engine(_database_url())
    try:
        with engine.connect() as connection:
            return [_import_response(job) for job in list_import_jobs(connection)]
    finally:
        engine.dispose()


@app.get("/imports/{job_id}", response_model=ImportResponse)
def import_status(job_id: str) -> ImportResponse:
    engine = create_engine(_database_url())
    try:
        with engine.connect() as connection:
            job = load_import_job(connection, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="import job not found")
            return _import_response(job)
    finally:
        engine.dispose()


@app.post("/imports/{job_id}/resume", response_model=ImportResponse, status_code=202)
def resume_import(job_id: str) -> ImportResponse:
    account, _ = _import_config()
    engine = create_engine(_database_url())
    try:
        with engine.begin() as connection:
            original = load_import_job(connection, job_id)
            if original is None:
                raise HTTPException(status_code=404, detail="import job not found")
            if (
                original.request.account_fingerprint is not None
                and original.request.account_fingerprint != sha256(account.encode()).hexdigest()
            ):
                raise HTTPException(status_code=409, detail="import account context has changed")
            try:
                job = resume_import_job(connection, job_id, at=datetime.now(UTC))
            except ImportJobError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return _import_response(job)
    finally:
        engine.dispose()
