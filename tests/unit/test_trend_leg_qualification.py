from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from test_trend_leg import START, bar, build, initial, point, update
from test_trend_leg import config as structural_config

from market_analysis.api.app import app
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.domain import Timeframe
from market_analysis.indicators import (
    AtrState,
    EmaState,
    MarketStateError,
    QualificationStatus,
    SwingPointState,
    SwingStructureState,
    TrendLegQualificationState,
    TrendLegState,
    TrendLegTransitionType,
)
from market_analysis.patterns import ParameterSpec, ParameterType, PatternDefinitionError


def config(**overrides: object) -> DetectionAnalysisConfig:
    base = structural_config()
    return resolve_detection_config(
        base.model_copy(
            update={
                "components": base.components
                + (
                    ComponentSelection(
                        component_id="trend_leg_qualification",
                        component_version="1",
                        parameters=tuple(
                            ConfigParameter(name=name, value=cast(Any, value))
                            for name, value in overrides.items()
                        ),
                    ),
                ),
            }
        )
    )


def observer(chain: Any, cfg: DetectionAnalysisConfig) -> TrendLegQualificationState:
    return TrendLegQualificationState(
        cfg,
        chain[-1],
        run_id="run",
        dataset_revision_id="dataset",
    )


@pytest.mark.parametrize("up", [True, False])
def test_default_exact_30_bar_70_point_boundary_and_direction_symmetry(up: bool) -> None:
    cfg = config()
    chain = build(initial(up, cfg), cfg)
    state = observer(chain, cfg)
    assert state.thresholds.min_duration_bars == 30
    assert state.thresholds.min_directional_move_points == Decimal(70)
    assert state.status == QualificationStatus.NO_ACTIVE_LEG
    for index in range(33):
        # Initial protected swing is bar 3: bar 32 is the thirtieth actual bar.
        close = ("180" if up else "60") if index >= 31 else "120"
        item = bar(index, close, "200", "40")
        update(chain, item)
        state.update(item)
        assert state.status == (
            QualificationStatus.NO_ACTIVE_LEG
            if index < 5
            else QualificationStatus.QUALIFIED
            if index == 32
            else QualificationStatus.UNQUALIFIED
        )
        if index == 31:
            evidence = state.active_qualification
            assert evidence is not None
            assert not evidence.duration_gate_passed and evidence.directional_move_gate_passed
    (earned,) = state.current_bar_qualifications
    assert earned.transition_type == QualificationStatus.QUALIFIED
    assert earned.source_leg.duration_bars == 30
    assert earned.source_leg.directional_movement_points == 70
    assert earned.event_time == earned.detection_time == bar(32).timestamp
    assert earned.source_leg.initial_protected_swing == initial(up, cfg)[4][0]


@pytest.mark.parametrize(
    "duration,move,close,qualified",
    [
        (3, "10", "120", True),
        (4, "10", "120", False),
        (3, "10.001", "120", False),
        (3, "0", "110", True),
        (3, "0", "109.5", False),
    ],
)
def test_individual_gates_zero_and_negative_directional_movement(
    duration: int,
    move: str,
    close: str,
    qualified: bool,
) -> None:
    cfg = config(min_duration_bars=duration, min_directional_move_points=Decimal(move))
    chain = build(initial(True, cfg), cfg)
    state = observer(chain, cfg)
    for index in range(6):
        item = bar(index, close if index == 5 else "120", "200", "100")
        update(chain, item)
        state.update(item)
    assert state.status == (
        QualificationStatus.QUALIFIED if qualified else QualificationStatus.UNQUALIFIED
    )
    assert len(state.current_bar_qualifications) == int(qualified)
    if qualified:
        earned = state.current_bar_qualifications[0]
        assert earned.event_time == bar(5).timestamp
        assert earned.event_time > earned.source_leg.initial_protected_swing.event_time
        assert not state.state.is_warm  # Observable evidence is not delayed by EMA warmup.


