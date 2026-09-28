from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any, cast

import pytest

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.component_registry import (
    SWING_STRUCTURE_V1_PARAMETERS,
    component_definition_schema,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    SWING_POINT_DEFINITION_ID,
    SWING_STRUCTURE_DEFINITION_ID,
    AtrState,
    MarketStateError,
    OverallStructure,
    StructureBreakType,
    SwingLabel,
    SwingPoint,
    SwingPointState,
    SwingStructureState,
    SwingType,
)
from market_analysis.patterns import PatternDefinitionError

START = datetime(2026, 1, 5, 12, tzinfo=UTC)


def config(**overrides: object) -> DetectionAnalysisConfig:
    parameters = {spec.parameter_id: spec.default for spec in SWING_STRUCTURE_V1_PARAMETERS}
    parameters.update(overrides)
    return resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(
            ComponentSelection(
                component_id="atr", component_version="1",
                parameters=(ConfigParameter(name="period", value=1),),
            ),
            ComponentSelection(
                component_id="swing_point", component_version="1",
                parameters=(ConfigParameter(name="atr_period", value=1),),
            ),
            ComponentSelection(
                component_id="swing_structure", component_version="1",
                parameters=tuple(
                    ConfigParameter(name=name, value=cast(Any, value))
                    for name, value in parameters.items()
                ),
            ),
        ),
    ))


def bar(index: int, close: str = "15", high: str = "25", low: str = "5") -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=index),
        Decimal(close), Decimal(high), Decimal(low), Decimal(close),
    )


def point(
    swing_index: int, index: int, high: bool, price: str,
    *, atr: str = "10", run_config: DetectionAnalysisConfig | None = None,
) -> SwingPoint:
    return SwingPoint(
        swing_index, SwingType.SWING_HIGH if high else SwingType.SWING_LOW,
        SWING_POINT_DEFINITION_ID, START + timedelta(minutes=index - 1), Decimal(price),
        START + timedelta(minutes=index), Decimal(15), index - 1, index,
        1, Decimal(atr), Decimal("1.5"), Decimal(atr) * Decimal("1.5"), 1,
        None, detection_config_hash(run_config or config()),
    )


class AuthoredSwingPointState(SwingPointState):
    """Only the point producer is substituted; exact bar metadata stays real."""

    def __init__(
        self, run_config: DetectionAnalysisConfig, atr: AtrState,
        schedule: dict[int, tuple[SwingPoint, ...]],
    ) -> None:
        self.schedule = schedule
        self.current: tuple[SwingPoint, ...] = ()
        super().__init__(run_config, atr)

    def _reset_state(self) -> None:
        super()._reset_state()
        self.current = ()

    def _update_completed_bar(self, bar: Bar) -> None:
        self.current = self.schedule.get(self._completed_bars, ())
        self._confirmed.extend(self.current)

    @property
    def current_bar_swing_points(self) -> tuple[SwingPoint, ...]:
        return self.current


def build(
    schedule: dict[int, tuple[SwingPoint, ...]] | None = None,
    run_config: DetectionAnalysisConfig | None = None,
) -> tuple[AtrState, SwingPointState, SwingStructureState]:
    run_config = run_config or config()
    atr = AtrState(run_config)
    swing = (SwingPointState(run_config, atr) if schedule is None
             else AuthoredSwingPointState(run_config, atr, schedule))
    structure = SwingStructureState(
        run_config, swing, run_id="run-1", dataset_revision_id="dataset-2",
    )
    return atr, swing, structure


def update(chain: tuple[AtrState, SwingPointState, SwingStructureState], item: Bar) -> None:
    for component in chain:
        component.update(item)


