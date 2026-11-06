"""Detection-time X is causal, phase-specific and durable across later processing."""

import json
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DatabaseError
from test_detector_runtime import bar, chain_series, config_with, pattern_selection

from alembic import command
from alembic.config import Config
from market_analysis.api.app import app
from market_analysis.application.context_capture import (
    capture_detection_context,
    load_detection_context,
)
from market_analysis.application.replay_event_store import ReplayEventStore
from market_analysis.config import EvaluationPlan, OutcomeSelection, detection_config_hash
from market_analysis.detection import (
    CONTINUATION_V1,
    ContinuationDetector,
    DetectorBinding,
    DetectorOutput,
    DetectorRecordError,
    DetectorRuntime,
    TransitionIntent,
)
from market_analysis.detection.context_snapshots import (
    CONTEXT_SCHEMA_VERSION,
    ContextSnapshotError,
    capture_values,
)
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    PatternDefinition,
    TransitionSpec,
)
from market_analysis.persistence.detection_contexts import detection_context_snapshots
from market_analysis.persistence.runs import create_run_snapshot, metadata


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def drive(
    connection, *, outcome=None, through_confirmation=True, schema=CONTEXT_SCHEMA_VERSION,
):
    definition = CONTINUATION_V1
    definitions = {definition.identity: definition}
    config = config_with([pattern_selection(definition)])
    run_id = uuid4()
    plan = None if outcome is None else EvaluationPlan(
        detection_config_hash=detection_config_hash(config, pattern_definitions=definitions),
        context_schema_version=schema,
        outcomes=(OutcomeSelection(outcome_id=outcome, outcome_version="1"),),
    )
    create_run_snapshot(
        connection, run_id=run_id, dataset_revision_id="context-fixture",
        calendar_version="demo-v1", build_id="test-build", detection_config=config,
        evaluation_plan=plan, pattern_definitions=definitions,
        outcome_parameters=None if outcome is None else {(outcome, "1"): ()},
    )
    runtime = DetectorRuntime(
        config, [DetectorBinding(definition, ContinuationDetector(), reentrant=True)],
        run_id=str(run_id), dataset_revision_id="context-fixture",
    )
    store = ReplayEventStore(definitions)
    series = (*chain_series(), bar(15, "150", "160", "119")) if (
        through_confirmation
    ) else chain_series()
    snapshots, results = [], []
    for candle in series:
        result = runtime.process_bar(candle)
        store(connection, candle, result, runtime.binding_fingerprint)
        results.append(result)
        for event in result.events:
            snapshot = load_detection_context(connection, store.identity(event)[0])
            assert snapshot is not None
            snapshots.append(snapshot)
    return runtime, store, results, snapshots


def test_exact_context_phase_references_and_future_processing(engine):
    with engine.begin() as connection:
        runtime, store, results, snapshots = drive(connection)
        assert len(snapshots) == 3
        candidate, reclaimed, confirmed = snapshots
        assert [item.values["detector"]["state"] for item in snapshots] == [
            "CANDIDATE", "RECLAIMED", "CONFIRMED",
        ]
        assert [item.within_bar_ordinal for item in snapshots] == [0, 0, 1]
        assert reclaimed.values["detector"]["context"]["reclaim_at"] == (
            confirmed.values["detector"]["context"]["reclaim_at"]
        )
        assert "reclaim_at" not in candidate.values["detector"]["context"]
        refs = confirmed.values["detector"]["rationale"]["source_market_event_refs"]
        assert len(refs) == 1
        assert refs[0] in {item["semantic_ref"] for item in confirmed.values["market_events"]}
        assert confirmed.values["components"]["trend_leg"]["active_leg"] is not None
        assert "protected_swing" in str(confirmed.values["components"]["trend_leg"])
        assert confirmed.values["optional_components"]["range_state"] == "PRESENT"
        assert confirmed.available_at == confirmed.detection_time + timedelta(minutes=1)
        original = candidate.content_hash
        for index in range(16, 25):
            result = runtime.process_bar(bar(index))
            store(connection, result.frame.bar, result, runtime.binding_fingerprint)
        assert load_detection_context(connection, candidate.event_id) == candidate
        assert candidate.content_hash == original
        with pytest.raises(TypeError):
            candidate.values["detector"]["context"]["source_direction"] = "FUTURE"
        with pytest.raises(FrozenInstanceError):
            candidate.context_schema_version = "future"
        restored = load_detection_context(connection, confirmed.event_id)
        assert restored == confirmed
        assert restored.lineage["dataset_revision_id"] == "context-fixture"
        assert restored.lineage["pattern_version"] == CONTINUATION_V1.pattern_version
        assert restored.lineage["component_versions"] == results[-1].frame.component_versions


