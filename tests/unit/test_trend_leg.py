from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from market_analysis.api.app import app
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.component_registry import component_definition_schema
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    SWING_POINT_DEFINITION_ID,
    AtrState,
    EmaState,
    MarketStateError,
    SwingPoint,
    SwingPointState,
    SwingStructureState,
    SwingType,
    TrendDirection,
    TrendLegState,
    TrendLegTransitionType,
)
from market_analysis.patterns import ParameterSpec, ParameterType, PatternDefinitionError

START = datetime(2026, 1, 5, 12, tzinfo=UTC)


def config(**overrides: object) -> DetectionAnalysisConfig:
    return resolve_detection_config(
        DetectionAnalysisConfig(
            instrument_id="US30",
            timeframe=Timeframe.M1,
            calendar_id="cal-v1",
            components=(
                ComponentSelection(
                    component_id="atr",
                    component_version="1",
                    parameters=(ConfigParameter(name="period", value=1),),
                ),
                ComponentSelection(
                    component_id="ema",
                    component_version="1",
                    parameters=(ConfigParameter(name="period", value=3),),
                ),
                ComponentSelection(
                    component_id="swing_point",
                    component_version="1",
                    parameters=(ConfigParameter(name="atr_period", value=1),),
                ),
                ComponentSelection(component_id="swing_structure", component_version="1"),
                ComponentSelection(
                    component_id="trend_leg",
                    component_version="1",
                    parameters=tuple(
                        ConfigParameter(name=name, value=cast(Any, value))
                        for name, value in overrides.items()
                    ),
                ),
            ),
        )
    )


def bar(index: int, close: str = "120", high: str = "125", low: str = "115") -> Bar:
    return Bar(
        "US30",
        Timeframe.M1,
        START + timedelta(minutes=index),
        Decimal(close),
        Decimal(high),
        Decimal(low),
        Decimal(close),
    )


def point(index: int, high: bool, price: str, cfg: DetectionAnalysisConfig) -> SwingPoint:
    return SwingPoint(
        index,
        SwingType.SWING_HIGH if high else SwingType.SWING_LOW,
        SWING_POINT_DEFINITION_ID,
        START + timedelta(minutes=index - 1),
        Decimal(price),
        START + timedelta(minutes=index),
        Decimal(120),
        index - 1,
        index,
        1,
        Decimal(10),
        Decimal("1.5"),
        Decimal(15),
        1,
        None,
        detection_config_hash(cfg),
    )


class AuthoredSwingState(SwingPointState):
    def __init__(
        self,
        cfg: DetectionAnalysisConfig,
        atr: AtrState,
        schedule: dict[int, tuple[SwingPoint, ...]],
    ) -> None:
        self.schedule = schedule
        self.current: tuple[SwingPoint, ...] = ()
        super().__init__(cfg, atr)

    def _reset_state(self) -> None:
        super()._reset_state()
        self.current = ()

    def _update_completed_bar(self, bar: Bar) -> None:
        self.current = self.schedule.get(self._completed_bars, ())
        self._confirmed.extend(self.current)

    @property
    def current_bar_swing_points(self) -> tuple[SwingPoint, ...]:
        return self.current


Chain = tuple[AtrState, SwingPointState, SwingStructureState, EmaState, TrendLegState]


def build(
    schedule: dict[int, tuple[SwingPoint, ...]] | None = None,
    cfg: DetectionAnalysisConfig | None = None,
) -> Chain:
    cfg = cfg or config()
    atr = AtrState(cfg)
    swing = (
        SwingPointState(cfg, atr) if schedule is None else AuthoredSwingState(cfg, atr, schedule)
    )
    structure = SwingStructureState(cfg, swing, run_id="run", dataset_revision_id="dataset")
    ema = EmaState(cfg)
    leg = TrendLegState(cfg, ema, structure, run_id="run", dataset_revision_id="dataset")
    return atr, swing, structure, ema, leg


def update(chain: Chain, item: Bar) -> None:
    for component in chain:
        component.update(item)