@pytest.mark.parametrize("up", [True, False])
def test_sticky_qualification_first_snapshot_protection_ema_and_deep_pullback(up: bool) -> None:
    cfg = config(min_duration_bars=3, min_directional_move_points=Decimal(10))
    schedule = initial(up, cfg)
    schedule[6] = (point(6, not up, "115" if up else "125", cfg),)
    schedule[7] = (point(7, up, "145" if up else "85", cfg),)
    chain = build(schedule, cfg)
    state = observer(chain, cfg)
    for index in range(6):
        item = bar(index)
        update(chain, item)
        state.update(item)
    earned = state.current_bar_qualifications[0]
    detached = state.state
    with pytest.raises(FrozenInstanceError):
        cast(Any, earned).event_time = bar(0).timestamp
    with pytest.raises(TypeError):
        cast(Any, detached.values["active_qualification"])["status"] = "OTHER"
    for index in (6, 7):
        item = bar(index, "112" if up else "128", "200", "40") if index == 6 else bar(index)
        update(chain, item)
        state.update(item)
        evidence = state.active_qualification
        assert evidence is not None and evidence.first_earned is earned
        assert state.status == QualificationStatus.QUALIFIED
        assert state.current_bar_qualifications == ()
        if index == 6:
            assert not evidence.directional_move_gate_passed
            assert evidence.source_leg.close_retracement_points > 35
            assert chain[-1].current_bar_ema_crosses
    evidence = state.active_qualification
    assert evidence is not None
    assert evidence.source_leg.protected_swing != earned.source_leg.protected_swing
    assert evidence.source_leg.initial_protected_swing == earned.source_leg.initial_protected_swing
    assert earned.source_leg.duration_bars == 3
    breaking = bar(8, "113" if up else "127", "200", "40")
    update(chain, breaking)
    state.update(breaking)
    assert state.status == QualificationStatus.NO_ACTIVE_LEG
    ended = state.current_bar_ended
    assert ended is not None and ended.evidence.first_earned is earned
    assert ended.evidence.status == QualificationStatus.QUALIFIED
    assert ended.source_transition.transition_type == TrendLegTransitionType.TERMINATED
    assert ended.evidence.source_leg == ended.source_transition.leg
    assert state.current_bar_qualifications == ()
    update(chain, bar(9))
    state.update(bar(9))
    assert state.current_bar_ended is None
    assert ended.evidence.first_earned is earned


def test_termination_precedes_first_passing_duration_even_when_final_move_passes() -> None:
    cfg = config(min_duration_bars=6, min_directional_move_points=Decimal(0))
    schedule = initial(True, cfg)
    schedule[6] = (point(6, False, "115", cfg),)
    schedule[7] = (point(7, True, "145", cfg),)
    chain = build(schedule, cfg)
    state = observer(chain, cfg)
    for index in range(8):
        update(chain, bar(index))
        state.update(bar(index))
    breaking = bar(8, "113", "130", "110")
    update(chain, breaking)
    state.update(breaking)
    ended = state.current_bar_ended
    assert ended is not None
    assert ended.evidence.duration_gate_passed and ended.evidence.directional_move_gate_passed
    assert ended.evidence.status == QualificationStatus.UNQUALIFIED
    assert ended.evidence.first_earned is None
    assert state.current_bar_qualifications == ()


def test_new_opposite_occurrence_does_not_inherit_earned_context() -> None:
    cfg = config(min_duration_bars=3, min_directional_move_points=Decimal(10))
    schedule = initial(True, cfg)
    for index, high, price in (
        (6, True, "135"),
        (7, False, "90"),
        (8, True, "130"),
        (9, False, "80"),
    ):
        schedule[index] = (point(index, high, price, cfg),)
    chain = build(schedule, cfg)
    state = observer(chain, cfg)
    first = None
    for index in range(10):
        item = (
            bar(index, "108", "125", "100")
            if index == 6
            else bar(index, "125", "130", "115")
            if index == 9
            else bar(index)
        )
        update(chain, item)
        state.update(item)
        if index == 5:
            first = state.active_qualification
    evidence = state.active_qualification
    assert first is not None and evidence is not None
    assert first.first_earned is not None
    assert evidence.status == QualificationStatus.UNQUALIFIED
    assert evidence.first_earned is None
    assert evidence.source_leg.leg_index == 2
    update(chain, bar(10))
    state.update(bar(10))
    evidence = state.active_qualification
    assert evidence is not None
    assert evidence.status == QualificationStatus.QUALIFIED
    assert evidence.first_earned is not first.first_earned
    assert evidence.first_earned is not None
    assert evidence.first_earned.event_time == bar(10).timestamp


