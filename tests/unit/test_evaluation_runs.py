"""Immutable evaluation inputs, X-only population selection and honest completion."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DatabaseError

from alembic import command
from alembic.config import Config
from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.evaluation_runs import (
    complete_evaluation_if_ready,
    create_evaluation_run,
    evaluation_events,
    evaluation_state,
    read_evaluation_run,
    record_event_membership,
)
from market_analysis.application.event_sets import event_set_decision, field_types
from market_analysis.application.outcome_evaluation import OutcomeResult, evaluate_outcome
from market_analysis.application.replay_catalog import (
    PATTERN_DEFINITIONS,
    demo_detection_config,
    detector_bindings,
    resolve_replay_calendar,
)
from market_analysis.application.replay_event_store import ReplayEventStore
from market_analysis.code_version import CodeCaptureStatus, CodeVersion
from market_analysis.config import (
    ConfigParameter,
    EvaluationPlan,
    EventSetPredicate,
    EventSetSelection,
    OutcomeSelection,
    detection_config_hash,
    evaluation_plan_hash,
)
from market_analysis.demo.replay_seed import (
    DEMO_END,
    DEMO_SELECTED_START,
    DEMO_V2_END,
    DEMO_V2_SELECTED_START,
    seed_replay_datasets,
)
from market_analysis.detection import DetectorRuntime
from market_analysis.domain import Timeframe
from market_analysis.domain.aggregation import CandleAggregator
from market_analysis.domain.evaluation import (
    EvaluationError,
    EvaluationRequest,
    EvaluationStatus,
    RequestedEvaluationStream,
)
from market_analysis.domain.outcomes import OutcomeDefinition, OutputType, ResultStatus
from market_analysis.patterns import ParameterSpec, ParameterType
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluations import (
    advance_evaluation,
    advance_evaluation_stream,
    cancel_evaluation,
    claim_evaluation,
    evaluation_event_sets,
    evaluation_inputs,
    fail_evaluation,
    finish_detection,
)
from market_analysis.persistence.runs import metadata

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def clean_build(build):
    return CodeVersion(build, "a" * 40, False, CodeCaptureStatus.AVAILABLE, "fixture")


def fixture_outcome():
    return OutcomeDefinition(
        outcome_id="fixture",
        outcome_version="1",
        output_type=OutputType.NUMERIC,
        units="points",
        parameters=(
            ParameterSpec(
                "horizon",
                ParameterType.INTEGER,
                2,
                minimum=Decimal(1),
            ),
        ),
    )


def evaluation_storage(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'evaluations.sqlite'}"
    root = tmp_path / "data"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(root))
    engine = create_engine(url)
    metadata.create_all(engine)
    store = DatasetStore(root)
    with engine.begin() as connection:
        seed_replay_datasets(connection, store)
    yield engine, store
    engine.dispose()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    yield from evaluation_storage(tmp_path, monkeypatch)


def request(*, predicates=(), outcomes=(), timeframe=Timeframe.M1, multihour=False):
    config = demo_detection_config("US30").model_copy(update={"timeframe": timeframe})
    plan = EvaluationPlan(
        schema_version="evaluation-plan-v2",
        context_schema_version="detection-time-context-v1",
        detection_config_hash=detection_config_hash(
            config, pattern_definitions=PATTERN_DEFINITIONS
        ),
        outcomes=outcomes,
        event_set=EventSetSelection(predicates=predicates),
    )
    return EvaluationRequest(
        dataset_revision_id="offline-replay-us30-v3" if multihour else "offline-replay-us30-v1",
        instrument_id="US30",
        selected_start=DEMO_V2_SELECTED_START if multihour else DEMO_SELECTED_START,
        selected_end=DEMO_V2_END if multihour else DEMO_END,
        streams=(RequestedEvaluationStream(config=config, plan=plan),),
    )


def create(connection, store, selected=None, definitions=None):
    return create_evaluation_run(
        connection,
        selected or request(),
        store,
        build_id="fixture",
        created_at=NOW,
        revision_provider=clean_build,
        outcome_definitions=definitions or {},
    )


def drive_fixture(connection, store, record, *, select_members=True, stream_index=0):
    """Small integration harness for SCRUM-95; production autonomous driver is SCRUM-96."""
    token = claim_evaluation(connection, str(record.inputs.evaluation_id), NOW)
    stream = record.inputs.streams[stream_index]
    from market_analysis.config import DetectionAnalysisConfig

    config = DetectionAnalysisConfig.from_canonical_json(stream.config_json)
    calendar = resolve_replay_calendar(
        record.inputs.instrument_id, record.inputs.calendar_id, record.inputs.calendar_version
    )
    runtime = DetectorRuntime(
        config,
        detector_bindings(config),
        run_id=str(stream.run_id),
        dataset_revision_id=record.inputs.dataset_revision_id,
        calendar=calendar,
        pinned_calendar_version=calendar.version,
        pinned_config_hash=stream.detection_config_hash,
    )
    bridge = ReplayEventStore(PATTERN_DEFINITIONS)
    sequence = store.load_sequence(
        connection, record.inputs.dataset_revision_id, record.inputs.instrument_id, Timeframe.M1
    )
    aggregator = CandleAggregator(config.timeframe, calendar)
    emitted = []
    for bar in sequence.bars:
        if bar.timestamp >= record.inputs.selected_end:
            break
        for candle in aggregator.push(bar):
            result = runtime.process_bar(candle.bar)
            bridge(
                connection,
                candle.bar,
                result,
                runtime.binding_fingerprint,
                available_at=candle.available_at,
            )
            advance_evaluation_stream(
                connection,
                str(record.inputs.evaluation_id),
                str(stream.run_id),
                token,
                candle.available_at,
            )
            for source in result.events:
                identity = bridge.identity(source)[0]
                event = next(
                    item
                    for value in bridge._instances.values()
                    for item in value.detector_events
                    if item.event_id == identity
                )
                if select_members:
                    record_event_membership(
                        connection, record, event, emission_order=len(emitted), owner_token=token
                    )
                emitted.append(event)
    advance_evaluation(
        connection,
        str(record.inputs.evaluation_id),
        token,
        cursor_index=record.inputs.execution_bar_count - 1,
        available_at=record.inputs.selected_end,
        at=NOW,
    )
    return token, emitted, sequence


def test_exact_input_roundtrip_and_uuid_independent_identity(storage):
    engine, store = storage
    with engine.begin() as connection:
        first = create(connection, store)
        second = create(connection, store)
        assert first.inputs.evaluation_id != second.inputs.evaluation_id
        assert first.inputs.semantic_key == second.inputs.semantic_key
        assert first.inputs.event_set_id == second.inputs.event_set_id
        assert first.inputs.streams[0].run_id != second.inputs.streams[0].run_id
    with engine.connect() as connection:
        assert read_evaluation_run(connection, str(first.inputs.evaluation_id)) == first
        snapshot = first.inputs.streams[0]
        assert (
            EvaluationPlan.from_canonical_json(snapshot.plan_json).canonical_json()
            == snapshot.plan_json
        )
        assert first.status is EvaluationStatus.CREATED and first.cursor_index == -1
        assert first.inputs.code_dirty is False


def test_each_stream_must_finish_even_when_shared_source_cursor_reaches_end(storage):
    engine, store = storage
    selected = request(multihour=True)
    higher = request(timeframe=Timeframe.M5, multihour=True).streams[0]
    selected = selected.model_copy(update={"streams": (selected.streams[0], higher)})
    with engine.begin() as connection:
        record = create(connection, store, selected)
        assert [item.timeframe for item in record.inputs.streams] == [Timeframe.M1, Timeframe.M5]
        token, _, _ = drive_fixture(connection, store, record)
        with pytest.raises(EvaluationError, match="analytical stream"):
            finish_detection(connection, str(record.inputs.evaluation_id), token, NOW)
        assert read_evaluation_run(connection, str(record.inputs.evaluation_id)).status is (
            EvaluationStatus.RUNNING
        )


def test_copied_detector_set_expands_without_mutable_tab_or_policy_state(storage):
    from test_detector_runtime import component_selections

    from market_analysis.config import PatternSelection
    from market_analysis.detection import CONTINUATION_V1, REVERSAL_V1

    engine, store = storage
    selected = request()
    original = selected.streams[0]
    config = original.config.model_copy(
        update={
            "components": tuple(
                next(
                    value
                    for value in original.config.components
                    if value.component_id == "range_state"
                )
                if item.component_id == "range_state"
                else item.model_copy(
                    update={"parameters": (ConfigParameter(name="period", value=2),)}
                )
                if item.component_id == "ema"
                else item
                for item in component_selections()
            ),
            "patterns": (
                *original.config.patterns,
                PatternSelection.from_definition(CONTINUATION_V1),
                PatternSelection.from_definition(REVERSAL_V1),
            ),
        }
    )
    plan = original.plan.model_copy(
        update={
            "detection_config_hash": detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            ),
            "event_set": EventSetSelection(
                predicates=(
                    EventSetPredicate(
                        field="context.detector.context.reclaim_at",
                        operator="IS_UNAVAILABLE",
                    ),
                )
            ),
        }
    )
    selected = selected.model_copy(
        update={"streams": (RequestedEvaluationStream(config=config, plan=plan),)}
    )
    with engine.begin() as connection:
        record = create(connection, store, selected)
        assert len(record.inputs.streams) == 3
        assert len(record.inputs.original_configs) == 1
        stream_index = next(
            index
            for index, item in enumerate(record.inputs.streams)
            if item.pattern_id == original.config.patterns[0].pattern_id
        )
        _, events, _ = drive_fixture(connection, store, record, stream_index=stream_index)
        assert events
        copied = selected.model_copy(update={"streams": ()})
        assert copied.streams == () and len(record.inputs.streams) == 3


def test_native_and_context_predicates_select_hand_reviewed_population(storage):
    engine, store = storage
    filters = (
        EventSetPredicate(field="event.event_kind", value="ACTIVE"),
        EventSetPredicate(field="context.detector.state", value="ACTIVE"),
    )
    with engine.begin() as connection:
        record = create(connection, store, request(predicates=filters))
        token, events, _ = drive_fixture(connection, store, record)
        assert [item.event_kind for item in events] == ["CANDIDATE", "ACTIVE", "COMPLETED"]
        members = (
            connection.execute(
                select(evaluation_event_sets)
                .where(
                    evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
                )
                .order_by(evaluation_event_sets.c.emission_order)
            )
            .mappings()
            .all()
        )
        assert [item["eligible"] for item in members] == [0, 1, 0]
        assert "selected detection interval" in members[0]["exclusion_reason"]
        finish_detection(connection, str(record.inputs.evaluation_id), token, NOW)
        assert complete_evaluation_if_ready(connection, str(record.inputs.evaluation_id), NOW)
        assert read_evaluation_run(connection, str(record.inputs.evaluation_id)).status is (
            EvaluationStatus.COMPLETED
        )


def test_snapshot_is_required_before_population_filter(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
        token, events, _ = drive_fixture(connection, store, record, select_members=False)
        connection.execute(
            text("DELETE FROM detection_context_snapshots WHERE event_id=:event"),
            {"event": events[1].event_id},
        )
        with pytest.raises(EvaluationError, match="before context capture"):
            record_event_membership(
                connection, record, events[1], emission_order=0, owner_token=token
            )


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("NE", Decimal(0), False),
        ("EQ", None, True),
        ("NE", None, False),
        ("IS_UNAVAILABLE", None, True),
    ],
)
def test_unavailable_numeric_context_is_not_treated_as_a_nonzero_measurement(
    storage,
    operator,
    value,
    expected,
):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
        _, events, _ = drive_fixture(connection, store, record, select_members=False)
        snapshot = load_detection_context(connection, events[1].event_id)
        components = dict(snapshot.values["components"])
        components["range_state"] = {**components["range_state"], "compression_score": None}
        snapshot = replace(snapshot, values={**snapshot.values, "components": components})
        selected = request(
            predicates=(
                EventSetPredicate(
                    field="context.components.range_state.compression_score",
                    operator=operator,
                    value=value,
                ),
            )
        )
        decision, _ = event_set_decision(
            events[1],
            snapshot,
            selected.streams[0].plan,
            selected_start=DEMO_SELECTED_START,
            selected_end=DEMO_END,
            fields=field_types([selected.streams[0].config], PATTERN_DEFINITIONS),
        )
        assert decision is expected


@pytest.mark.parametrize(
    "field",
    [
        "outcomes.return",
        "context.future_label",
        "event.rationale.outcome",
        "context.components.range_state.future_score",
    ],
)
def test_primary_population_cannot_filter_on_future_or_unapproved_data(storage, field):
    engine, store = storage
    with engine.begin() as connection, pytest.raises(ValueError, match="not approved"):
        create(connection, store, request(predicates=(EventSetPredicate(field=field, value=1),)))
    with engine.connect() as connection:
        assert connection.scalar(select(text("COUNT(*)")).select_from(evaluation_inputs)) == 0


@pytest.mark.parametrize("mutation", ["source", "detector", "context", "outcome", "warmup"])
def test_invalid_references_fail_preflight_without_partial_runs(storage, mutation):
    engine, store = storage
    selected = request()
    if mutation == "source":
        selected = selected.model_copy(update={"dataset_revision_id": "missing-revision"})
    elif mutation == "warmup":
        selected = selected.model_copy(
            update={"selected_start": DEMO_SELECTED_START - timedelta(minutes=10)}
        )
    else:
        stream = selected.streams[0]
        if mutation == "context":
            stream = stream.model_copy(
                update={
                    "plan": stream.plan.model_copy(
                        update={
                            "context_schema_version": "future-schema",
                        }
                    )
                }
            )
        elif mutation == "outcome":
            stream = stream.model_copy(
                update={
                    "plan": stream.plan.model_copy(
                        update={
                            "outcomes": (
                                OutcomeSelection(outcome_id="missing", outcome_version="1"),
                            ),
                        }
                    )
                }
            )
        else:
            from market_analysis.config import PatternSelection

            stream = stream.model_copy(
                update={
                    "config": stream.config.model_copy(
                        update={
                            "patterns": (
                                PatternSelection(pattern_id="missing", pattern_version="1"),
                            ),
                        }
                    )
                }
            )
        selected = selected.model_copy(update={"streams": (stream,)})
    with engine.begin() as connection, pytest.raises(ValueError):
        create(connection, store, selected)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM evaluation_inputs")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM run_snapshots")) == 0


def test_pending_outcomes_block_full_completion_and_do_not_define_population(storage):
    engine, store = storage
    definition = fixture_outcome()
    selected = request(outcomes=(OutcomeSelection(outcome_id="fixture", outcome_version="1"),))
    with engine.begin() as connection:
        record = create(connection, store, selected, {definition.identity: definition})
        token, events, sequence = drive_fixture(connection, store, record)
        finish_detection(connection, str(record.inputs.evaluation_id), token, NOW)
        assert not complete_evaluation_if_ready(connection, str(record.inputs.evaluation_id), NOW)
        assert read_evaluation_run(connection, str(record.inputs.evaluation_id)).status is (
            EvaluationStatus.DETECTION_COMPLETED
        )
        calendar = resolve_replay_calendar(
            record.inputs.instrument_id, record.inputs.calendar_id, record.inputs.calendar_version
        )
        for event in events[1:]:
            evaluate_outcome(
                connection,
                event=event,
                pattern=PATTERN_DEFINITIONS[(event.pattern_id, event.pattern_version)],
                definition=definition,
                definitions={definition.identity: definition},
                bar_sequence=sequence,
                calendar=calendar,
                revision_provider=clean_build,
                algorithm=lambda source: OutcomeResult(
                    ResultStatus.AVAILABLE,
                    source.bars[-1].close - source.window.reference_price,
                ),
            )
        assert complete_evaluation_if_ready(connection, str(record.inputs.evaluation_id), NOW)
        result = evaluation_events(connection, str(record.inputs.evaluation_id))
        assert result["complete"]
        assert len(result["events"]) == 2


def test_horizon_changes_plan_identity_but_not_detection_or_event_set(storage):
    engine, store = storage
    definition = fixture_outcome()
    with engine.begin() as connection:
        first = create(
            connection,
            store,
            request(
                outcomes=(
                    OutcomeSelection(
                        outcome_id="fixture",
                        outcome_version="1",
                    ),
                )
            ),
            {definition.identity: definition},
        )
        second = create(
            connection,
            store,
            request(
                outcomes=(
                    OutcomeSelection(
                        outcome_id="fixture",
                        outcome_version="1",
                        parameters=(ConfigParameter(name="horizon", value=3),),
                    ),
                )
            ),
            {definition.identity: definition},
        )
        assert (
            first.inputs.streams[0].detection_config_hash
            == second.inputs.streams[0].detection_config_hash
        )
        assert (
            first.inputs.streams[0].evaluation_plan_hash
            != second.inputs.streams[0].evaluation_plan_hash
        )
        assert first.inputs.event_set_id == second.inputs.event_set_id
        assert first.inputs.semantic_key != second.inputs.semantic_key


def test_v1_hash_compatibility_and_v2_typed_filter_roundtrip(storage):
    engine, store = storage
    first = request(
        predicates=(
            EventSetPredicate(
                field="context.components.range_state.choppiness_score",
                operator="GTE",
                value=Decimal("60.00"),
            ),
            EventSetPredicate(field="event.event_kind", value="ACTIVE"),
        )
    )
    second = first.streams[0].plan.model_copy(
        update={
            "event_set": EventSetSelection(
                predicates=tuple(reversed(first.streams[0].plan.event_set.predicates)),
            )
        }
    )
    assert evaluation_plan_hash(first.streams[0].plan) == evaluation_plan_hash(second)
    assert (
        EvaluationPlan.from_canonical_json(second.canonical_json()).canonical_json()
        == second.canonical_json()
    )
    legacy = EvaluationPlan(detection_config_hash="a" * 64, context_schema_version="ctx")
    assert "event_set" not in legacy.canonical_dict()
    with engine.begin() as connection:
        record = create(connection, store, first)
        assert '"value":"60","value_type":"decimal"' in record.inputs.streams[0].plan_json


def test_only_one_execution_owner_and_failed_output_never_completes(storage):
    engine, store = storage
    with engine.begin() as connection:
        first, second = create(connection, store), create(connection, store)
        token = claim_evaluation(connection, str(first.inputs.evaluation_id), NOW)
        with pytest.raises(EvaluationError, match="another evaluation"):
            claim_evaluation(connection, str(second.inputs.evaluation_id), NOW)
        with pytest.raises(EvaluationError, match="pinned end"):
            finish_detection(connection, str(first.inputs.evaluation_id), token, NOW)
        fail_evaluation(connection, str(first.inputs.evaluation_id), token, "forced failure", NOW)
        with pytest.raises(EvaluationError, match="required detection stages"):
            complete_evaluation_if_ready(connection, str(first.inputs.evaluation_id), NOW)
        assert claim_evaluation(connection, str(second.inputs.evaluation_id), NOW)
        state = evaluation_state(connection, str(first.inputs.evaluation_id))
        assert state["status"] == "FAILED" and state["completed_at"] is None


def test_cancelled_execution_releases_slot_but_cannot_complete(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
        token = claim_evaluation(connection, str(record.inputs.evaluation_id), NOW)
        cancel_evaluation(connection, str(record.inputs.evaluation_id), token, NOW)
        with pytest.raises(EvaluationError, match="required detection stages"):
            complete_evaluation_if_ready(connection, str(record.inputs.evaluation_id), NOW)
        state = evaluation_state(connection, str(record.inputs.evaluation_id))
        assert state["status"] == "CANCELLED" and state["completed_at"] is None


def test_tampered_event_and_dirty_build_are_rejected(storage):
    engine, store = storage
    with engine.begin() as connection:
        record = create(connection, store)
        token, events, _ = drive_fixture(connection, store, record, select_members=False)
        with pytest.raises(EvaluationError, match="persisted detector event"):
            record_event_membership(
                connection,
                record,
                replace(events[1], event_kind="FAKE"),
                emission_order=0,
                owner_token=token,
            )
    with engine.begin() as connection, pytest.raises(EvaluationError, match="clean committed"):
        create_evaluation_run(
            connection,
            request(),
            store,
            build_id="fixture",
            created_at=NOW,
            revision_provider=lambda build: replace(clean_build(build), dirty=True),
        )


def test_api_submission_is_frozen_and_readable_without_a_session(storage, monkeypatch):
    from market_analysis.api.app import app

    engine, store = storage
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "false")
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    with TestClient(app) as client:
        response = client.post("/evaluation-runs", json=request().model_dump(mode="json"))
        assert response.status_code == 201, response.text
        state = response.json()
        assert state["status"] == "CREATED"
        identity = state["evaluation_id"]
        assert client.get(f"/evaluation-runs/{identity}").json() == state
        assert client.get(f"/evaluation-runs/{identity}/events").json()["events"] == []
        assert client.get(f"/evaluation-runs/{identity}/events?limit=501").status_code == 422
        assert client.get("/evaluation-runs/not-a-uuid").status_code == 422
    with engine.connect() as connection:
        assert read_evaluation_run(connection, identity).status is EvaluationStatus.CREATED


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_migrated_inputs_and_membership_are_immutable(tmp_path, monkeypatch, backend):
    url = f"sqlite:///{tmp_path / 'migrated-evaluation.sqlite'}"
    if backend == "postgres":
        url = os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL")
        if not url:
            pytest.skip("PostgreSQL integration URL is not configured")
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    with engine.begin() as connection:
        seed_replay_datasets(connection, store)
        record = create(connection, store)
        token, _, _ = drive_fixture(connection, store, record)
        finish_detection(connection, str(record.inputs.evaluation_id), token, NOW)
        complete_evaluation_if_ready(connection, str(record.inputs.evaluation_id), NOW)
    for table in ("evaluation_inputs", "evaluation_streams", "evaluation_event_sets"):
        for statement in (
            f"UPDATE {table} SET evaluation_id=evaluation_id",
            f"DELETE FROM {table}",
        ):
            with engine.begin() as connection, pytest.raises(DatabaseError, match="immutable"):
                connection.execute(text(statement))
    with pytest.raises(RuntimeError, match="evaluation lineage must be retained"):
        command.downgrade(Config("alembic.ini"), "20261106_17")
    with engine.connect() as connection:
        assert read_evaluation_run(connection, str(record.inputs.evaluation_id)) is not None
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_postgres_simultaneous_claims_allow_one_execution(tmp_path, monkeypatch):
    url = os.environ["STREAM_ANALYSIS_TEST_DATABASE_URL"]
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    store = DatasetStore(tmp_path / "data")
    with engine.begin() as connection:
        seed_replay_datasets(connection, store)
        records = [create(connection, store), create(connection, store)]
    barrier = Barrier(2)

    def claim(record):
        barrier.wait(timeout=10)
        try:
            with engine.begin() as connection:
                token = claim_evaluation(connection, str(record.inputs.evaluation_id), NOW)
            return record, token
        except EvaluationError as error:
            assert "another evaluation" in str(error)
            return record, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, records))
    winners = [(record, token) for record, token in results if token]
    assert len(winners) == 1
    with engine.begin() as connection:
        winner, token = winners[0]
        fail_evaluation(connection, str(winner.inputs.evaluation_id), token, "fixture cleanup", NOW)
        loser = next(record for record, token in results if token is None)
        token = claim_evaluation(connection, str(loser.inputs.evaluation_id), NOW)
        fail_evaluation(connection, str(loser.inputs.evaluation_id), token, "fixture cleanup", NOW)
    engine.dispose()