def test_outcome_only_plans_and_run_ids_do_not_change_x(engine):
    with engine.begin() as connection:
        first = drive(connection, outcome="return")[3]
        second = drive(connection, outcome="adverse_excursion")[3]
        assert [item.values for item in first] == [item.values for item in second]
        assert [item.semantic_hash for item in first] == [item.semantic_hash for item in second]
        assert first[0].run_id != second[0].run_id
        assert first[0].snapshot_id != second[0].snapshot_id
        assert "evaluation_plan_hash" not in first[0].lineage


@pytest.mark.parametrize("target", ["component", "context", "evidence"])
def test_future_only_values_are_rejected(engine, target):
    with engine.begin() as connection:
        _, _, results, _ = drive(connection)
        result = results[-1]
        frame, event, instance = result.frame, result.events[0], result.event_instances[0]
        future = event.detection_time + timedelta(minutes=1)
        if target == "component":
            components = dict(frame.components)
            components["trend_leg"] = {**components["trend_leg"], "active_leg": {
                "detection_time": future,
            }}
            frame = replace(frame, components=components)
        elif target == "context":
            instance = replace(instance, context={**instance.context, "reclaim_at": future})
        else:
            event = replace(event, rationale={"typed_value": {
                "type": "datetime", "value": future.isoformat(),
            }})
        with pytest.raises(ContextSnapshotError, match="future observation"):
            capture_values(frame, event, instance, CONTINUATION_V1)


def test_schema_source_phase_and_value_type_validation(engine):
    with engine.begin() as connection:
        _, _, results, snapshots = drive(connection)
        snapshot = snapshots[-1]
        with pytest.raises(ContextSnapshotError, match="schema_version"):
            replace(snapshot, context_schema_version="unknown")
        with pytest.raises(ContextSnapshotError, match="value fields"):
            replace(snapshot, values={**snapshot.values, "outcome": 42})
        with pytest.raises(ContextSnapshotError, match="unsupported context value"):
            replace(snapshot, values={**snapshot.values, "bar": object()})
        @dataclass(frozen=True)
        class UIState:
            selection: str
        with pytest.raises(ContextSnapshotError, match="unsupported context value"):
            replace(snapshot, values={**snapshot.values, "bar": UIState("chart")})
        result = results[-1]
        with pytest.raises(ContextSnapshotError, match="emitting frame"):
            capture_values(
                replace(result.frame, dataset_revision_id="other"), result.events[0],
                result.event_instances[0], CONTINUATION_V1,
            )
        with pytest.raises(ContextSnapshotError, match="phase"):
            capture_values(
                result.frame, result.events[0], result.event_instances[1], CONTINUATION_V1,
            )
        with pytest.raises(ContextSnapshotError, match="component version"):
            capture_values(
                replace(result.frame, component_versions={
                    **result.frame.component_versions, "ema": "2",
                }), result.events[0], result.event_instances[0], CONTINUATION_V1,
            )
        unavailable = capture_values(result.frame, result.events[0], None, CONTINUATION_V1)
        assert unavailable["detector"]["context_status"] == "UNAVAILABLE"
        assert unavailable["detector"]["context"] is None
        missing = capture_values(
            replace(result.frame, components={}, availability={}), result.events[0],
            result.event_instances[0], CONTINUATION_V1,
        )
        assert set(missing["optional_components"].values()) == {"NOT_CONFIGURED"}
        with pytest.raises(DetectorRecordError, match="align"):
            DetectorOutput(result.instances[0], (), ({},))


def test_idempotent_capture_conflicts_and_source_binding(engine):
    with engine.begin() as connection:
        _, store, results, snapshots = drive(connection)
        result = results[-1]
        event = result.events[-1]
        stored = next(
            item for record in store._instances.values() for item in record.detector_events
            if item.event_id == snapshots[-1].event_id
        )
        arguments = dict(
            frame=result.frame, event=event, instance=result.event_instances[-1],
            stored=stored, definition=CONTINUATION_V1, available_at=snapshots[-1].available_at,
        )
        assert capture_detection_context(connection, **arguments) == snapshots[-1]
        with pytest.raises(ContextSnapshotError, match="already captured"):
            capture_detection_context(connection, **{**arguments, "available_at": (
                snapshots[-1].available_at + timedelta(minutes=1)
            )})
        with pytest.raises(ContextSnapshotError, match="persisted detector event"):
            capture_detection_context(connection, **{**arguments, "stored": replace(
                stored, new_state="UNRELATED",
            )})


def test_corrupt_payload_is_not_returned(engine):
    with engine.begin() as connection:
        snapshot = drive(connection)[3][0]
        connection.execute(detection_context_snapshots.update().values(content_hash="0" * 64))
        with pytest.raises(ContextSnapshotError, match="invalid stored"):
            load_detection_context(connection, snapshot.event_id)