def test_closure_actual_bar_count_bounded_state_and_full_chain_precision_replay() -> None:
    cfg = config(min_duration_bars=4, min_directional_move_points=Decimal(10))
    chain = build(initial(True, cfg), cfg)
    state = observer(chain, cfg)
    series = tuple(bar(index) for index in range(6)) + (
        replace(bar(6, "122.123456789", "200", "100"), timestamp=START + timedelta(days=3)),
    )
    for item in series:
        update(chain, item)
        state.update(item)
    earned = state.current_bar_qualifications[0]
    assert earned.source_leg.duration_bars == 4
    assert earned.event_time == series[-1].timestamp
    baseline = state.debug_json()
    for component in reversed((*chain, state)):
        component.reset()
    with localcontext() as context:
        context.prec = 5
        for item in series:
            update(chain, item)
            state.update(item)
    assert state.debug_json() == baseline
    for index in range(7, 207):
        item = replace(bar(index), timestamp=series[-1].timestamp + timedelta(minutes=index))
        update(chain, item)
        state.update(item)
        assert state.current_bar_qualifications == () and state.current_bar_ended is None
    assert not any(isinstance(value, list) for value in vars(state).values())
    assert state.active_qualification is not None
    assert state.active_qualification.first_earned.source_leg.duration_bars == 4


@pytest.mark.parametrize("dependency", range(5))
def test_reset_generations_and_exact_bar_guards_are_atomic(dependency: int) -> None:
    cfg = config()
    chain = build({}, cfg)
    state = observer(chain, cfg)
    before = state.debug_json()
    with pytest.raises(MarketStateError, match="exact bar"):
        state.update(bar(0))
    assert state.debug_json() == before
    update(chain, bar(0))
    with pytest.raises(MarketStateError, match="exact bar"):
        state.update(bar(0, "121"))
    assert state.debug_json() == before
    state.update(bar(0))
    chain[dependency].reset()
    before = state.debug_json()
    with pytest.raises(MarketStateError, match="reset"):
        state.update(bar(1))
    assert state.debug_json() == before


@pytest.mark.parametrize("change", ["lineage", "hash", "config", "binding", "settings", "identity"])
def test_source_corruption_guard_is_atomic(change: str) -> None:
    cfg = config()
    chain = build({}, cfg)
    state = observer(chain, cfg)
    update(chain, bar(0))
    state.update(bar(0))
    update(chain, bar(1))
    source = chain[-1]
    if change == "lineage":
        source._lineage = replace(source.lineage, dataset_revision_id="forged")
    elif change == "hash":
        source.detection_config_hash = "forged"
    elif change == "config":
        source._run_config = cfg.model_copy(update={"calendar_id": "forged"})
    elif change == "binding":
        source.instance_id = "forged"
    elif change == "settings":
        source.ema.period = 45
    else:
        state.trend_leg = build({}, cfg)[-1]
    before = state.debug_json()
    with pytest.raises(MarketStateError):
        state.update(bar(1))
    assert state.debug_json() == before


@pytest.mark.parametrize(
    "change", ["missing", "disabled", "version", "parameters", "extra", "duration", "move", "mode"]
)
def test_constructor_requires_exact_resolved_v1_contract(change: str) -> None:
    cfg = config()
    chain = build({}, cfg)
    selection = cfg.components[-1]
    alterations: dict[str, Any] = {
        "disabled": {"enabled": False},
        "version": {"component_version": "2"},
        "parameters": {"parameters": selection.parameters[:-1]},
        "extra": {"parameters": selection.parameters + (ConfigParameter(name="extra", value=0),)},
    }
    if change in ("duration", "move", "mode"):
        name, value = {
            "duration": ("min_duration_bars", True),
            "move": ("min_directional_move_points", 70),
            "mode": ("qualification_mode", "OTHER"),
        }[change]
        alterations[change] = {
            "parameters": tuple(
                parameter.model_copy(update={"value": value})
                if parameter.name == name
                else parameter
                for parameter in selection.parameters
            )
        }
    components = (
        cfg.components[:-1]
        if change == "missing"
        else (cfg.components[:-1] + (selection.model_copy(update=alterations[change]),))
    )
    with pytest.raises(MarketStateError):
        observer(chain, cfg.model_copy(update={"components": components}))


