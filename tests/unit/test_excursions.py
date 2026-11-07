"""Hand-calculated extrema and captured-basis downstream integration."""

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
from market_analysis.application.excursions import ALGORITHMS, DEFINITIONS, excursion
from market_analysis.application.outcome_evaluation import evaluate_outcome
from market_analysis.config import ConfigParameter, OutcomeSelection
from market_analysis.detection.records import freeze_mapping
from market_analysis.domain.outcomes import ResultStatus
from market_analysis.persistence.evaluation_jobs import JobStatus
from market_analysis.persistence.outcomes import load_outcome_observation
from market_analysis.persistence.runs import metadata
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage
BY_ID = {definition.outcome_id: definition for definition in DEFINITIONS}


def excursion_source(
    kind="MFE", *, normalized=False, atr="4", ready=True, instance="atr", **kwargs
):
    result = source(**kwargs)
    definition = BY_ID[f"{kind}_ATR" if normalized else kind]
    parameters = (ConfigParameter(name="horizon", value=result.window.horizon_bars),)
    if normalized:
        parameters += (ConfigParameter(name="atr_instance_id", value=instance),)
    context = dict(result.context)
    context["components"] = {
        "atr": {"component_id": "atr", "instance_id": "atr", "value_ready": ready, "atr": atr},
        "other_atr": {
            "component_id": "atr",
            "instance_id": "other_atr",
            "value_ready": True,
            "atr": "999",
        },
    }
    bars = tuple(
        replace(
            bar, high=Decimal("112"), low=Decimal("94"), open=Decimal("100"), close=Decimal("100")
        )
        for bar in result.bars
    )
    # Last required future bar supplies the largest extrema; never use only close.
    bars = (*bars[:-1], replace(bars[-1], high=Decimal("120"), low=Decimal("90")))
    return replace(
        result,
        bars=bars,
        context=freeze_mapping(context),
        configuration=definition.resolve(
            OutcomeSelection(
                outcome_id=definition.outcome_id, outcome_version="1", parameters=parameters
            )
        ),
    )


@pytest.mark.parametrize(
    "kind,up,down",
    [
        ("MFE", "20", "10"),
        ("MAE", "-10", "-20"),
        ("UP_EXCURSION", "20", "20"),
        ("DOWN_EXCURSION", "10", "10"),
    ],
)
@pytest.mark.parametrize("direction", ["UP", "DOWN"])
@pytest.mark.parametrize("normalized", [False, True])
def test_hand_calculated_bullish_bearish_extrema(kind, up, down, direction, normalized):
    result = excursion(
        excursion_source(kind, normalized=normalized, direction=direction),
        kind=kind,
        normalized=normalized,
    )
    expected = Decimal(up if direction == "UP" else down)
    assert result.status is ResultStatus.AVAILABLE
    assert result.value == (expected / 4 if normalized else expected)


@pytest.mark.parametrize("kind", ["MFE", "MAE", "UP_EXCURSION", "DOWN_EXCURSION"])
def test_directionless_raw_values_never_invent_direction(kind):
    result = excursion(excursion_source(kind, pattern="COMPRESSION"), kind=kind)
    if kind in ("MFE", "MAE"):
        assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None
    else:
        assert result.value == Decimal("20" if kind == "UP_EXCURSION" else "10")


@pytest.mark.parametrize("atr", [None, "0", "-1", "NaN", "Infinity", "bad", True])
def test_invalid_captured_atr_only_invalidates_normalized_observation(atr):
    assert excursion(excursion_source(atr=atr), kind="MFE").value == Decimal("20")
    result = excursion(excursion_source(normalized=True, atr=atr), kind="MFE", normalized=True)
    assert result.status is ResultStatus.NOT_APPLICABLE and result.value is None


@pytest.mark.parametrize("kwargs", [{"ready": False}, {"instance": "missing"}])
def test_unready_or_absent_selected_atr_does_not_fall_back_to_other_instance(kwargs):
    result = excursion(excursion_source(normalized=True, **kwargs), kind="MFE", normalized=True)
    assert result.status is ResultStatus.NOT_APPLICABLE


def test_selected_atr_instance_and_later_state_independence():
    value = excursion_source(normalized=True)
    assert excursion(value, kind="MFE", normalized=True).value == Decimal("5")
    changed = dict(value.context)
    components = {key: dict(item) for key, item in changed["components"].items()}
    components["other_atr"]["atr"] = "1"
    changed["components"] = components
    changed["later_state"] = {"atr": "1000", "direction": "DOWN"}
    assert excursion(
        replace(value, context=freeze_mapping(changed)), kind="MFE", normalized=True
    ).value == Decimal("5")
    selected = excursion_source(normalized=True, instance="other_atr")
    with localcontext() as context:
        context.prec = 4
        assert excursion(selected, kind="MFE", normalized=True).value == Decimal(
            "0.02002002002002002002002002002"
        )


def test_reversal_uses_candidate_direction_not_previous_source_move():
    value = excursion_source(pattern="EMA_STRUCTURE_REVERSAL_V1", direction="UP", candidate="DOWN")
    assert excursion(value, kind="MFE").value == Decimal("10")


