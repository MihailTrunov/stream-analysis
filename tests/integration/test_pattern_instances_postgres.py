from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from alembic import command
from alembic.config import Config
from market_analysis.config import DetectionAnalysisConfig, PatternSelection
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    PatternDefinition,
    TransitionSpec,
)
from market_analysis.patterns.instance_context import PatternInstanceError
from market_analysis.persistence.pattern_instances import (
    LifecycleStep,
    advance_pattern_instance,
    create_pattern_instance,
    load_pattern_instance,
)
from market_analysis.persistence.runs import create_run_snapshot


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_postgres_pattern_instances_roundtrip_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    start = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    definition = PatternDefinition(
        "pg-reversal",
        "1",
        "PG reversal",
        "test",
        (),
        (),
        (),
        ("idle", "candidate", "completed"),
        (
            TransitionSpec("idle", "candidate", "start"),
            TransitionSpec("candidate", "completed", "finish"),
        ),
        (ConditionGroup("evidence", ("start", "finish")),),
        (),
        (ContextFieldSpec("count", "int"),),
        ("start", "finish"),
    )
    run_id = uuid4()
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260930_08"
        create_run_snapshot(
            connection,
            run_id=run_id,
            dataset_revision_id="pg-dataset-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=DetectionAnalysisConfig(
                instrument_id="US30",
                calendar_id="demo-v1",
                patterns=(PatternSelection.from_definition(definition),),
            ),
            pattern_definitions={definition.identity: definition},
        )
        occurrence = create_pattern_instance(
            connection,
            run_id=run_id,
            definition=definition,
            binding_fingerprint="a" * 64,
            occurrence_event_time=start,
            occurrence_detection_time=start,
            occurrence_ordinal=0,
            at=start,
        )
        with pytest.raises(PatternInstanceError, match="already exists"):
            create_pattern_instance(
                connection,
                run_id=run_id,
                definition=definition,
                binding_fingerprint="a" * 64,
                occurrence_event_time=start,
                occurrence_detection_time=start,
                occurrence_ordinal=0,
                at=start,
            )
        at = start + timedelta(minutes=1)
        advanced = advance_pattern_instance(
            connection,
            occurrence.instance_id,
            definition,
            expected_revision=0,
            bar=Bar("US30", Timeframe.M1, at, Decimal(1), Decimal(2), Decimal(1), Decimal(2)),
            steps=(
                LifecycleStep(
                    "idle",
                    "candidate",
                    "start",
                    start,
                    at,
                    {"condition": "start", "source_ordinal": 0},
                ),
            ),
            context={"count": 3},
        )
        assert advanced.context["count"] == 3
        assert advanced.transitions[0].step.detection_time == at
        assert advanced.transitions[0].rationale == {
            "condition": "start",
            "source_ordinal": 0,
        }
        assert advanced.detector_events[0].event_id
        with pytest.raises(DatabaseError, match="immutable"):
            with connection.begin_nested():
                connection.execute(text(
                    "UPDATE pattern_instance_transitions SET rationale_json = '{}' "
                    "WHERE instance_id = :instance_id"
                ), {"instance_id": occurrence.instance_id})
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO pattern_instance_transitions "
                        "(instance_id, sequence, event_id, event_semantic_ref, "
                        "bar_time, event_time, "
                        "detection_time, from_state, to_state, trigger_id) "
                        "VALUES (:id, 2, :event_id, :ref, :bar, :event, :detection, 'candidate', "
                        "'completed', 'finish')"
                    ),
                    {
                        "id": occurrence.instance_id,
                        "event_id": str(uuid4()),
                        "ref": "b" * 64,
                        "bar": at,
                        "event": at,
                        "detection": at + timedelta(minutes=1),
                    },
                )
    with engine.connect() as connection:
        assert load_pattern_instance(connection, occurrence.instance_id, definition) == advanced
    engine.dispose()