def initial(up: bool, cfg: DetectionAnalysisConfig) -> dict[int, tuple[SwingPoint, ...]]:
    prices = (
        ("130", "100", "130", "110", "140")
        if up
        else (
            "100",
            "140",
            "100",
            "130",
            "90",
        )
    )
    return {
        index: (point(index, up == (index % 2 == 1), price, cfg),)
        for index, price in enumerate(prices, 1)
    }


@pytest.mark.parametrize("up", [True, False])
def test_confirmed_pair_observability_anchor_and_favorable_scope(up: bool) -> None:
    cfg = config()
    chain = build(initial(up, cfg), cfg)
    for index in range(5):
        update(chain, bar(index))
        assert chain[-1].active_leg is None
    update(chain, bar(5))
    leg = chain[-1].active_leg
    assert leg is not None
    assert leg.direction == (TrendDirection.UP if up else TrendDirection.DOWN)
    assert leg.event_time == START + timedelta(minutes=3)
    assert leg.detection_time == START + timedelta(minutes=5)
    assert leg.initial_protected_swing == leg.protected_swing == chain[1].schedule[4][0]
    assert leg.duration_bars == 3
    assert leg.directional_movement_points == 10
    assert leg.favorable_extreme == (140 if up else 90)
    assert leg.close_retracement_points == (20 if up else 30)
    assert leg.favorable_extreme_observation_start == bar(5).timestamp
    (event,) = chain[-1].current_bar_transitions
    assert event.transition_type == TrendLegTransitionType.ESTABLISHED
    assert event.event_time == leg.event_time and event.detection_time == leg.detection_time
    assert event.previous_leg is None and event.leg == leg


@pytest.mark.parametrize("up", [True, False])
def test_ema_pullback_does_not_terminate_and_protection_advances_without_reanchor(up: bool) -> None:
    cfg = config()
    schedule = initial(up, cfg)
    schedule[6] = (point(6, not up, "115" if up else "125", cfg),)
    schedule[7] = (point(7, up, "145" if up else "85", cfg),)
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    old = chain[-1].active_leg
    assert old is not None
    update(chain, bar(6, "112" if up else "128", "145" if up else "130", "108" if up else "85"))
    assert chain[-1].active_leg is not None
    assert chain[-1].active_leg.protected_swing == old.protected_swing
    assert chain[-1].current_bar_transitions == ()
    assert chain[-1].current_bar_ema_crosses
    update(chain, bar(7))
    new = chain[-1].active_leg
    assert new is not None
    assert new.protected_swing == schedule[6][0]
    assert new.initial_protected_swing == old.initial_protected_swing
    assert new.event_time == old.event_time and new.detection_time == old.detection_time
    assert new.protection_detection_time == bar(7).timestamp
    assert new.duration_bars == 5
    assert new.favorable_extreme == (145 if up else 85)  # already observed on completed bar6
    assert (
        chain[-1].current_bar_transitions[0].transition_type
        == TrendLegTransitionType.PROTECTION_ADVANCED
    )


@pytest.mark.parametrize("up", [True, False])
def test_pending_new_reference_break_does_not_replace_old_protection(up: bool) -> None:
    cfg = config()
    schedule = initial(up, cfg)
    schedule[6] = (point(6, not up, "118" if up else "122", cfg),)
    chain = build(schedule, cfg)
    for index in range(7):
        update(chain, bar(index))
    old = chain[-1].active_leg
    assert old is not None
    update(chain, bar(7, "115" if up else "125", "135", "105"))
    assert chain[2].current_bar_breaks  # latest Structure reference is now broken
    assert chain[-1].active_leg is not None
    assert chain[-1].active_leg.protected_swing == old.protected_swing
    update(chain, bar(8, "108.999" if up else "131.001", "140", "100"))
    assert chain[2].current_bar_breaks == ()  # once-per-new-reference evidence cannot end old leg
    assert chain[-1].active_leg is None
    (event,) = chain[-1].current_bar_transitions
    assert event.leg.protected_swing == old.protected_swing
    assert event.transition_type == TrendLegTransitionType.TERMINATED


