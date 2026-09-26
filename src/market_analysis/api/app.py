from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from market_analysis import __version__
from market_analysis.application.diagnostics import database_status, read_worker_heartbeat


class DiagnosticsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_version: str
    build_id: str
    database_configured: bool
    database_healthy: bool
    schema_version: str | None
    evaluation_worker_available: bool
    import_worker_available: bool
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
        oanda_import_available=bool(os.getenv("OANDA_TOKEN")),
        data_root=str(data_root),
    )


app = FastAPI(title="Stream Analysis", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/diagnostics", response_model=DiagnosticsResponse)
def diagnostics() -> DiagnosticsResponse:
    return diagnostics_snapshot()
