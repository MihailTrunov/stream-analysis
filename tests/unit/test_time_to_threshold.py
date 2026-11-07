"""Hand-calculated first touches, frozen threshold bases and durable worker results."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
import test_evaluation_scheduler as queue_tests
from sqlalchemy import create_engine
from test_evaluation_runs import clean_build, request
from test_forward_return import source
from test_outcomes import setup_source

from market_analysis.application.evaluation_runs import create_evaluation_run, evaluation_events
from market_analysis.application.evaluation_scheduler import enqueue_evaluation, run_scheduler_once
from market_analysis.application.outcome_evaluation import evaluate_outcome
from market_analysis.application.time_to_threshold import ALGORITHMS, DEFINITIONS, time_to_threshold
from market_analysis.config import ConfigParameter, OutcomeSelection
from market_analysis.detection.records import freeze_mapping
from market_analysis.domain import Timeframe
from market_analysis.domain.outcomes import ResultStatus
from market_analysis.persistence.evaluation_jobs import JobStatus
from market_analysis.persistence.outcomes import load_outcome_observation
from market_analysis.persistence.runs import metadata
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage
BY_KIND = dict(zip(("CATEGORY", "BARS", "SECONDS"), DEFINITIONS, strict=True))


def selection(kind="CATEGORY", **parameters):
    definition = BY_KIND[kind]
    return OutcomeSelection(
        outcome_id=definition.outcome_id,
        outcome_version="1",
        parameters=tuple(
            ConfigParameter(name=name, value=value) for name, value in parameters.items()
        ),
    )


def threshold_source(
    *, kind="CATEGORY", ranges=None, parameters=None, atr="4", ready=True, **kwargs
):
    value = source(**kwargs)
    ranges = ranges or [("104", "96")] * len(value.bars)
    bars = tuple(
        replace(bar, open=Decimal(hi), high=Decimal(hi), low=Decimal(lo), close=Decimal(lo))
        for bar, (hi, lo) in zip(value.bars, ranges, strict=True)
    )
    context = dict(value.context)
    context["components"] = {
        "atr": {"component_id": "atr", "instance_id": "atr", "value_ready": ready, "atr": atr},
        "other_atr": {
            "component_id": "atr",
            "instance_id": "other_atr",
            "value_ready": True,
            "atr": "999",
        },
    }
    config = dict(horizon=len(bars), positive_threshold=Decimal(10), negative_threshold=Decimal(10))
    config.update(parameters or {})
    return replace(
        value,
        bars=bars,
        context=freeze_mapping(context),
        configuration=BY_KIND[kind].resolve(selection(kind, **config)),
    )


POSITIVE = [("104", "96"), ("110", "95"), ("115", "90"), ("104", "96"), ("104", "96")]
NEGATIVE = [("104", "96"), ("109", "90"), ("110", "85"), ("104", "96"), ("104", "96")]
SAME = [("104", "96"), ("110", "90"), ("104", "96"), ("104", "96"), ("104", "96")]


@pytest.mark.parametrize(
    "ranges,category",
    [
        (POSITIVE, "POSITIVE_FIRST"),
        (NEGATIVE, "NEGATIVE_FIRST"),
        ([("110", "96")] * 5, "POSITIVE_FIRST"),
        ([("104", "90")] * 5, "NEGATIVE_FIRST"),
        ([("104", "96")] * 5, "NOT_REACHED"),
    ],
)
def test_hand_calculated_first_hit_and_neither(ranges, category):
    result = time_to_threshold(threshold_source(ranges=ranges))
    assert result.status is ResultStatus.AVAILABLE and result.value == category


@pytest.mark.parametrize(
    "kind,side,expected",
    [
        ("BARS", "POSITIVE", "2"),
        ("BARS", "NEGATIVE", "3"),
        ("SECONDS", "POSITIVE", "120"),
        ("SECONDS", "NEGATIVE", "180"),
    ],
)
def test_exact_per_side_bar_and_observable_time_offsets(kind, side, expected):
    result = time_to_threshold(
        threshold_source(kind=kind, ranges=POSITIVE, parameters={"side": side}), kind=kind
    )
    assert result.status is ResultStatus.AVAILABLE and result.value == Decimal(expected)


def test_same_bar_ambiguity_preserves_known_individual_offsets():
    result = time_to_threshold(threshold_source(ranges=SAME))
    assert result.status is ResultStatus.AMBIGUOUS and result.value is None
    assert "SAME_BAR_AMBIGUOUS" in result.reason
    for side in ("POSITIVE", "NEGATIVE"):
        assert time_to_threshold(
            threshold_source(kind="BARS", ranges=SAME, parameters={"side": side}), kind="BARS"
        ).value == Decimal(2)


def test_later_dual_touch_does_not_override_first_side():
    assert time_to_threshold(threshold_source(ranges=POSITIVE)).value == "POSITIVE_FIRST"


@pytest.mark.parametrize(
    "direction,candidate,pattern,expected",
    [
        ("DOWN", "UP", "EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", "NEGATIVE_FIRST"),
        ("UP", "DOWN", "EMA_STRUCTURE_REVERSAL_V1", "NEGATIVE_FIRST"),
    ],
)
def test_captured_event_direction_including_reversal(direction, candidate, pattern, expected):
    value = threshold_source(
        ranges=POSITIVE, direction=direction, candidate=candidate, pattern=pattern
    )
    assert time_to_threshold(value).value == expected
    changed = dict(value.context)
    changed["later_state"] = {"direction": "UP", "atr": "1000"}
    assert time_to_threshold(replace(value, context=freeze_mapping(changed))) == time_to_threshold(
        value
    )


@pytest.mark.parametrize("kind", ["CATEGORY", "BARS", "SECONDS"])
def test_directionless_requires_explicit_price_axes(kind):
    value = threshold_source(kind=kind, ranges=POSITIVE, pattern="COMPRESSION")
    assert time_to_threshold(value, kind=kind).status is ResultStatus.NOT_APPLICABLE
    value = threshold_source(
        kind=kind, ranges=POSITIVE, pattern="COMPRESSION", parameters={"basis": "UP_DOWN"}
    )
    assert time_to_threshold(value, kind=kind).value == (
        "UP_FIRST" if kind == "CATEGORY" else Decimal(2 if kind == "BARS" else 120)
    )


def test_explicit_down_first_and_final_bar_inclusivity():
    assert (
        time_to_threshold(threshold_source(ranges=NEGATIVE, parameters={"basis": "UP_DOWN"})).value
        == "DOWN_FIRST"
    )
    last = [("104", "96")] * 4 + [("110", "95")]
    assert time_to_threshold(
        threshold_source(kind="BARS", ranges=last), kind="BARS"
    ).value == Decimal(5)
    assert time_to_threshold(
        threshold_source(kind="SECONDS", ranges=last), kind="SECONDS"
    ).value == Decimal(300)


@pytest.mark.parametrize("kind", ["BARS", "SECONDS"])
def test_numeric_not_reached_is_unavailable_without_zero_fill(kind):
    result = time_to_threshold(threshold_source(kind=kind), kind=kind)
    assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None
    assert "NOT_REACHED" in result.reason


def test_atr_and_mixed_units_use_captured_selected_atr():
    parameters = {"positive_units": "ATR", "positive_threshold": Decimal("2.5")}
    assert (
        time_to_threshold(threshold_source(ranges=POSITIVE, parameters=parameters)).value
        == "POSITIVE_FIRST"
    )
    parameters["atr_instance_id"] = "other_atr"
    assert (
        time_to_threshold(threshold_source(ranges=POSITIVE, parameters=parameters)).value
        == "NEGATIVE_FIRST"
    )
    parameters["atr_instance_id"] = "missing"
    assert (
        time_to_threshold(threshold_source(ranges=POSITIVE, parameters=parameters)).status
        is ResultStatus.NOT_APPLICABLE
    )


@pytest.mark.parametrize(
    "atr,ready",
    [(None, True), ("0", True), ("-1", True), ("NaN", True), ("bad", True), ("4", False)],
)
def test_invalid_atr_does_not_invalidate_point_thresholds(atr, ready):
    assert (
        time_to_threshold(threshold_source(ranges=POSITIVE, atr=atr, ready=ready)).value
        == "POSITIVE_FIRST"
    )
    result = time_to_threshold(
        threshold_source(
            ranges=POSITIVE, atr=atr, ready=ready, parameters={"positive_units": "ATR"}
        )
    )
    assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None
    # A point-side offset remains independently knowable when only the other side needs ATR.
    result = time_to_threshold(
        threshold_source(
            kind="BARS", ranges=POSITIVE, atr=atr, ready=ready, parameters={"negative_units": "ATR"}
        ),
        kind="BARS",
    )
    assert result.value == Decimal(2)


def test_threshold_products_and_comparisons_ignore_ambient_decimal_precision():
    value = threshold_source(
        ranges=[("100.01", "99.99")] * 4 + [("100.15241578750190521", "99.99")],
        atr="1.23456789",
        parameters={"positive_units": "ATR", "positive_threshold": Decimal("0.123456789")},
    )
    with localcontext() as context:
        context.prec = 4
        assert time_to_threshold(value).value == "POSITIVE_FIRST"
    before = list(value.bars)
    before[-1] = replace(before[-1], open=Decimal("100"), high=Decimal("100.15241578750190520"))
    assert time_to_threshold(replace(value, bars=tuple(before))).value == "NOT_REACHED"


def test_elapsed_seconds_keep_closed_periods_while_bar_offset_counts_future_candles():
    value = threshold_source(kind="SECONDS", ranges=POSITIVE)
    bars = tuple(
        replace(bar, timestamp=bar.timestamp + (timedelta(hours=1) if i else timedelta(0)))
        for i, bar in enumerate(value.bars)
    )
    value = replace(value, bars=bars)
    assert time_to_threshold(value, kind="SECONDS").value == Decimal(3720)


@pytest.mark.parametrize("future,missing", [(4, ()), (60, (18,))])
def test_complete_window_required_even_when_first_candle_touches_both(future, missing):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, _ = setup_source(
                connection, BY_KIND["CATEGORY"], future=future, missing=missing
            )
            observation = evaluate_outcome(
                connection, **arguments, algorithm=ALGORITHMS[BY_KIND["CATEGORY"].identity]
            )
            assert observation.status is ResultStatus.INSUFFICIENT_DATA
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["CATEGORY", "BARS", "SECONDS"])
@pytest.mark.parametrize("timeframe", [Timeframe.M1, Timeframe.M5])
def test_sql_roundtrip_idempotence_source_exclusion_and_actual_availability(kind, timeframe):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    definition = BY_KIND[kind]
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(
                connection,
                definition,
                future=5,
                timeframe=timeframe,
                selection_values=(ConfigParameter(name="basis", value="UP_DOWN"),),
            )
            observation = evaluate_outcome(
                connection, **arguments, algorithm=ALGORITHMS[definition.identity]
            )
            assert observation.window.reference_time == snapshot.available_at
            assert observation.status in (ResultStatus.AVAILABLE, ResultStatus.AMBIGUOUS)
            assert load_outcome_observation(connection, observation.observation_id) == observation
            assert (
                evaluate_outcome(connection, **arguments, algorithm=ALGORITHMS[definition.identity])
                == observation
            )
            if kind == "BARS":
                assert observation.value == Decimal(1)
            if kind == "SECONDS":
                assert observation.value == Decimal(60)
    finally:
        engine.dispose()


def run_thresholds(engine, store):
    selections = (
        selection(basis="UP_DOWN"),
        selection("BARS", basis="UP_DOWN"),
        selection("BARS", basis="UP_DOWN", side="NEGATIVE"),
        selection("SECONDS", basis="UP_DOWN"),
        selection("SECONDS", basis="UP_DOWN", side="NEGATIVE"),
        selection(
            basis="UP_DOWN", positive_threshold=Decimal(100000), negative_threshold=Decimal(100000)
        ),
    )
    with atomic_transaction(engine) as connection:
        record = create_evaluation_run(
            connection,
            request(multihour=True, outcomes=selections),
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        enqueue_evaluation(connection, str(record.inputs.evaluation_id), queue_tests.now())
    queue_tests.resume(engine)
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )
    with engine.connect() as connection:
        events = evaluation_events(connection, str(record.inputs.evaluation_id))["events"]
        assert events and all(len(event["outcomes"]) == 6 for event in events)
        statuses = {observation["status"] for event in events for observation in event["outcomes"]}
        assert "AVAILABLE" in statuses
        for event in events:
            for observation in event["outcomes"]:
                if observation["status"] != "AVAILABLE":
                    assert observation["value"] is None and observation["reason"]
                assert (
                    observation["lineage"]["source_event_semantic_ref"]
                    == event["event_semantic_ref"]
                )


def test_real_queued_threshold_execution(storage):
    run_thresholds(*storage)


def test_postgres_real_queued_threshold_execution(isolated_postgres_url, tmp_path, monkeypatch):
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
        run_thresholds(engine, store)
    finally:
        engine.dispose()


def test_threshold_catalog_is_read_only_without_database(monkeypatch):
    from fastapi.testclient import TestClient

    from market_analysis.api.app import app

    monkeypatch.delenv("STREAM_ANALYSIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    with TestClient(app) as client:
        response = client.get("/outcome-definitions")
        assert response.status_code == 200
        catalog = {item["outcome_id"]: item for item in response.json()["definitions"]}
        for definition in DEFINITIONS:
            assert catalog[definition.outcome_id] == definition.model_dump(mode="json")


@pytest.mark.parametrize(
    "parameters",
    [
        {"positive_threshold": Decimal(-1)},
        {"negative_threshold": Decimal(-1)},
        {"positive_units": "PIPS"},
        {"basis": "GUESSED"},
        {"horizon": 0},
    ],
)
def test_invalid_threshold_selection_rejected_before_run_persistence(storage, parameters):
    from sqlalchemy import func, select

    from market_analysis.persistence.evaluations import evaluation_inputs

    engine, store = storage
    with atomic_transaction(engine) as connection:
        before = connection.scalar(select(func.count()).select_from(evaluation_inputs))
        with pytest.raises(ValueError):
            create_evaluation_run(
                connection,
                request(outcomes=(selection(**parameters),)),
                store,
                build_id="fixture",
                created_at=queue_tests.now(),
                revision_provider=clean_build,
            )
        assert connection.scalar(select(func.count()).select_from(evaluation_inputs)) == before


def test_resolved_threshold_defaults_and_changed_basis_have_distinct_plan_identities():
    from market_analysis.application.outcome_registry import OUTCOME_DEFINITIONS
    from market_analysis.config import EvaluationPlan, evaluation_plan_hash

    specs = {key: definition.parameters for key, definition in OUTCOME_DEFINITIONS.items()}
    plan = EvaluationPlan(
        schema_version="evaluation-plan-v2",
        detection_config_hash="a" * 64,
        context_schema_version="detection-time-context-v1",
        outcomes=(selection(),),
    )
    explicit = plan.model_copy(update={"outcomes": (BY_KIND["CATEGORY"].resolve(selection()),)})
    assert evaluation_plan_hash(plan, outcome_parameters=specs) == evaluation_plan_hash(
        explicit, outcome_parameters=specs
    )
    variants = [
        selection(basis="UP_DOWN"),
        selection(positive_units="ATR"),
        selection(positive_threshold=Decimal(2)),
        selection(atr_instance_id="other_atr"),
    ]
    hashes = {evaluation_plan_hash(plan, outcome_parameters=specs)}
    hashes.update(
        evaluation_plan_hash(
            plan.model_copy(update={"outcomes": (variant,)}), outcome_parameters=specs
        )
        for variant in variants
    )
    assert len(hashes) == 5


def test_zero_magnitude_is_an_explicit_reference_price_touch():
    result = time_to_threshold(
        threshold_source(
            parameters={"positive_threshold": Decimal(0), "negative_threshold": Decimal(0)}
        )
    )
    assert result.status is ResultStatus.AMBIGUOUS
    assert "SAME_BAR_AMBIGUOUS" in result.reason