@pytest.mark.parametrize("up", [True, False])
def test_wick_buffer_boundary_and_break_first_conflict(up: bool) -> None:
    cfg = config()
    schedule = initial(up, cfg)
    schedule[8] = (point(6, not up, "115" if up else "125", cfg),)
    schedule[9] = (point(7, up, "145" if up else "85", cfg),)
    # Adjust authored detection indexes while preserving contiguous swing identity.
    for index in (8, 9):
        source = schedule[index][0]
        schedule[index] = (
            replace(
                source,
                detection_time=bar(index).timestamp,
                event_time=bar(index - 1).timestamp,
                confirmation_bar_index=index,
                candidate_bar_index=index - 1,
            ),
        )
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    old = chain[-1].active_leg
    assert old is not None
    update(chain, bar(6, "120", "200", "0"))
    update(chain, bar(7, "109" if up else "131", "200", "0"))
    assert chain[-1].active_leg is not None  # equality is not strict close-break
    update(chain, bar(8))
    update(chain, bar(9, "108.999" if up else "131.001", "200", "0"))
    (event,) = chain[-1].current_bar_transitions
    assert event.transition_type == TrendLegTransitionType.TERMINATED
    assert event.leg.protected_swing == old.protected_swing
    assert event.leg.current_close == event.breaking_close
    assert event.leg.current_time == bar(9).timestamp
    assert chain[-1].active_leg is None
    update(chain, bar(10))
    assert chain[-1].active_leg is None


def test_termination_requires_fresh_post_end_corrective_directional_sequence() -> None:
    cfg = config()
    schedule = initial(True, cfg)
    # LH is observed on termination; LL alone afterward cannot establish DOWN.
    for index, high, price in (
        (6, True, "135"),
        (7, False, "90"),
        (8, True, "130"),
        (9, False, "80"),
    ):
        schedule[index] = (point(index, high, price, cfg),)
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    update(chain, bar(6, "108", "125", "100"))
    assert chain[-1].active_leg is None
    update(chain, bar(7))
    assert chain[-1].active_leg is None
    update(chain, bar(8))
    assert chain[-1].active_leg is None
    update(chain, bar(9))
    assert chain[-1].active_leg is not None
    assert chain[-1].active_leg.direction == TrendDirection.DOWN
    assert chain[-1].active_leg.leg_index == 2
    assert chain[-1].active_leg.initial_protected_swing == schedule[8][0]


@pytest.mark.parametrize("up", [True, False])
@pytest.mark.parametrize("equal_corrective", [True, False])
def test_equal_swings_never_establish(up: bool, equal_corrective: bool) -> None:
    cfg = config()
    schedule = initial(up, cfg)
    target = 4 if equal_corrective else 5
    price = ("100" if up else "140") if equal_corrective else ("130" if up else "100")
    schedule[target] = (replace(schedule[target][0], event_price=Decimal(price)),)
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    assert chain[-1].active_leg is None


def test_equal_and_backwards_protected_levels_retain_protection() -> None:
    cfg = config()
    schedule = initial(True, cfg)
    # A later HL relative to an intervening LL can remain below the old protection.
    for index, high, price in (
        (6, False, "110"),
        (7, True, "145"),
        (8, False, "100"),
        (9, True, "150"),
        (10, False, "105"),
        (11, True, "155"),
    ):
        schedule[index] = (point(index, high, price, cfg),)
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    old = chain[-1].active_leg
    assert old is not None
    for index in range(6, 12):
        update(chain, bar(index))
        assert chain[-1].active_leg is not None
        assert chain[-1].active_leg.protected_swing == old.protected_swing
        assert chain[-1].current_bar_transitions == ()


def test_confirmed_hh_below_older_high_can_advance_using_structure_progression() -> None:
    cfg = config()
    schedule = initial(True, cfg)
    for index, high, price in (
        (6, False, "112"),
        (7, True, "130"),
        (8, False, "115"),
        (9, True, "135"),
    ):
        schedule[index] = (point(index, high, price, cfg),)
    chain = build(schedule, cfg)
    for index in range(10):
        update(chain, bar(index))
    leg = chain[-1].active_leg
    assert leg is not None
    assert leg.protected_swing == schedule[8][0]
    assert leg.favorable_extreme == 140