@pytest.mark.parametrize(("high", "new_price", "label"), [
    (True, "21.00001", SwingLabel.HH), (True, "19.99999", SwingLabel.EH),
    (True, "21", SwingLabel.EH), (True, "19", SwingLabel.EH),
    (True, "18.99999", SwingLabel.LH),
    (False, "11.00001", SwingLabel.HL), (False, "11", SwingLabel.EL),
    (False, "10", SwingLabel.EL), (False, "9", SwingLabel.EL),
    (False, "8.99999", SwingLabel.LL),
])
def test_classification_inclusive_new_atr_boundaries(
    high: bool, new_price: str, label: SwingLabel,
) -> None:
    first = point(1, 1, high, "20" if high else "10", atr="100")
    second = point(2, 3, high, new_price, atr="10")
    chain = build({1: (first,), 3: (second,)})
    update(chain, bar(0))
    assert chain[2].classifications == ()
    update(chain, bar(1))
    assert chain[2].classifications[0].label == (
        SwingLabel.HIGH_UNCLASSIFIED if high else SwingLabel.LOW_UNCLASSIFIED
    )
    update(chain, bar(2))
    update(chain, bar(3))
    result = chain[2].classifications[-1]
    assert result.label == label
    assert result.equality_tolerance == Decimal(1)
    assert result.previous_same_type_swing == first
    assert result.source_swing == second
    assert result.lower_equality_threshold == first.event_price - 1
    assert result.upper_equality_threshold == first.event_price + 1
    assert result.definition_id == SWING_STRUCTURE_DEFINITION_ID
    serialized = cast(Any, chain[2].state.values["classifications"])[-1]
    assert serialized["event_time"] == second.event_time
    assert serialized["detection_time"] == second.detection_time


@pytest.mark.parametrize(("high_price", "low_price", "expected"), [
    ("22", "12", OverallStructure.BULLISH),
    ("18", "8", OverallStructure.BEARISH),
    ("22", "8", OverallStructure.MIXED),
    ("18", "12", OverallStructure.MIXED),
    ("20", "10", OverallStructure.MIXED),
    ("20", "12", OverallStructure.MIXED),
    ("22", "10", OverallStructure.MIXED),
])
def test_conservative_overall_structure(
    high_price: str, low_price: str, expected: OverallStructure,
) -> None:
    chain = build({
        1: (point(1, 1, True, "20"),), 2: (point(2, 2, False, "10"),),
        3: (point(3, 3, True, high_price),), 4: (point(4, 4, False, low_price),),
    })
    for index in range(5):
        update(chain, bar(index))
        assert chain[2].overall_structure == (
            expected if index == 4 else OverallStructure.UNDEFINED
        )


@pytest.mark.parametrize("high", [True, False])
def test_break_strict_close_frozen_atr_duplicate_and_replacement(high: bool) -> None:
    chain = build({
        1: (point(1, 1, high, "20" if high else "10"),),
        6: (point(2, 6, high, "20" if high else "10"),),
    })
    beyond = "21.0001" if high else "8.9999"
    boundary = "21" if high else "9"
    update(chain, bar(0))
    update(chain, bar(1, beyond))  # New reference must not break on detection bar.
    assert chain[2].break_events == ()
    update(chain, bar(2, "15", "100", "0"))  # Wicks only; rolling ATR now differs.
    update(chain, bar(3, boundary))
    assert chain[2].break_events == ()
    update(chain, bar(4, beyond))
    event = chain[2].break_events[0]
    assert event.break_type == (StructureBreakType.SWING_HIGH_CLOSE_BREAK if high
                               else StructureBreakType.SWING_LOW_CLOSE_BREAK)
    assert event.buffer_points == Decimal(1)
    assert event.threshold == Decimal(boundary)
    assert event.reference_atr == Decimal(10)
    assert event.reference_swing == chain[2].classifications[0].source_swing
    assert event.pre_break_overall_structure == OverallStructure.UNDEFINED
    assert event.breaking_close == Decimal(beyond)
    serialized = cast(Any, chain[2].state.values["break_events"])[0]
    assert serialized["event_time"] == serialized["detection_time"] == event.detection_time
    assert serialized["reference_swing"]["detection_time"] == event.reference_swing.detection_time
    assert chain[2].state.values[
        "active_high_break_threshold" if high else "active_low_break_threshold"
    ] == Decimal(boundary)
    assert chain[2].state.values["last_structure_update_detection_time"] == event.detection_time
    assert chain[2].current_bar_breaks == (event,)
    update(chain, bar(5, beyond))
    assert chain[2].current_bar_breaks == ()
    assert len(chain[2].break_events) == 1
    update(chain, bar(6, beyond))
    assert len(chain[2].break_events) == 1
    assert chain[2].state.values["high_broken" if high else "low_broken"] is False
    update(chain, bar(7, beyond))
    assert len(chain[2].break_events) == 2
    assert chain[2].break_events[-1].reference_swing.swing_index == 2