def test_constructor_hash_run_dataset_and_source_definition_validation() -> None:
    cfg = config()
    chain = build({}, cfg)
    for identifiers in (
        {"run_id": "other", "dataset_revision_id": "dataset"},
        {"run_id": "run", "dataset_revision_id": "other"},
        {"run_id": "", "dataset_revision_id": "dataset"},
    ):
        with pytest.raises(MarketStateError):
            TrendLegQualificationState(cfg, chain[-1], **identifiers)
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        TrendLegQualificationState(
            cfg, chain[-1], run_id="run", dataset_revision_id="dataset", pinned_config_hash="forged"
        )
    with pytest.raises(MarketStateError, match="M1"):
        observer(chain, cfg.model_copy(update={"timeframe": Timeframe.M5}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_duration_bars", 0),
        ("min_duration_bars", True),
        ("min_directional_move_points", Decimal(-1)),
        ("qualification_mode", "OTHER"),
    ],
)
def test_resolution_parameter_constraints(field: str, value: object) -> None:
    with pytest.raises(PatternDefinitionError):
        config(**{field: value})


def test_resolution_binding_m1_schema_preview_hash_and_legacy_compatibility() -> None:
    cfg = config()
    client = TestClient(app)
    definitions = client.get("/component-definitions").json()["components"]
    definition = next(
        item for item in definitions if item["component_id"] == "trend_leg_qualification"
    )
    assert definition["component_version"] == "1"
    assert {item["parameter_id"] for item in definition["parameters"]} == {
        "min_duration_bars",
        "min_directional_move_points",
        "qualification_mode",
        "trend_leg_instance_id",
    }
    preview = client.post("/config/preview", json=cfg.model_dump(mode="json"))
    assert preview.status_code == 200
    assert preview.json()["detection_config_hash"] == detection_config_hash(cfg)
    proposed = cfg.model_dump(mode="json")
    proposed["timeframe"] = Timeframe.M5.value
    rejected = client.post("/config/preview", json=proposed)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == (
        "TrendLeg qualification v1 requires canonical M1 bars"
    )
    with pytest.raises(ConfigurationError, match="bind"):
        config(trend_leg_instance_id="missing")
    with pytest.raises(ConfigurationError, match="M1"):
        resolve_detection_config(cfg.model_copy(update={"timeframe": Timeframe.M5}))
    assert detection_config_hash(cfg) != detection_config_hash(config(min_duration_bars=31))
    legacy = structural_config()
    assert resolve_detection_config(legacy).canonical_json() == legacy.canonical_json()
    disabled = cfg.model_copy(
        update={
            "components": tuple(
                item.model_copy(update={"enabled": False})
                if item.component_id == "trend_leg_qualification"
                else item
                for item in cfg.components
            ),
            "timeframe": Timeframe.M5,
        }
    )
    resolve_detection_config(disabled)


def test_alias_binding_and_future_version_resolution_are_independent() -> None:
    cfg = config()
    aliased = resolve_detection_config(
        cfg.model_copy(
            update={
                "components": tuple(
                    item.model_copy(update={"instance_id": "source"})
                    if item.component_id == "trend_leg"
                    else item.model_copy(
                        update={
                            "parameters": tuple(
                                ConfigParameter(name=p.name, value="source")
                                if p.name == "trend_leg_instance_id"
                                else p
                                for p in item.parameters
                            )
                        }
                    )
                    if item.component_id == "trend_leg_qualification"
                    else item
                    for item in cfg.components
                )
            }
        )
    )
    assert detection_config_hash(aliased) != detection_config_hash(cfg)
    atr = AtrState(aliased)
    swing = SwingPointState(aliased, atr)
    structure = SwingStructureState(aliased, swing, run_id="run", dataset_revision_id="dataset")
    source = TrendLegState(
        aliased,
        EmaState(aliased),
        structure,
        run_id="run",
        dataset_revision_id="dataset",
        instance_id="source",
    )
    TrendLegQualificationState(aliased, source, run_id="run", dataset_revision_id="dataset")
    future = DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="cal-v1",
        timeframe=Timeframe.M5,
        components=(
            ComponentSelection(
                component_id="trend_leg_qualification",
                component_version="2",
            ),
        ),
    )
    resolved = resolve_detection_config(
        future,
        component_parameters={
            ("trend_leg_qualification", "2"): (
                ParameterSpec("experiment", ParameterType.INTEGER, 7),
            ),
        },
    )
    assert resolved.components[0].parameters == (ConfigParameter(name="experiment", value=7),)