def test_already_close_broken_proposed_reference_cannot_establish_or_advance() -> None:
    cfg = config()
    chain = build(initial(True, cfg), cfg)
    for index in range(5):
        update(chain, bar(index))
    update(chain, bar(5, "108", "125", "100"))
    assert chain[-1].active_leg is None
    assert chain[-1].current_bar_transitions == ()


def test_raw_cross_initialization_equality_shared_bar_metrics() -> None:
    chain = build({})
    # EMA3 values None,None,100,105,105,102.5,106.25.
    closes = ("100", "100", "100", "110", "105", "100", "110")
    for index, close in enumerate(closes):
        update(chain, bar(index, close, "120", "90"))
        if index < 3:
            assert chain[-1].active_ema_segment is None
            assert chain[-1].current_bar_ema_crosses == ()
        if index == 4:
            assert chain[-1].current_bar_ema_crosses == ()  # equality/current neutral
        if index == 5:
            (event,) = chain[-1].current_bar_ema_crosses
            ended = event.ended_segment
            started = event.started_segment
            assert ended is not None and started is not None
            assert ended.direction == TrendDirection.UP and started.direction == TrendDirection.DOWN
            assert ended.bars == 3 and started.bars == 1
            assert ended.end_time == started.start_time == bar(5).timestamp
            assert ended.current_close == started.start_close == 100
            assert ended.end_close == 100 and started.end_close is None
            assert ended.net_points == ended.directional_net_points == -10
            assert ended.span_points == 30
            assert abs(ended.efficiency_pct - Decimal(100) / 3) < Decimal("1e-25")
            assert event.previous_close == event.previous_ema == 105
    assert chain[-1].active_leg is None
    (event,) = chain[-1].current_bar_ema_crosses
    assert event.ended_segment is not None and event.ended_segment.directional_net_points == -10
    assert (
        event.started_segment is not None and event.started_segment.direction == TrendDirection.UP
    )


def test_zero_span_efficiency_and_same_direction_recross_retains_raw_anchor() -> None:
    chain = build({})
    for index, close in enumerate(("100", "100", "100", "110", "105", "110")):
        update(chain, bar(index, close, close, close))
        if index == 3:
            assert chain[-1].active_ema_segment is not None
            assert chain[-1].active_ema_segment.efficiency_pct is None
        if index == 5:
            (event,) = chain[-1].current_bar_ema_crosses
            assert event.started_segment is None and event.ended_segment is None
            assert event.direction == TrendDirection.UP
            assert chain[-1].active_ema_segment is not None
            assert chain[-1].active_ema_segment.start_time == bar(3).timestamp
            assert chain[-1].active_ema_segment.bars == 3


def test_first_valid_ema_off_neutral_does_not_synthesize_cross() -> None:
    chain = build({})
    for index, close in enumerate(("100", "105", "110", "115")):
        update(chain, bar(index, close, "125", "90"))
        assert chain[-1].active_ema_segment is None


def test_immutable_lineage_snapshots_and_bounded_current_bar_output() -> None:
    cfg = config()
    chain = build(initial(True, cfg), cfg)
    for index in range(6):
        update(chain, bar(index))
    leg = chain[-1].active_leg
    assert leg is not None
    snapshot = chain[-1].state
    assert leg.lineage == chain[2].lineage == chain[-1].lineage
    with pytest.raises(FrozenInstanceError):
        cast(Any, leg).duration_bars = 0
    with pytest.raises(FrozenInstanceError):
        cast(Any, leg.initial_protected_swing).event_price = Decimal(0)
    with pytest.raises(TypeError):
        cast(Any, snapshot.values["active_leg"])["current_close"] = 0
    for index in range(6, 206):
        update(chain, bar(index))
        assert chain[-1].current_bar_transitions == ()
        assert chain[-1].current_bar_ema_crosses == ()
    assert cast(Any, snapshot.values["active_leg"])["duration_bars"] == 3
    assert not any(isinstance(value, list) for value in vars(chain[-1]).values())