def test_new_classification_precedes_old_opposite_reference_break() -> None:
    chain = build({
        1: (point(1, 1, True, "20"),), 2: (point(2, 2, False, "10"),),
        3: (point(3, 3, True, "22"),), 4: (point(4, 4, False, "12"),),
    })
    for index in range(4):
        update(chain, bar(index))
    update(chain, bar(4, "23.1"))
    event, = chain[2].current_bar_breaks
    assert event.reference_swing.swing_index == 3
    assert event.pre_break_overall_structure == OverallStructure.BULLISH
    assert event.pre_break_high_label == SwingLabel.HH
    assert event.pre_break_low_label == SwingLabel.HL


def test_zero_multipliers_use_exact_levels() -> None:
    cfg = config(equal_level_atr_multiplier=Decimal(0), break_buffer_atr_multiplier=Decimal(0))
    chain = build({
        1: (point(1, 1, True, "20", run_config=cfg),),
        3: (point(2, 3, True, "20.00001", run_config=cfg),),
    }, cfg)
    for index in range(4):
        update(chain, bar(index, "20"))
    assert chain[2].classifications[-1].label == SwingLabel.HH
    update(chain, bar(4, "20.00002"))
    assert chain[2].break_events[-1].buffer_points == 0


def test_evidence_lineage_and_nested_snapshots_are_immutable() -> None:
    chain = build({1: (point(1, 1, True, "20"),)})
    for index in range(3):
        update(chain, bar(index, "22"))
    structure = chain[2]
    classification, = structure.classifications
    event, = structure.break_events
    assert classification.lineage == event.lineage == structure.lineage
    assert structure.lineage.run_id == "run-1"
    assert structure.lineage.dataset_revision_id == "dataset-2"
    assert structure.lineage.instrument_id == "US30"
    assert structure.lineage.timeframe == Timeframe.M1
    assert structure.lineage.calendar_id == "cal-v1"
    assert structure.lineage.detection_config_hash == detection_config_hash(config())
    with pytest.raises(FrozenInstanceError):
        cast(Any, classification).label = SwingLabel.LH
    with pytest.raises(FrozenInstanceError):
        cast(Any, event.reference_swing).event_price = Decimal(1)
    with pytest.raises(FrozenInstanceError):
        cast(Any, structure.lineage).run_id = "other"
    with pytest.raises(AttributeError):
        cast(Any, structure).lineage = replace(structure.lineage, run_id="other")
    snapshot = structure.state
    with pytest.raises(TypeError):
        cast(Any, snapshot.values["latest_high"])["source_swing"]["event_price"] = Decimal(1)
    update(chain, bar(3))
    assert snapshot.completed_bars == 3
    assert snapshot.values["current_bar_breaks"]


@pytest.mark.parametrize(("name", "value"), [
    ("equal_level_atr_multiplier", Decimal("-0.01")),
    ("break_buffer_atr_multiplier", Decimal("-0.01")),
    ("equality_atr_anchor", "PRIOR_ATR"), ("break_atr_anchor", "CURRENT_ATR"),
    ("break_confirmation_source", "HIGH_LOW"),
    ("allow_break_on_reference_detection_bar", True),
])
def test_schema_rejects_unsupported_v1_parameters(name: str, value: object) -> None:
    with pytest.raises(PatternDefinitionError):
        config(**{name: value})


def test_registered_defaults_and_legacy_hash_stability() -> None:
    bare = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal-v1",
        components=(ComponentSelection(component_id="atr", component_version="1"),),
    )
    resolved = resolve_detection_config(bare)
    assert [item.component_id for item in resolved.components] == ["atr"]
    assert detection_config_hash(resolved) == detection_config_hash(
        bare.model_copy(update={"components": (ComponentSelection(
            component_id="atr", component_version="1",
            parameters=(ConfigParameter(name="period", value=14),),
        ),)})
    )
    metadata = next(item for item in component_definition_schema()
                    if item["component_id"] == "swing_structure")
    assert metadata["component_version"] == "1"
    assert len(cast(list[object], metadata["parameters"])) == 6


