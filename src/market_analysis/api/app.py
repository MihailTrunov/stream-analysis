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
from market_analysis.application.browser_replay import (
    BrowserReplayError,
    browser_replay,
    replay_sources,
)
from market_analysis.application.diagnostics import database_status, read_worker_heartbeat
from market_analysis.application.logging import configure_logging, research_logger
from market_analysis.application.replay_catalog import (
    DEMO_CONFIG_ID,
    DEMO_CONFIG_VERSION,
    PATTERN_DEFINITIONS,
    demo_detection_config,
    required_warmup_bars,
)
from market_analysis.application.replay_pipeline import DetectorEventFilter
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
from market_analysis.demo.replay_seed import seed_replay_datasets
from market_analysis.domain import SessionCalendarError, Timeframe
from market_analysis.persistence.dataset_store import DatasetStore
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
from market_analysis.persistence.runs import metadata


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


class ReplayLaunchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_revision_id: str
    instrument_id: str
    timeframe: Timeframe = Timeframe.M1
    selected_start: datetime
    selected_end: datetime
    config_id: str = DEMO_CONFIG_ID
    config_version: str = DEMO_CONFIG_VERSION
    detection_config: DetectionAnalysisConfig | None = None


class ReplaySeekRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: datetime


class ReplayEventFilterRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_id: str | None = None
    pattern_version: str | None = None
    instance_id: str | None = None
    trigger_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None


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
    database_url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if database_url and os.getenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "1") == "1":
        engine = create_engine(database_url)
        try:
            if os.getenv("STREAM_ANALYSIS_SMOKE_INIT_DB") == "1":
                # Isolated browser-test SQLite only; normal installations use Alembic.
                if not database_url.startswith("sqlite+"):
                    raise RuntimeError("smoke schema initialization requires SQLite")
                metadata.create_all(engine)
            with engine.begin() as connection:
                seed_replay_datasets(
                    connection, DatasetStore(Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data")))
                )
        finally:
            engine.dispose()
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
        resolved = resolve_detection_config(config, pattern_definitions=PATTERN_DEFINITIONS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "detection_config": resolved.canonical_dict(),
        "detection_config_hash": detection_config_hash(
            resolved, pattern_definitions=PATTERN_DEFINITIONS
        ),
    }


@app.get("/replay/sources")
def browser_replay_sources() -> dict[str, object]:
    engine = create_engine(_database_url())
    try:
        with engine.connect() as connection:
            return {"sources": replay_sources(connection)}
    finally:
        engine.dispose()


@app.get("/replay/config-default")
def browser_replay_default_config(instrument_id: str) -> dict[str, object]:
    try:
        resolved = resolve_detection_config(
            demo_detection_config(instrument_id), pattern_definitions=PATTERN_DEFINITIONS
        )
        return {
            "config_id": DEMO_CONFIG_ID,
            "config_version": DEMO_CONFIG_VERSION,
            # The canonical hash payload includes value_type audit fields, while
            # the editable request model accepts only name/value pairs.
            "detection_config": resolved.model_dump(mode="json"),
            "detection_config_hash": detection_config_hash(
                resolved, pattern_definitions=PATTERN_DEFINITIONS
            ),
            "warmup_bars": required_warmup_bars(resolved),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/replay", status_code=201)
def launch_browser_replay(request: ReplayLaunchRequest) -> dict[str, object]:
    if (request.config_id, request.config_version) != (DEMO_CONFIG_ID, DEMO_CONFIG_VERSION):
        raise HTTPException(status_code=422, detail="unknown replay configuration version")
    if request.timeframe is not Timeframe.M1:
        raise HTTPException(status_code=422, detail="browser replay requires M1")
    config = request.detection_config or demo_detection_config(request.instrument_id)
    if config.instrument_id != request.instrument_id or config.timeframe != request.timeframe:
        raise HTTPException(status_code=422, detail="configuration instrument/timeframe mismatch")
    try:
        return browser_replay.launch(
            request.dataset_revision_id, config, request.selected_start, request.selected_end
        )
    except BrowserReplayError as exc:
        status = 409 if "active walkthrough" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/replay/active")
def active_browser_replay() -> dict[str, object]:
    return {"active": browser_replay.active()}


@app.get("/replay/{run_id}")
def browser_replay_state(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.state(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/replay/{run_id}/stop")
def stop_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.stop(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/replay/{run_id}/step")
def step_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.step_visible(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/tick")
def tick_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.step_visible(run_id, keep_running=True)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/play")
def play_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.play(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/pause")
def pause_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.pause(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/next-event")
def next_browser_replay_event(
    run_id: str, request: ReplayEventFilterRequest | None = None,
) -> dict[str, object]:
    try:
        event_filter = DetectorEventFilter(**request.model_dump()) if request else None
        return browser_replay.next_event(run_id, event_filter)
    except (BrowserReplayError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/reset")
def reset_browser_replay(run_id: str) -> dict[str, object]:
    try:
        return browser_replay.replace(run_id)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/replay/{run_id}/seek")
def seek_browser_replay(run_id: str, request: ReplaySeekRequest) -> dict[str, object]:
    try:
        return browser_replay.replace(run_id, target=request.target)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/replay/{run_id}/bars")
def browser_replay_bars(
    run_id: str, start: datetime, end: datetime, limit: int = 100,
) -> dict[str, object]:
    try:
        return browser_replay.visible_bars(run_id, start, end, limit)
    except BrowserReplayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