def test_unsupported_plan_schema_rolls_back_event_and_context_together(engine):
    with pytest.raises(ContextSnapshotError, match="unsupported context_schema_version"):
        with engine.begin() as connection:
            drive(connection, outcome="return", schema="future-schema")
    with engine.connect() as connection:
        for name in ("run_snapshots", "pattern_instances", "pattern_instance_transitions",
                     "detection_context_snapshots"):
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {name}")) == 0


def test_migration_guards_and_durable_api(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'contexts.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        snapshot = drive(connection)[3][-1]
    with engine.connect() as connection:
        assert load_detection_context(connection, snapshot.event_id) == snapshot
        assert connection.scalar(select(detection_context_snapshots.c.context_schema_version)) == (
            CONTEXT_SCHEMA_VERSION
        )
    for operation in ("UPDATE detection_context_snapshots SET content_hash = 'changed'",
                      "DELETE FROM detection_context_snapshots"):
        with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(operation))
    with pytest.raises(RuntimeError, match="must be retained"):
        command.downgrade(Config("alembic.ini"), "20261105_15")
    with TestClient(app) as client:
        response = client.get(f"/detector-events/{snapshot.event_id}/context")
        assert response.status_code == 200
        assert response.json() == json.loads(json.dumps(snapshot.to_dict()))
        assert client.get(f"/detector-events/{uuid4()}/context").status_code == 404
        assert client.get("/detector-events/not-a-uuid/context").status_code == 422
    engine.dispose()


def test_unused_migration_can_be_downgraded(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'unused.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    command.downgrade(Config("alembic.ini"), "20261105_15")
    command.upgrade(Config("alembic.ini"), "head")


def test_distinct_intermediate_contexts_and_exact_minimal_fixture(engine):
    definition = PatternDefinition(
        "phase-test", "1", "Phase", "fixture", (), (), (),
        ("idle", "candidate", "confirmed"),
        (TransitionSpec("idle", "candidate", "open"),
         TransitionSpec("candidate", "confirmed", "confirm")),
        (ConditionGroup("evidence", ("open", "confirm")),), (),
        (ContextFieldSpec("count", "int"),), ("open", "confirm"),
        same_bar_chains=(("open", "confirm"),),
    )

    class TwoTransitions:
        def process_bar(self, source):
            def intent(old, new, trigger):
                return TransitionIntent(
                    "phase-test", "1", source.instance.instance_id, old, new, trigger,
                    source.detection_time, source.detection_time, {"condition": trigger},
                )
            return DetectorOutput(
                replace(source.instance, state="confirmed", context={"count": 2}),
                (intent("idle", "candidate", "open"),
                 intent("candidate", "confirmed", "confirm")),
                ({"count": 1}, {"count": 2}),
            )

        def reset(self):
            pass

    from market_analysis.config import DetectionAnalysisConfig, PatternSelection

    config = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal-v1",
        patterns=(PatternSelection.from_definition(definition),),
    )
    definitions = {definition.identity: definition}
    run_id = uuid4()
    with engine.begin() as connection:
        create_run_snapshot(
            connection, run_id=run_id, dataset_revision_id="phase-fixture",
            calendar_version="cal-v1", build_id="test-build", detection_config=config,
            pattern_definitions=definitions,
        )
        runtime = DetectorRuntime(
            config, [DetectorBinding(definition, TwoTransitions())],
            run_id=str(run_id), dataset_revision_id="phase-fixture",
        )
        result = runtime.process_bar(bar(0))
        store = ReplayEventStore(definitions)
        store(connection, bar(0), result, runtime.binding_fingerprint)
        first = load_detection_context(connection, store.identity(result.events[0])[0])
        second = load_detection_context(connection, store.identity(result.events[1])[0])
        assert first.values["detector"]["context"] == {"count": 1}
        assert second.values["detector"]["context"] == {"count": 2}
        assert first.to_dict()["values"] == {
            "bar": dict(bar(0).to_canonical_dict()), "completed_bars": 1,
            "components": {}, "availability": {},
            "optional_components": {
                family: "NOT_CONFIGURED" for family in (
                    "session", "ema", "atr", "swing_point", "swing_structure",
                    "trend_leg", "trend_leg_qualification", "range_state",
                )
            },
            "market_events": [],
            "detector": {
                "pattern_id": "phase-test", "pattern_version": "1", "from_state": "idle",
                "state": "candidate", "trigger_id": "open", "sequence": 0,
                "context_status": "AVAILABLE", "context": {"count": 1},
                "rationale": {"condition": "open"},
            },
        }