@pytest.mark.parametrize("change", ["missing", "disabled", "version", "parameter", "extra"])
def test_constructor_requires_exact_enabled_v1_selection(change: str) -> None:
    cfg = config()
    swing = SwingPointState(cfg, AtrState(cfg))
    selection = cfg.components[-1]
    if change == "missing":
        components = cfg.components[:-1]
    else:
        edits: dict[str, Any] = {
            "disabled": {"enabled": False}, "version": {"component_version": "2"},
            "parameter": {"parameters": selection.parameters[:-1]},
            "extra": {
                "parameters": selection.parameters + (ConfigParameter(name="extra", value=0),),
            },
        }
        components = cfg.components[:-1] + (selection.model_copy(update=edits[change]),)
    cfg = cfg.model_copy(update={"components": components})
    with pytest.raises(MarketStateError):
        SwingStructureState(cfg, swing, run_id="run", dataset_revision_id="dataset")


@pytest.mark.parametrize("identifier", ["", "  ", None, 5])
@pytest.mark.parametrize("field", ["run_id", "dataset_revision_id"])
def test_missing_lineage_rejected(field: str, identifier: object) -> None:
    cfg = config()
    ids = {"run_id": "run", "dataset_revision_id": "dataset", field: identifier}
    with pytest.raises(MarketStateError):
        SwingStructureState(cfg, SwingPointState(cfg, AtrState(cfg)), **cast(Any, ids))


def test_constructor_rejects_forged_hash_and_dependency_config() -> None:
    cfg = config()
    _, swing, _ = build()
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        SwingStructureState(cfg, swing, run_id="run", dataset_revision_id="dataset",
                            pinned_config_hash="forged")
    with pytest.raises(MarketStateError, match="config/hash"):
        SwingStructureState(config(break_buffer_atr_multiplier=Decimal(1)), swing,
                            run_id="run", dataset_revision_id="dataset")
    swing.detection_config_hash = "forged"
    with pytest.raises(MarketStateError, match="config/hash"):
        SwingStructureState(cfg, swing, run_id="run", dataset_revision_id="dataset")


@pytest.mark.parametrize("invalid", ["future", "hash", "index", "event", "bar_index", "atr"])
def test_invalid_point_rejected_without_structure_mutation(invalid: str) -> None:
    original = point(1, 1, True, "20")
    invalid_point = replace(original, **{
        "future": {"detection_time": START + timedelta(minutes=2)},
        "hash": {"detection_config_hash": "forged"},
        "index": {"swing_index": 2},
        "event": {"event_time": original.detection_time},
        "bar_index": {"confirmation_bar_index": 2},
        "atr": {"atr_at_extreme": Decimal(-1)},
    }[invalid])
    chain = build({1: (invalid_point,)})
    update(chain, bar(0))
    before = chain[2].debug_json()
    chain[0].update(bar(1))
    chain[1].update(bar(1))
    with pytest.raises(MarketStateError, match="invalid, skipped or future"):
        chain[2].update(bar(1))
    assert chain[2].debug_json() == before


def test_dependencies_require_exact_current_bar_and_no_skipping() -> None:
    chain = build({})
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[2].update(bar(0))
    chain[0].update(bar(0))
    chain[1].update(bar(0))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[2].update(bar(0, "16"))
    chain[2].update(bar(0))
    for index in (1, 2):
        chain[0].update(bar(index))
        chain[1].update(bar(index))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[2].update(bar(2))


@pytest.mark.parametrize("dependency", [0, 1])
def test_upstream_reset_replay_to_same_count_is_rejected(dependency: int) -> None:
    chain = build({})
    update(chain, bar(0))
    chain[dependency].reset()
    chain[dependency].update(bar(0))
    chain[0].update(bar(1))
    chain[1].update(bar(1))
    before = chain[2].debug_json()
    with pytest.raises(MarketStateError, match="upstream reset"):
        chain[2].update(bar(1))
    assert chain[2].debug_json() == before


REAL_SERIES = tuple(bar(index, "10", "11", "9") for index in range(5)) + (
    bar(5, "12", "20", "9"), bar(6, "19.5", "21", "19"),
    bar(7, "20", "21", "19.5"), bar(8, "18.2", "20.9", "18"),
    bar(9, "5", "18.5", "4"), bar(10, "5.8", "6", "4.2"),
    bar(11, "6.8", "7", "3"), bar(12, "7.8", "8", "3.2"),
    bar(13, "8.8", "9", "3.5"), bar(14, "18", "20", "4"),
    bar(15, "16", "22", "15"), bar(16, "14.5", "21", "14"),
    bar(17, "9", "20", "8"),
)