@pytest.mark.parametrize("kind", ["MFE", "MAE", "UP_EXCURSION", "DOWN_EXCURSION"])
@pytest.mark.parametrize("direction", ["UP", "DOWN"])
def test_flat_window_and_signed_formulas_are_not_floored(kind, direction):
    value = excursion_source(kind, direction=direction)
    flat = tuple(
        replace(
            bar, open=Decimal("100"), high=Decimal("100"), low=Decimal("100"), close=Decimal("100")
        )
        for bar in value.bars
    )
    assert excursion(replace(value, bars=flat), kind=kind).value == Decimal(0)
    above = tuple(
        replace(
            bar, open=Decimal("110"), high=Decimal("115"), low=Decimal("105"), close=Decimal("110")
        )
        for bar in value.bars
    )
    expected = {
        "MFE": "15" if direction == "UP" else "-5",
        "MAE": "5" if direction == "UP" else "-15",
        "UP_EXCURSION": "15",
        "DOWN_EXCURSION": "-5",
    }
    assert excursion(replace(value, bars=above), kind=kind).value == Decimal(expected[kind])


@pytest.mark.parametrize("future,missing", [(4, ()), (60, (18,))])
def test_excursion_incomplete_windows_are_insufficient(future, missing):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, _ = setup_source(connection, BY_ID["MFE"], future=future, missing=missing)
            observation = evaluate_outcome(
                connection, **arguments, algorithm=ALGORITHMS[("MFE", "1")]
            )
            assert observation.status is ResultStatus.INSUFFICIENT_DATA
    finally:
        engine.dispose()


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda value: value.outcome_id)
def test_real_captured_context_sql_formula_and_roundtrip(definition):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(connection, definition, future=5)

            def bounded_algorithm(value):
                assert len(value.bars) == 5
                assert value.bars[0].timestamp == snapshot.available_at
                assert value.bars[-1].timestamp == snapshot.available_at + timedelta(minutes=4)
                assert all(bar.timestamp > snapshot.detection_time for bar in value.bars)
                return ALGORITHMS[definition.identity](value)

            observation = evaluate_outcome(connection, **arguments, algorithm=bounded_algorithm)
            raw = {
                "MFE": Decimal(55),
                "MAE": Decimal(-10),
                "UP_EXCURSION": Decimal(55),
                "DOWN_EXCURSION": Decimal(10),
            }
            kind = definition.outcome_id.removesuffix("_ATR")
            expected = raw[kind]
            if definition.outcome_id.endswith("_ATR"):
                atr = Decimal(snapshot.values["components"]["atr"]["atr"])
                expected /= atr
            assert observation.status is ResultStatus.AVAILABLE
            assert observation.value == expected
            assert load_outcome_observation(connection, observation.observation_id) == observation
            assert (
                evaluate_outcome(connection, **arguments, algorithm=ALGORITHMS[definition.identity])
                == observation
            )
    finally:
        engine.dispose()


def run_all_excursions(engine, store):
    selections = tuple(
        OutcomeSelection(outcome_id=item.outcome_id, outcome_version="1") for item in DEFINITIONS
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
        assert events and all(len(event["outcomes"]) == 8 for event in events)
        assert any(
            result["status"] == "AVAILABLE" for event in events for result in event["outcomes"]
        )
        for event in events:
            observations = {item["outcome_id"]: item for item in event["outcomes"]}
            for kind in ("MFE", "MAE", "UP_EXCURSION", "DOWN_EXCURSION"):
                raw, normalized = observations[kind], observations[f"{kind}_ATR"]
                assert raw["status"] == normalized["status"]
                if raw["status"] == "AVAILABLE":
                    atr = Decimal(event["context"]["values"]["components"]["atr"]["atr"])
                    assert Decimal(normalized["value"]) == Decimal(raw["value"]) / atr
                assert BY_ID[kind].units == "points"
                assert BY_ID[f"{kind}_ATR"].units == "atr_multiple"


def test_real_queue_executes_every_excursion(storage):
    run_all_excursions(*storage)


def test_postgres_queue_executes_every_excursion(isolated_postgres_url, tmp_path, monkeypatch):
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
        run_all_excursions(engine, store)
    finally:
        engine.dispose()


def test_excursion_catalog_exposes_fixed_units_and_explicit_atr_instance(monkeypatch):
    from fastapi.testclient import TestClient

    from market_analysis.api.app import app

    monkeypatch.delenv("STREAM_ANALYSIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    with TestClient(app) as client:
        response = client.get("/outcome-definitions")
        assert response.status_code == 200
        catalog = {item["outcome_id"]: item for item in response.json()["definitions"]}
        for definition in DEFINITIONS:
            item = catalog[definition.outcome_id]
            assert item["units"] == definition.units
            assert item["reference_mode"] == "FROM_DETECTION_TIME"
            if definition.outcome_id.endswith("_ATR"):
                assert any(
                    parameter["parameter_id"] == "atr_instance_id" and parameter["default"] == "atr"
                    for parameter in item["parameters"]
                )