def test_closure_carry_and_full_reset_precision_replay_parity() -> None:
    cfg = config()
    schedule = initial(True, cfg)
    chain = build(schedule, cfg)
    series = tuple(bar(index) for index in range(6)) + (
        replace(bar(6, "122.123456789", "130", "110"), timestamp=START + timedelta(days=3)),
    )
    for item in series:
        update(chain, item)
    leg = chain[-1].active_leg
    assert leg is not None and leg.duration_bars == 4
    assert leg.event_time == schedule[4][0].event_time
    baseline = chain[-1].debug_json()
    for component in reversed(chain):
        component.reset()
    with localcontext() as context:
        context.prec = 6
        for item in series:
            update(chain, item)
    assert chain[-1].debug_json() == baseline


@pytest.mark.parametrize("dependency", range(4))
def test_reset_generation_rejects_even_replayed_matching_count_atomically(dependency: int) -> None:
    chain = build({})
    update(chain, bar(0))
    chain[dependency].reset()
    chain[dependency].update(bar(0))
    # TrendLeg rejects a changed generation even before upstream can advance.
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError, match="reset"):
        chain[-1].update(bar(1))
    assert chain[-1].debug_json() == before


def test_exact_bar_lockstep_guard_is_atomic() -> None:
    chain = build({})
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[-1].update(bar(0))
    assert chain[-1].debug_json() == before
    for component in chain[:-1]:
        component.update(bar(0))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[-1].update(bar(0, "121"))
    assert chain[-1].debug_json() == before
    chain[-1].update(bar(0))
    for index in (1, 2):
        for component in chain[:-1]:
            component.update(bar(index))
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[-1].update(bar(2))
    assert chain[-1].debug_json() == before


@pytest.mark.parametrize("change", ["lineage", "hash", "config", "binding", "settings"])
def test_dependency_identity_changes_fail_closed_atomically(change: str) -> None:
    chain = build({})
    update(chain, bar(0))
    for component in chain[:-1]:
        component.update(bar(1))
    if change == "lineage":
        chain[2]._lineage = replace(chain[2].lineage, run_id="forged")
    elif change == "hash":
        chain[2].detection_config_hash = "forged"
    elif change == "config":
        chain[3]._run_config = chain[3].run_config.model_copy(update={"calendar_id": "forged"})
    elif change == "binding":
        chain[3].instance_id = "forged"
    else:
        chain[2].break_buffer_atr_multiplier = Decimal(1)
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError):
        chain[-1].update(bar(1))
    assert chain[-1].debug_json() == before


@pytest.mark.parametrize("change", ["lineage", "label", "delta", "source", "batch"])
def test_malformed_classification_batch_is_rejected_before_mutation(change: str) -> None:
    cfg = config()
    chain = build({1: (point(1, True, "130", cfg),)}, cfg)
    update(chain, bar(0))
    for component in chain[:-1]:
        component.update(bar(1))
    (record,) = chain[2]._current_classifications
    if change == "lineage":
        record = replace(record, lineage=replace(record.lineage, dataset_revision_id="forged"))
    elif change == "label":
        record = replace(record, label=cast(Any, "HH"))
    elif change == "delta":
        record = replace(record, price_delta=Decimal(1))
    elif change == "source":
        record = replace(
            record, source_swing=replace(record.source_swing, event_price=Decimal("NaN"))
        )
    chain[2]._current_classifications = (record,) if change != "batch" else ()
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError):
        chain[-1].update(bar(1))
    assert chain[-1].debug_json() == before


@pytest.mark.parametrize(
    "field",
    [
        "establishment_mode",
        "termination_mode",
        "ema_segment_boundary_mode",
        "favorable_extreme_mode",
    ],
)
def test_fixed_semantic_modes_are_validated(field: str) -> None:
    with pytest.raises(PatternDefinitionError):
        config(**{field: "OTHER"})


