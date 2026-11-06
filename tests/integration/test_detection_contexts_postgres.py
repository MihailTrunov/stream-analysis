"""PostgreSQL source binding, round-trip and append-only context enforcement."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from alembic import command
from alembic.config import Config
from market_analysis.application.context_capture import (
    capture_detection_context,
    load_detection_context,
)
from market_analysis.config import DetectionAnalysisConfig, PatternSelection
from market_analysis.detection import DetectorEvent, PatternInstance
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import MarketStateFrame
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    PatternDefinition,
    TransitionSpec,
)
from market_analysis.persistence.pattern_instances import (
    LifecycleStep,
    advance_pattern_instance,
    create_pattern_instance,
)
from market_analysis.persistence.runs import create_run_snapshot


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_context_postgres_roundtrip_and_immutable_guards(monkeypatch):
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    definition = PatternDefinition(
        "context-test", "1", "Context test", "fixture", (), (), (),
        ("idle", "candidate"), (TransitionSpec("idle", "candidate", "start"),),
        (ConditionGroup("evidence", ("start",)),), (),
        (ContextFieldSpec("count", "int"),), ("start",),
    )
    config = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="demo-v1",
        patterns=(PatternSelection.from_definition(definition),),
    )
    run_id = uuid4()
    at = datetime(2026, 10, 1, 12, tzinfo=UTC)
    bar = Bar("US30", Timeframe.M1, at, Decimal(1), Decimal(2), Decimal(1), Decimal(2))
    with engine.begin() as connection:
        run = create_run_snapshot(
            connection, run_id=run_id, dataset_revision_id="pg-context-fixture",
            calendar_version="demo-v1", build_id="test-build", detection_config=config,
            pattern_definitions={definition.identity: definition},
        )
        occurrence = create_pattern_instance(
            connection, run_id=run_id, definition=definition, binding_fingerprint="a" * 64,
            occurrence_event_time=at, occurrence_detection_time=at, occurrence_ordinal=0, at=at,
        )
        occurrence = advance_pattern_instance(
            connection, occurrence.instance_id, definition, expected_revision=0, bar=bar,
            steps=(LifecycleStep(
                "idle", "candidate", "start", at, at, rationale={"condition": "start"},
            ),),
            context={"count": 1},
        )
        stored = occurrence.detector_events[0]
        event = DetectorEvent(
            str(run_id), run.dataset_revision_id, "US30", "1m", run.detection_config_hash,
            definition.pattern_id, "1", "runtime-instance", 0,
            "idle", "candidate", "start", at, at, {"condition": "start"},
        )
        frame = MarketStateFrame(
            bar, 1, str(run_id), run.dataset_revision_id, run.detection_config_hash,
            run.detection_config_hash, {}, {}, {}, (),
        )
        snapshot = capture_detection_context(
            connection, frame=frame, event=event, stored=stored,
            instance=PatternInstance("context-test", "1", "runtime-instance", "candidate",
                                     {"count": 1}),
            definition=definition, available_at=at + timedelta(minutes=1),
        )
    with engine.connect() as connection:
        assert load_detection_context(connection, stored.event_id) == snapshot
    for operation in ("UPDATE detection_context_snapshots SET content_hash = 'changed'",
                      "DELETE FROM detection_context_snapshots"):
        with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(operation))
    with pytest.raises(RuntimeError, match="must be retained"):
        command.downgrade(Config("alembic.ini"), "20261105_15")
    with engine.connect() as connection:
        assert load_detection_context(connection, stored.event_id) == snapshot
    engine.dispose()