def test_real_atr_swing_chain_is_causal_and_reset_replay_is_identical() -> None:
    chain = build()
    for index, item in enumerate(REAL_SERIES):
        update(chain, item)
        if index < 9:
            assert chain[2].classifications == ()
        assert all(record.source_swing.detection_time <= item.timestamp
                   for record in chain[2].classifications)
        assert chain[1].current_bar_swing_points == tuple(
            p for p in chain[1].confirmed_swing_points if p.detection_time == item.timestamp
        )
    structure = chain[2]
    assert [record.source_swing for record in structure.classifications] == list(
        chain[1].confirmed_swing_points
    )
    assert [record.label for record in structure.classifications] == [
        SwingLabel.HIGH_UNCLASSIFIED, SwingLabel.LOW_UNCLASSIFIED, SwingLabel.HH,
    ]
    baseline = structure.debug_json()
    for component in reversed(chain):
        component.reset()
    with localcontext() as context:
        context.prec = 6
        for item in REAL_SERIES:
            update(chain, item)
    assert structure.debug_json() == baseline
    assert structure.break_events == ()


def test_sessions_and_days_preserve_structure() -> None:
    chain = build({1: (point(1, 1, True, "20"),)})
    update(chain, bar(0))
    update(chain, bar(1))
    next_day = replace(bar(2, "22"), timestamp=START + timedelta(days=1))
    update(chain, next_day)
    assert chain[2].break_events[0].reference_swing.event_time == START
    assert chain[2].break_events[0].detection_time == next_day.timestamp


def test_real_chain_close_break_carries_latest_confirmed_reference() -> None:
    chain = build()
    for item in REAL_SERIES:
        update(chain, item)
    reference = chain[1].confirmed_swing_points[-1]
    update(chain, bar(18, "30", "31", "8"))
    event, = chain[2].current_bar_breaks
    assert event.break_type == StructureBreakType.SWING_HIGH_CLOSE_BREAK
    assert event.reference_swing == reference
    assert event.reference_swing.event_time == REAL_SERIES[15].timestamp
    assert event.reference_swing.detection_time == REAL_SERIES[17].timestamp
    assert event.event_time == event.detection_time == bar(18).timestamp
    assert event.threshold == reference.event_price + Decimal("0.10") * reference.atr_at_extreme
    assert event.lineage == chain[2].lineage


@pytest.mark.parametrize(("name", "value"), [
    ("equal_level_atr_multiplier", True), ("equal_level_atr_multiplier", "0.1"),
    ("break_buffer_atr_multiplier", Decimal(-1)),
    ("equality_atr_anchor", "ROLLING_ATR"), ("break_atr_anchor", "ROLLING_ATR"),
    ("break_confirmation_source", "WICK"),
    ("allow_break_on_reference_detection_bar", 0),
])
def test_constructor_independently_rejects_unsupported_parameters(
    name: str, value: object,
) -> None:
    cfg = config()
    swing = SwingPointState(cfg, AtrState(cfg))
    selection = cfg.components[-1]
    changed = selection.model_copy(update={"parameters": tuple(
        ConfigParameter(name=item.name, value=cast(Any, value)) if item.name == name else item
        for item in selection.parameters
    )})
    cfg = cfg.model_copy(update={"components": cfg.components[:-1] + (changed,)})
    with pytest.raises(MarketStateError):
        SwingStructureState(cfg, swing, run_id="run", dataset_revision_id="dataset")


def test_reset_metadata_is_read_only_and_exact_bar_snapshot_is_preserved() -> None:
    chain = build({})
    initial = chain[0].reset_generation
    assert chain[0].last_completed_bar is None
    update(chain, bar(0))
    assert all(component.last_completed_bar == bar(0) for component in chain)
    with pytest.raises(AttributeError):
        cast(Any, chain[0]).reset_generation = 42
    with pytest.raises(AttributeError):
        cast(Any, chain[0]).last_completed_bar = bar(1)
    chain[0].reset()
    assert chain[0].reset_generation == initial + 1
    assert chain[0].last_completed_bar is None