@pytest.mark.parametrize("field", ["ema_instance_id", "structure_instance_id"])
def test_binding_schema_preview_and_hash(field: str) -> None:
    with pytest.raises(ConfigurationError, match="bind"):
        config(**{field: "missing"})
    cfg = config()
    component_id = "ema" if field == "ema_instance_id" else "swing_structure"
    bare = cfg.model_copy(
        update={
            "components": tuple(
                item.model_copy(update={"instance_id": "bound"})
                if item.component_id == component_id
                else item.model_copy(
                    update={
                        "parameters": tuple(
                            ConfigParameter(name=p.name, value="bound") if p.name == field else p
                            for p in item.parameters
                        )
                    }
                )
                if item.component_id == "trend_leg"
                else item
                for item in cfg.components
            )
        }
    )
    resolved = resolve_detection_config(bare)
    assert detection_config_hash(resolved) != detection_config_hash(cfg)
    atr = AtrState(resolved)
    swing = SwingPointState(resolved, atr)
    structure = SwingStructureState(
        resolved,
        swing,
        run_id="run",
        dataset_revision_id="dataset",
        instance_id="bound" if component_id == "swing_structure" else "swing_structure",
    )
    ema = EmaState(resolved, instance_id="bound" if component_id == "ema" else "ema")
    TrendLegState(resolved, ema, structure, run_id="run", dataset_revision_id="dataset")
    metadata = next(
        item for item in component_definition_schema() if item["component_id"] == "trend_leg"
    )
    assert len(cast(list[Any], metadata["parameters"])) == 6


@pytest.mark.parametrize("change", ["missing", "disabled", "version", "parameter", "extra"])
def test_constructor_independently_requires_exact_enabled_v1_selection(change: str) -> None:
    chain = build({})
    cfg = config()
    selection = cfg.components[-1]
    changes: dict[str, Any] = {
        "disabled": {"enabled": False},
        "version": {"component_version": "2"},
        "parameter": {"parameters": selection.parameters[:-1]},
        "extra": {"parameters": selection.parameters + (ConfigParameter(name="extra", value=0),)},
    }
    components = (
        cfg.components[:-1]
        if change == "missing"
        else (cfg.components[:-1] + (selection.model_copy(update=changes[change]),))
    )
    malformed = cfg.model_copy(update={"components": components})
    with pytest.raises(MarketStateError):
        TrendLegState(malformed, chain[3], chain[2], run_id="run", dataset_revision_id="dataset")


def test_constructor_rejects_pinned_hash_and_run_dataset_mismatch() -> None:
    chain = build({})
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        TrendLegState(
            config(),
            chain[3],
            chain[2],
            run_id="run",
            dataset_revision_id="dataset",
            pinned_config_hash="forged",
        )
    for name, value in (("run_id", "other"), ("dataset_revision_id", "other"), ("run_id", "")):
        identifiers = {"run_id": "run", "dataset_revision_id": "dataset", name: value}
        with pytest.raises(MarketStateError):
            TrendLegState(config(), chain[3], chain[2], **identifiers)


def test_future_registered_trend_leg_version_does_not_inherit_v1_bindings() -> None:
    bare = DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="cal-v1",
        components=(ComponentSelection(component_id="trend_leg", component_version="2"),),
    )
    resolved = resolve_detection_config(
        bare,
        component_parameters={
            ("trend_leg", "2"): (ParameterSpec("experiment", ParameterType.INTEGER, 7),),
        },
    )
    assert resolved.components[0].parameters == (ConfigParameter(name="experiment", value=7),)


