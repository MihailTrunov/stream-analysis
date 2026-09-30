from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from alembic.config import Config
from market_analysis.application.diagnostics import database_status
from market_analysis.config import DetectionAnalysisConfig
from market_analysis.persistence.runs import create_run_snapshot, load_run_snapshot


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_migration_and_snapshot_roundtrip_on_postgres(monkeypatch) -> None:
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    assert database_status(url) == (True, "20260930_06")
    engine = create_engine(url)
    run_id = uuid4()
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260930_06"
        expected = create_run_snapshot(
            connection,
            run_id=run_id,
            dataset_revision_id="demo-rev-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=config,
        )
    with engine.connect() as connection:
        assert load_run_snapshot(connection, run_id) == expected
    engine.dispose()
