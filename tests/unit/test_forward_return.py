"""Hand-calculated forward returns and real persisted evaluation integration."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
import test_evaluation_scheduler as queue_tests
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_evaluation_runs import clean_build, request
from test_outcomes import setup_source

from market_analysis.application.evaluation_runs import create_evaluation_run, evaluation_events
from market_analysis.application.evaluation_scheduler import (
    cancel_job,
    enqueue_evaluation,
    run_scheduler_once,
)
from market_analysis.application.forward_return import (
    DEFAULT_HORIZONS,
    FORWARD_RETURN,
    FORWARD_RETURN_PERCENT,
    default_forward_return_selections,
    forward_return,
    forward_return_percent,
)
from market_analysis.application.outcome_evaluation import OutcomeInput, evaluate_outcome
from market_analysis.application.outcome_registry import OUTCOME_DEFINITIONS
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS, resolve_replay_calendar
from market_analysis.config import (
    ConfigParameter,
    EvaluationPlan,
    OutcomeSelection,
    evaluation_plan_hash,
    resolve_evaluation_plan,
)
from market_analysis.detection.records import freeze_mapping
from market_analysis.domain import Bar, Timeframe
from market_analysis.domain.outcomes import ObservationWindow, ReferenceMode, ResultStatus
from market_analysis.persistence.evaluation_jobs import JobStatus
from market_analysis.persistence.outcomes import load_outcome_observation, outcome_observations
from market_analysis.persistence.pattern_instances import load_pattern_instance
from market_analysis.persistence.runs import metadata
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage
MINUTE = timedelta(minutes=1)


def source(
    *,
    percent=False,
    horizon=5,
    reference="100",
    last="110",
    directional=False,
    pattern="EMA_RECLAIM_STRUCTURE_CONTINUATION_V1",
    direction="UP",
    candidate="DOWN",
    available=True,
):
    definition = FORWARD_RETURN_PERCENT if percent else FORWARD_RETURN
    selection = definition.resolve(
        OutcomeSelection(
            outcome_id=definition.outcome_id,
            outcome_version="1",
            parameters=(
                ConfigParameter(name="horizon", value=horizon),
                ConfigParameter(name="directional", value=directional),
            ),
        )
    )
    at = datetime(2026, 1, 5, 12, 1, tzinfo=UTC)
    bars = tuple(
        Bar("US30", Timeframe.M1, at + index * MINUTE, last, last, last, last)
        for index in range(horizon)
    )
    return OutcomeInput(
        ObservationWindow(
            reference_mode=ReferenceMode.FROM_DETECTION_TIME,
            reference_time=at,
            end=at + horizon * MINUTE,
            horizon_bars=horizon,
            observed_bars=horizon,
            reference_price=Decimal(reference),
        ),
        bars,
        (),
        freeze_mapping(
            {
                "detector": {
                    "pattern_id": pattern,
                    "pattern_version": "1",
                    "context_status": "AVAILABLE" if available else "UNAVAILABLE",
                    "context": {"source_direction": direction, "candidate_direction": candidate},
                },
                "components": {"later_direction": "DOWN"},
            }
        ),
        selection,
    )


@pytest.mark.parametrize("percent", [False, True])
@pytest.mark.parametrize(
    "last,points,ratio", [("110", "10", "0.1"), ("90", "-10", "-0.1"), ("100", "0", "0")]
)
def test_exact_positive_negative_and_zero(percent, last, points, ratio):
    result = (forward_return_percent if percent else forward_return)(
        source(percent=percent, last=last)
    )
    assert result.status is ResultStatus.AVAILABLE
    assert result.value == Decimal(ratio if percent else points)


@pytest.mark.parametrize("horizon", DEFAULT_HORIZONS)
def test_default_horizons_use_the_last_close_and_exclude_earlier_extremes(horizon):
    value = source(horizon=horizon, last="103")
    bars = list(value.bars)
    bars[-2] = replace(
        bars[-2], open=Decimal("999"), high=Decimal("999"), low=Decimal("999"), close=Decimal("999")
    )
    assert forward_return(replace(value, bars=tuple(bars))).value == Decimal("3")


@pytest.mark.parametrize(
    "pattern,direction,candidate,expected",
    [
        ("EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", "UP", "DOWN", "10"),
        ("EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", "DOWN", "UP", "-10"),
        ("EMA_STRUCTURE_REVERSAL_V1", "UP", "DOWN", "-10"),
        ("EMA_STRUCTURE_REVERSAL_V1", "DOWN", "UP", "10"),
    ],
)
def test_direction_uses_the_captured_pattern_contract(pattern, direction, candidate, expected):
    value = source(pattern=pattern, direction=direction, candidate=candidate, directional=True)
    assert forward_return(value).value == Decimal(expected)
    changed = dict(value.context)
    changed["components"] = {"later_direction": "UP", "later_atr": "999"}
    assert forward_return(replace(value, context=freeze_mapping(changed))).value == Decimal(
        expected
    )


@pytest.mark.parametrize(
    "kwargs", [{"pattern": "COMPRESSION"}, {"available": False}, {"direction": "UNKNOWN"}]
)
def test_direction_unavailable_does_not_invent_sign_or_invalidate_raw_return(kwargs):
    assert forward_return(source(**kwargs)).value == Decimal("10")
    result = forward_return(source(directional=True, **kwargs))
    assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None


def test_percentage_zero_reference_and_fixed_precision():
    assert (
        forward_return_percent(source(percent=True, reference="0")).status
        is ResultStatus.NOT_APPLICABLE
    )
    with localcontext() as context:
        context.prec = 5
        result = forward_return_percent(source(percent=True, reference="3", last="4"))
        assert result.value == Decimal("0.3333333333333333333333333333")
        assert forward_return(
            source(
                reference="1000000000000000000000000000000.0000001",
                last="1000000000000000000000000000000.0000002",
            )
        ).value == (Decimal("0.0000001"))


def test_multi_configuration_plan_identity_and_duplicate_default_resolution():
    selections = default_forward_return_selections()
    specs = {key: item.parameters for key, item in OUTCOME_DEFINITIONS.items()}
    plan = EvaluationPlan(
        schema_version="evaluation-plan-v2",
        detection_config_hash="a" * 64,
        context_schema_version="detection-time-context-v1",
        outcomes=selections,
    )
    reverse = plan.model_copy(update={"outcomes": tuple(reversed(selections))})
    assert evaluation_plan_hash(plan, outcome_parameters=specs) == evaluation_plan_hash(
        reverse, outcome_parameters=specs
    )
    assert (
        EvaluationPlan.from_canonical_json(plan.canonical_json()).canonical_json()
        == plan.canonical_json()
    )
    with pytest.raises(ValueError, match="outcome values must be unique"):
        resolve_evaluation_plan(
            plan.model_copy(
                update={
                    "outcomes": (
                        selections[0],
                        OutcomeSelection(outcome_id="FORWARD_RETURN", outcome_version="1"),
                    )
                }
            ),
            outcome_parameters=specs,
        )
    with pytest.raises(ValueError, match="outcome values must be unique"):
        EvaluationPlan(
            detection_config_hash="a" * 64, context_schema_version="ctx", outcomes=selections
        )


@pytest.mark.parametrize("horizon", DEFAULT_HORIZONS)
def test_real_context_sql_window_and_roundtrip(tmp_path, horizon):
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(
                connection,
                FORWARD_RETURN,
                future=60,
                selection_values=(ConfigParameter(name="horizon", value=horizon),),
            )
            result = evaluate_outcome(connection, **arguments, algorithm=forward_return)
            assert result.status is ResultStatus.AVAILABLE
            assert result.value == Decimal(10 * horizon)
            assert result.window.reference_price == Decimal(150)
            assert result.lineage.context_snapshot_id == snapshot.snapshot_id
            assert load_outcome_observation(connection, result.observation_id) == result
            assert evaluate_outcome(connection, **arguments, algorithm=forward_return) == result
    finally:
        engine.dispose()


def create_forward(engine, store, *, early=False):
    selections = (
        default_forward_return_selections()
        + default_forward_return_selections(percent=True)
        + default_forward_return_selections(directional=True)
    )
    selected = request(multihour=True, outcomes=selections)
    if early:
        selected = selected.model_copy(update={"selected_end": selected.selected_end - 90 * MINUTE})
    with atomic_transaction(engine) as connection:
        record = create_evaluation_run(
            connection,
            selected,
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        job = enqueue_evaluation(connection, str(record.inputs.evaluation_id), queue_tests.now())
    return record, job


@pytest.mark.parametrize("early", [False, True])
def test_real_queue_executes_all_horizons_and_preserves_honest_statuses(storage, early):
    engine, store = storage
    record, job = create_forward(engine, store, early=early)
    queue_tests.resume(engine)
    final = run_scheduler_once(engine, store, revision_provider=clean_build)
    assert final.status is JobStatus.COMPLETED and final.job_id == job.job_id
    with engine.connect() as connection:
        events = evaluation_events(connection, str(record.inputs.evaluation_id))["events"]
        assert events
        sequence = store.load_sequence(
            connection, record.inputs.dataset_revision_id, "US30", Timeframe.M1
        )
        index = {bar.timestamp: i for i, bar in enumerate(sequence.bars)}
        statuses = set()
        for event in events:
            assert len(event["outcomes"]) == 12
            reference = Decimal(event["context"]["values"]["bar"]["close"])
            for outcome in event["outcomes"]:
                statuses.add(outcome["status"])
                parameters = {p["name"]: p["value"] for p in outcome["configuration"]["parameters"]}
                if outcome["status"] == "AVAILABLE":
                    last = sequence.bars[index[event["available_at"]] + parameters["horizon"] - 1]
                    expected = last.close - reference
                    if outcome["outcome_id"] == FORWARD_RETURN_PERCENT.outcome_id:
                        expected /= reference
                    assert Decimal(outcome["value"]) == expected
                else:
                    assert outcome["value"] is None and outcome["reason"]
        assert "AVAILABLE" in statuses and "NOT_APPLICABLE" in statuses
        if early:
            assert "INSUFFICIENT_DATA" not in statuses


def test_cancellation_during_outcomes_preserves_only_committed_observations(storage, monkeypatch):
    engine, store = storage
    record, job = create_forward(engine, store)
    monkeypatch.setattr("market_analysis.application.evaluation_outcomes.OUTCOME_BATCH_SIZE", 1)
    queue_tests.resume(engine)
    commits = 0

    def committed():
        nonlocal commits
        commits += 1
        if commits == 2:  # one detector chunk (all 300 bars), then one outcome batch
            with atomic_transaction(engine) as connection:
                cancel_job(connection, job.job_id, queue_tests.now())

    final = run_scheduler_once(
        engine, store, chunk_size=2000, revision_provider=clean_build, on_commit=committed
    )
    assert final.status is JobStatus.CANCELLED
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outcome_observations)) == 1
        assert not evaluation_events(connection, str(record.inputs.evaluation_id))["complete"]


def test_registered_catalog_is_available_without_running_an_analysis(storage):
    from market_analysis.api.app import app

    with TestClient(app) as client:
        response = client.get("/outcome-definitions")
        assert response.status_code == 200
        assert response.json()["default_horizons"] == [5, 15, 30, 60]
        assert {"FORWARD_RETURN", "FORWARD_RETURN_PERCENT"} <= {
            item["outcome_id"] for item in response.json()["definitions"]
        }


@pytest.mark.parametrize("future,missing", [(4, ()), (60, (18,))])
def test_forward_return_does_not_extend_a_short_or_gapped_window(future, missing):
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, _ = setup_source(connection, FORWARD_RETURN, future=future, missing=missing)
            observation = evaluate_outcome(connection, **arguments, algorithm=forward_return)
            assert observation.status is ResultStatus.INSUFFICIENT_DATA
            assert observation.value is None and observation.reason
    finally:
        engine.dispose()


def test_higher_timeframe_forward_window_begins_at_source_close():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(connection, FORWARD_RETURN, timeframe=Timeframe.M5)
            observation = evaluate_outcome(connection, **arguments, algorithm=forward_return)
            end_label = snapshot.available_at + 4 * MINUTE
            close = next(
                bar.close for bar in arguments["bar_sequence"].bars if bar.timestamp == end_label
            )
            assert observation.value == close - Decimal(snapshot.values["bar"]["close"])
            assert observation.window.reference_time == snapshot.available_at
            assert observation.window.reference_time > snapshot.detection_time
    finally:
        engine.dispose()


def test_exact_selection_is_required_and_recalculation_is_idempotent(storage):
    engine, store = storage
    record, _ = create_forward(engine, store)
    queue_tests.resume(engine)
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )
    with engine.begin() as connection:
        row = evaluation_events(connection, str(record.inputs.evaluation_id))["events"][0]
        stream = record.inputs.streams[0]
        pattern = PATTERN_DEFINITIONS[(stream.pattern_id, stream.pattern_version)]
        occurrence = load_pattern_instance(connection, row["instance_id"], pattern)
        event = next(
            item for item in occurrence.detector_events if item.event_id == row["event_id"]
        )
        arguments = dict(
            event=event,
            pattern=pattern,
            definition=FORWARD_RETURN,
            definitions=OUTCOME_DEFINITIONS,
            algorithm=forward_return,
            revision_provider=clean_build,
            bar_sequence=store.load_sequence(
                connection, record.inputs.dataset_revision_id, "US30", Timeframe.M1
            ),
            calendar=resolve_replay_calendar(
                "US30", record.inputs.calendar_id, record.inputs.calendar_version
            ),
        )
        with pytest.raises(ValueError, match="exact outcome configuration"):
            evaluate_outcome(connection, **arguments)
        invalid = OutcomeSelection(
            outcome_id="FORWARD_RETURN",
            outcome_version="1",
            parameters=(ConfigParameter(name="horizon", value=1),),
        )
        with pytest.raises(ValueError, match="not selected"):
            evaluate_outcome(connection, **arguments, selection=invalid)
        result = evaluate_outcome(
            connection, **arguments, selection=default_forward_return_selections()[0]
        )
        assert load_outcome_observation(connection, result.observation_id) == result


def test_postgres_registered_forward_returns_execute_and_roundtrip(
    isolated_postgres_url, tmp_path, monkeypatch
):
    from sqlalchemy import create_engine

    from alembic import command
    from alembic.config import Config
    from market_analysis.demo.replay_seed import seed_replay_datasets
    from market_analysis.persistence.dataset_store import DatasetStore

    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", isolated_postgres_url)
    monkeypatch.setenv("STREAM_ANALYSIS_BUILD_ID", "fixture")
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "false")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(isolated_postgres_url)
    store = DatasetStore(tmp_path / "data")
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
        test_real_queue_executes_all_horizons_and_preserves_honest_statuses((engine, store), True)
        with engine.connect() as connection:
            identifiers = connection.scalars(select(outcome_observations.c.observation_id)).all()
            assert identifiers
            for identifier in identifiers:
                observation = load_outcome_observation(connection, identifier)
                assert observation is not None
                assert observation.lineage.context_snapshot_id
    finally:
        engine.dispose()


def test_failure_during_outcomes_retains_committed_prefix(storage, monkeypatch):
    import market_analysis.application.evaluation_outcomes as stage

    engine, store = storage
    record, _ = create_forward(engine, store)
    monkeypatch.setattr(stage, "OUTCOME_BATCH_SIZE", 1)
    real_evaluate = stage.evaluate_outcome
    count = 0

    def failing(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("outcome fixture failed")
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(stage, "evaluate_outcome", failing)
    queue_tests.resume(engine)
    final = run_scheduler_once(engine, store, revision_provider=clean_build)
    assert final.status is JobStatus.FAILED
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(outcome_observations)) == 1
        result = evaluation_events(connection, str(record.inputs.evaluation_id))
        assert not result["complete"] and result["events"]
