from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from market_analysis import __version__
from market_analysis.application.diagnostics import database_status, read_worker_heartbeat
from market_analysis.application.logging import configure_logging, research_logger
from market_analysis.config import (
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.component_registry import component_definition_schema
from market_analysis.demo.data import load_demo_bars


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
        oanda_credentials_configured=bool(os.getenv("OANDA_TOKEN")),
        oanda_import_available=False,
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
