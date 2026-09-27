from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from alembic.config import Config
from market_analysis.application.logging import configure_logging
from market_analysis.config import (
    ComponentSelection,
    DetectionAnalysisConfig,
    EvaluationPlan,
    detection_config_hash,
)
from market_analysis.patterns import ParameterSpec, ParameterType
from market_analysis.persistence.runs import (
    create_run_snapshot,
    load_run_snapshot,
    metadata,
)


def test_explicit_migration_creates_schema_and_version(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "migration-test.db"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", f"sqlite:///{database_path}")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260927_01"
        assert connection.scalar(text("SELECT count(*) FROM run_snapshots")) == 0
    engine.dispose()


def test_replay_and_evaluation_snapshots_preserve_exact_resolved_config() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    plan = EvaluationPlan(
        detection_config_hash=detection_config_hash(config),
        context_schema_version="context-v1",
    )
    replay_id, evaluation_id = uuid4(), uuid4()
    with engine.begin() as connection:
        replay = create_run_snapshot(
            connection,
            run_id=replay_id,
            dataset_revision_id="demo-rev-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=config,
            preset_id="demo-preset",
            preset_revision=1,
        )
        evaluation = create_run_snapshot(
            connection,
            run_id=evaluation_id,
            dataset_revision_id="demo-rev-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=config,
            evaluation_plan=plan,
        )
        assert load_run_snapshot(connection, replay_id) == replay
        assert load_run_snapshot(connection, evaluation_id) == evaluation

    assert replay.run_kind == "replay"
    assert replay.detection_config_json == config.canonical_json()
    assert replay.evaluation_plan_json is None
    assert evaluation.run_kind == "evaluation"
    assert evaluation.detection_config_hash == replay.detection_config_hash
    assert evaluation.evaluation_plan_json == plan.canonical_json()


def test_snapshot_rejects_mismatched_plan_and_duplicate_run_id() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    wrong_plan = EvaluationPlan(
        detection_config_hash="not-the-config-hash",
        context_schema_version="context-v1",
    )
    run_id = uuid4()
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="different detection config"):
            create_run_snapshot(
                connection,
                run_id=run_id,
                dataset_revision_id="demo-rev-1",
                calendar_version="demo-v1",
                build_id="test-build",
                detection_config=config,
                evaluation_plan=wrong_plan,
            )
        values = dict(
            run_id=run_id,
            dataset_revision_id="demo-rev-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=config,
        )
        create_run_snapshot(connection, **values)
        with pytest.raises(IntegrityError):
            create_run_snapshot(connection, **values)


def test_snapshot_expands_registered_component_defaults_before_persisting() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    config = DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="demo-v1",
        components=(ComponentSelection(component_id="atr", component_version="1"),),
    )
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="unregistered component"):
            create_run_snapshot(
                connection,
                run_id=uuid4(),
                dataset_revision_id="demo-rev-1",
                calendar_version="demo-v1",
                build_id="test-build",
                detection_config=config,
            )
        record = create_run_snapshot(
            connection,
            run_id=uuid4(),
            dataset_revision_id="demo-rev-1",
            calendar_version="demo-v1",
            build_id="test-build",
            detection_config=config,
            component_parameters={
                ("atr", "1"): (ParameterSpec("period", ParameterType.INTEGER, 14),)
            },
        )
        assert '"name":"period"' in record.detection_config_json
        assert '"value":14' in record.detection_config_json


def test_snapshot_error_log_contains_run_context_without_changing_record(tmp_path) -> None:
    configure_logging(data_root=tmp_path)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    wrong_plan = EvaluationPlan(
        detection_config_hash="wrong",
        context_schema_version="context-v1",
    )
    run_id = uuid4()
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="different detection config"):
            create_run_snapshot(
                connection,
                run_id=run_id,
                dataset_revision_id="demo-rev-1",
                calendar_version="demo-v1",
                build_id="test-build",
                detection_config=config,
                evaluation_plan=wrong_plan,
            )
        assert load_run_snapshot(connection, run_id) is None
    records = (tmp_path / "logs" / "application.jsonl").read_text().splitlines()
    payload = json.loads(records[-1])
    assert payload["run_id"] == str(run_id)
    assert payload["instrument"] == "US30"
    assert payload["component"] == "run-snapshot"
    assert payload["severity"] == "ERROR"


def test_reduced_logging_does_not_change_persisted_snapshot(tmp_path) -> None:
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    run_id = uuid4()
    records = []
    for level in (logging.INFO, logging.CRITICAL):
        configure_logging(data_root=tmp_path, level=level, enable_file=False)
        engine = create_engine("sqlite://")
        metadata.create_all(engine)
        with engine.begin() as connection:
            saved = create_run_snapshot(
                connection,
                run_id=run_id,
                dataset_revision_id="demo-rev-1",
                calendar_version="demo-v1",
                build_id="test-build",
                detection_config=config,
            )
            assert load_run_snapshot(connection, run_id) == saved
            records.append(saved)
        engine.dispose()

    assert records[0] == records[1]