@pytest.mark.parametrize("up", [True, False])
def test_actual_chain_establishment_metrics_close_break_and_deterministic_replay(up: bool) -> None:
    cfg = config()
    cfg = resolve_detection_config(
        cfg.model_copy(
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
                    for item in cfg.components
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
    chain = build(None, cfg)
    for index, item in enumerate(series):
        update(chain, item)
        if index < 14:
            assert chain[-1].active_leg is None
        assert all(
            event.detection_time <= item.timestamp for event in chain[-1].current_bar_transitions
        )
    leg = chain[-1].active_leg
    assert leg is not None
    assert leg.direction == (TrendDirection.UP if up else TrendDirection.DOWN)
    assert leg.event_time == bar(11).timestamp and leg.detection_time == bar(14).timestamp
    assert leg.initial_protected_swing.candidate_bar_index == 11
    assert leg.duration_bars == 4 and leg.directional_movement_points == 31
    assert leg.favorable_extreme == (141 if up else 59)
    assert leg.close_retracement_points == 21
    assert leg.break_buffer_points == Decimal("2.10")
    # Next completed bar crosses the retained threshold. This is a real-chain
    # termination, independent of whether a fresh swing is confirmed on it.
    breaking = bar(15, "86.89", "120", "86") if up else bar(15, "113.11", "114", "80")
    update(chain, breaking)
    assert chain[-1].active_leg is None
    (ended,) = chain[-1].current_bar_transitions
    assert ended.transition_type == TrendLegTransitionType.TERMINATED
    assert ended.leg.initial_protected_swing == leg.initial_protected_swing
    baseline = chain[-1].debug_json()
    for component in reversed(chain):
        component.reset()
    with localcontext() as context:
        context.prec = 5
        for item in (*series, breaking):
            update(chain, item)
    assert chain[-1].debug_json() == baseline


def test_server_preview_publishes_schema_and_validates_binding_and_hash() -> None:
    client = TestClient(app)
    definitions = client.get("/component-definitions")
    assert definitions.status_code == 200
    definition = next(
        item for item in definitions.json()["components"] if item["component_id"] == "trend_leg"
    )
    assert definition["component_version"] == "1"
    assert len(definition["parameters"]) == 6
    proposed = config().model_dump(mode="json")
    preview = client.post("/config/preview", json=proposed)
    assert preview.status_code == 200
    assert preview.json()["detection_config_hash"] == detection_config_hash(config())
    parameters = proposed["components"][-1]["parameters"]
    for parameter in parameters:
        if parameter["name"] == "ema_instance_id":
            parameter["value"] = "swing_structure"
    rejected = client.post("/config/preview", json=proposed)
    assert rejected.status_code == 422
    assert "enabled ema v1" in rejected.json()["detail"]


@pytest.mark.parametrize("dependency", ["ema", "structure"])
def test_constructor_rejects_preexisting_dependency_setting_corruption(dependency: str) -> None:
    chain = build({})
    if dependency == "ema":
        chain[3].period = 45
    else:
        chain[2].break_buffer_atr_multiplier = Decimal(1)
    with pytest.raises(MarketStateError, match="settings"):
        TrendLegState(config(), chain[3], chain[2], run_id="run", dataset_revision_id="dataset")


def test_dependency_replacement_cannot_fool_matching_reset_counts() -> None:
    chain = build({})
    update(chain, bar(0))
    for component in chain[:-1]:
        component.update(bar(1))
    replacement = EmaState(config())
    replacement.update(bar(0))
    replacement.update(bar(1))
    chain[-1].ema = replacement
    before = chain[-1].debug_json()
    with pytest.raises(MarketStateError, match="replaced"):
        chain[-1].update(bar(1))
    assert chain[-1].debug_json() == before


@pytest.mark.parametrize("up", [True, False])
def test_shared_extreme_event_bar_keeps_causal_confirmation_order(up: bool) -> None:
    cfg = config()
    schedule = initial(up, cfg)
    corrective = schedule[4][0]
    directional = schedule[5][0]
    schedule[5] = (
        replace(
            directional,
            event_time=corrective.event_time,
            candidate_bar_index=corrective.candidate_bar_index,
            bars_to_confirmation=2,
        ),
    )
    chain = build(schedule, cfg)
    for index in range(6):
        update(chain, bar(index))
    leg = chain[-1].active_leg
    assert leg is not None
    assert leg.initial_protected_swing.event_time == leg.directional_swing.event_time
    assert leg.initial_protected_swing.detection_time < leg.directional_swing.detection_time
    assert leg.detection_time == bar(5).timestamp