def test_delayed_establishment_qualifies_first_observable_bar_without_backfill() -> None:
    cfg = config()
    schedule = initial(True, cfg)
    directional = schedule.pop(5)[0]
    schedule[32] = (
        replace(
            directional,
            detection_time=bar(32).timestamp,
            confirmation_bar_index=32,
            bars_to_confirmation=28,
        ),
    )
    chain = build(schedule, cfg)
    state = observer(chain, cfg)
    for index in range(33):
        item = bar(index, "180", "200", "100")
        update(chain, item)
        state.update(item)
        if index < 32:
            assert state.status == QualificationStatus.NO_ACTIVE_LEG
            assert state.current_bar_qualifications == ()
    earned = state.current_bar_qualifications[0]
    assert earned.event_time == earned.detection_time == bar(32).timestamp
    assert earned.source_leg.duration_bars == 30
    assert earned.source_leg.initial_protected_swing.event_time == bar(3).timestamp


@pytest.mark.parametrize("up", [True, False])
def test_real_structural_chain_threshold_variation_and_precision_replay(up: bool) -> None:
    base = config(min_duration_bars=4, min_directional_move_points=Decimal(31))
    cfg = resolve_detection_config(
        base.model_copy(
            update={
                "components": tuple(
                    item.model_copy(
                        update={
                            "parameters": tuple(
                                ConfigParameter(name=p.name, value=Decimal("0.1"))
                                if p.name == "reversal_atr_multiplier"
                                else p
                                for p in item.parameters
                            )
                        }
                    )
                    if item.component_id == "swing_point"
                    else item
                    for item in base.components
                )
            }
        )
    )
    triples = (
        ("129", "130", "100"),
        ("110", "129", "109"),
        ("80", "110", "79"),
        ("100", "101", "80"),
        ("120", "121", "99"),
        ("110", "120", "109"),
        ("90", "110", "89"),
        ("110", "111", "90"),
        ("140", "141", "109"),
        ("120", "140", "119"),
    )
    series = tuple(bar(i, "100", "101", "99") for i in range(5)) + tuple(
        bar(i, *prices) for i, prices in enumerate(triples, 5)
    )
    if not up:
        series = tuple(
            replace(
                item,
                open=200 - item.open,
                high=200 - item.low,
                low=200 - item.high,
                close=200 - item.close,
            )
            for item in series
        )
    traces = []
    for threshold in (Decimal(31), Decimal(32)):
        variation = resolve_detection_config(
            cfg.model_copy(
                update={
                    "components": tuple(
                        item.model_copy(
                            update={
                                "parameters": tuple(
                                    ConfigParameter(name=p.name, value=threshold)
                                    if p.name == "min_directional_move_points"
                                    else p
                                    for p in item.parameters
                                )
                            }
                        )
                        if item.component_id == "trend_leg_qualification"
                        else item
                        for item in cfg.components
                    )
                }
            )
        )
        chain = build(None, variation)
        state = observer(chain, variation)
        for item in series:
            update(chain, item)
            state.update(item)
        assert state.status == (
            QualificationStatus.QUALIFIED if threshold == 31 else QualificationStatus.UNQUALIFIED
        )
        earned = state.active_qualification
        assert earned is not None and earned.source_leg.duration_bars == 4
        traces.append(
            (
                earned.source_leg.direction,
                earned.source_leg.duration_bars,
                earned.source_leg.directional_movement_points,
                earned.source_leg.protected_swing.event_price,
                tuple(item.transition_type for item in chain[-1].current_bar_transitions),
            )
        )
        breaking = bar(15, "86.89", "120", "86") if up else bar(15, "113.11", "114", "80")
        update(chain, breaking)
        state.update(breaking)
        baseline = state.debug_json()
        for component in reversed((*chain, state)):
            component.reset()
        with localcontext() as context:
            context.prec = 5
            for item in (*series, breaking):
                update(chain, item)
                state.update(item)
        assert state.debug_json() == baseline
    assert traces[0] == traces[1]
