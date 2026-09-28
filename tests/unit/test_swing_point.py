from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    SWING_POINT_DEFINITION_ID,
    AtrState,
    MarketStateError,
    SwingPoint,
    SwingPointReference,
    SwingPointState,
    SwingType,
)

START = datetime(2026, 1, 5, 12, tzinfo=UTC)
MULTIPLIER = Decimal("1.5")


def bar(index: int, high: str, low: str, close: str) -> Bar:
    return Bar(
        "US30", Timeframe.M1, START + timedelta(minutes=index),
        Decimal(close), Decimal(high), Decimal(low), Decimal(close),
    )


# ATR(1) warm-up bars: every true range is 2, so ATR is 2 across the warm-up.
WARMUP = tuple(bar(i, "11", "9", "10") for i in range(5))

# Hand-reviewed swing-high fixture. Bar 6 replaces the candidate (ATR 9,
# threshold 13.5); bar 9 closes at 5 <= 21 - 13.5 and confirms it. Bars 11 and
# 15 mirror the same mechanics for the low and the next high.
SWING_HIGH_SERIES = (
    bar(5, "20", "9", "12"),
    bar(6, "21", "19", "19.5"),
    bar(7, "21", "19.5", "20"),
    bar(8, "20.9", "18", "18.2"),
    bar(9, "18.5", "4", "5"),
    bar(10, "6", "4.2", "5.8"),
    bar(11, "7", "3", "6.8"),
    bar(12, "8", "3.2", "7.8"),
    bar(13, "9", "3.5", "8.8"),
    bar(14, "20", "4", "18"),
    bar(15, "22", "15", "16"),
    bar(16, "21", "14", "14.5"),
    bar(17, "20", "8", "9"),
)

SWING_LOW_SERIES = (
    bar(5, "11", "5", "8"),
    bar(6, "10", "1", "9"),
    bar(7, "9.5", "1", "8.8"),
    bar(8, "9", "1.5", "8.5"),
    bar(9, "30", "1.4", "29.5"),
)

SAME_BAR_SERIES = (
    bar(5, "20", "15", "19"),
    bar(6, "19.9", "4", "5"),
)

REPLACED_SERIES = (
    bar(5, "20", "9", "19"),
    bar(6, "21", "3", "3.5"),
    bar(7, "20.5", "2.9", "3"),
)

MULTIPLIER_SERIES = (
    bar(5, "20", "9", "19"),
    bar(6, "20.5", "13", "14"),
    bar(7, "20", "12.5", "12.6"),
)

MONOTONE_SERIES = tuple(
    bar(5 + offset, str(22 + offset), str(20 + offset), str(21 + offset))
    for offset in range(8)
)


def selection_parameters(atr_period: int = 1, **overrides: object) -> dict[str, object]:
    parameters: dict[str, object] = {
        "atr_period": atr_period,
        "reversal_atr_multiplier": MULTIPLIER,
        "extreme_source": "HIGH_LOW",
        "confirmation_source": "CLOSE",
        "freeze_atr_at_extreme": True,
        "allow_same_bar_confirmation": False,
        "equal_extreme_policy": "KEEP_EARLIEST",
        "require_alternation": True,
    }
    parameters.update(overrides)
    return parameters


def run_config(
    atr_period: int = 1,
    *,
    with_swing_point: bool = True,
    with_atr: bool = True,
    atr_component_period: int | None = None,
    component_version: str = "1",
    enabled: bool = True,
    omit_parameter: str | None = None,
    extra_parameter: str | None = None,
    **overrides: object,
) -> DetectionAnalysisConfig:
    components = []
    if with_atr:
        components.append(ComponentSelection(
            component_id="atr", component_version="1",
            parameters=(ConfigParameter(
                name="period",
                value=atr_component_period if atr_component_period is not None else atr_period,
            ),),
        ))
    if with_swing_point:
        parameters = selection_parameters(atr_period, **overrides)
        if omit_parameter is not None:
            del parameters[omit_parameter]
        if extra_parameter is not None:
            parameters[extra_parameter] = "HIGH_LOW"
        components.append(ComponentSelection(
            component_id="swing_point", component_version=component_version,
            enabled=enabled,
            parameters=tuple(
                ConfigParameter(name=name, value=value)
                for name, value in sorted(parameters.items())
            ),
        ))
    return DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=tuple(components),
    )


def build(
    atr_period: int = 1,
    *,
    pinned_config_hash: str | None = None,
    **overrides: object,
) -> tuple[AtrState, SwingPointState]:
    config = run_config(atr_period, **overrides)
    atr = AtrState(config)
    return atr, SwingPointState(config, atr, pinned_config_hash=pinned_config_hash)


def feed(
    series: tuple[Bar, ...],
    atr_period: int = 1,
    *,
    pinned_config_hash: str | None = None,
    **overrides: object,
) -> tuple[AtrState, SwingPointState]:
    atr, swing = build(atr_period, pinned_config_hash=pinned_config_hash, **overrides)
    for item in series:
        atr.update(item)
        swing.update(item)
    return atr, swing


def candidate(swing: SwingPointState, high: bool) -> dict[str, Any]:
    key = "provisional_candidate_high" if high else "provisional_candidate_low"
    return cast(dict[str, Any], swing.state.values[key])


def confirmed_values(swing: SwingPointState) -> tuple[dict[str, Any], ...]:
    return cast(tuple[dict[str, Any], ...], swing.state.values["confirmed"])


def test_swing_high_fixture_emits_expected_event_and_later_detection() -> None:
    _, swing = feed(WARMUP + SWING_HIGH_SERIES)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [
        SwingType.SWING_HIGH, SwingType.SWING_LOW, SwingType.SWING_HIGH,
    ]
    first = points[0]
    assert first.swing_index == 1
    assert first.event_time == START + timedelta(minutes=6)
    assert first.event_price == Decimal(21)
    assert first.detection_time == START + timedelta(minutes=9)
    assert first.confirmation_close == Decimal(5)
    assert first.detection_time > first.event_time
    assert first.candidate_bar_index == 6
    assert first.confirmation_bar_index == 9
    assert first.bars_to_confirmation == 3
    assert first.atr_period == 1
    assert first.atr_at_extreme == Decimal(9)
    assert first.reversal_atr_multiplier == MULTIPLIER
    assert first.threshold_points == Decimal("13.5")
    assert first.previous_swing is None
    assert first.definition_id == SWING_POINT_DEFINITION_ID


def test_swing_low_fixture_confirms_symmetrically() -> None:
    atr, swing = feed(WARMUP + SWING_LOW_SERIES)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_LOW]
    point = points[0]
    assert point.event_time == START + timedelta(minutes=6)
    assert point.event_price == Decimal(1)
    assert point.detection_time == START + timedelta(minutes=9)
    assert point.confirmation_close == Decimal("29.5")
    assert point.candidate_bar_index == 6
    assert point.confirmation_bar_index == 9
    assert point.bars_to_confirmation == 3
    assert point.atr_at_extreme == Decimal(9)
    assert point.threshold_points == Decimal("13.5")
    assert point.event_time < point.detection_time
    # Bar 8's live ATR moved to 7.5 while the pending candidate stayed frozen
    # at the ATR captured when the extreme formed on bar 6.
    atr_before_confirmation, pending_swing = feed(WARMUP + SWING_LOW_SERIES[:4])
    assert atr_before_confirmation.state.values["atr"] == Decimal("7.5")
    pending_low = candidate(pending_swing, high=False)
    assert pending_low["atr_at_extreme"] == Decimal(9)
    assert pending_low["threshold_points"] == Decimal("13.5")
    assert pending_low["equal_extreme_count"] == 1
    assert pending_low["last_equal_extreme_time"] == START + timedelta(minutes=7)


def test_more_extreme_bar_replaces_candidate_and_freezes_new_threshold() -> None:
    _, swing = feed(WARMUP + SWING_HIGH_SERIES[:1])
    after_seed = candidate(swing, high=True)
    assert after_seed["event_price"] == Decimal(20)
    assert after_seed["atr_at_extreme"] == Decimal(11)
    assert after_seed["threshold_points"] == Decimal("16.5")
    _, swing = feed(WARMUP + SWING_HIGH_SERIES[:2])
    after_replacement = candidate(swing, high=True)
    assert after_replacement["event_time"] == START + timedelta(minutes=6)
    assert after_replacement["event_price"] == Decimal(21)
    assert after_replacement["atr_at_extreme"] == Decimal(9)
    assert after_replacement["threshold_points"] == Decimal("13.5")
    _, swing = feed(WARMUP + SWING_HIGH_SERIES)
    point = swing.confirmed_swing_points[0]
    assert point.threshold_points == Decimal("13.5")
    assert point.atr_at_extreme == Decimal(9)
    # Close 5 exceeds the discarded candidate's 20 - 16.5 boundary, so the
    # confirmation can only come from the replaced candidate's frozen threshold.
    assert point.confirmation_close == Decimal(5)


def test_frozen_atr_survives_later_atr_changes_for_unchanged_candidate() -> None:
    atr, swing = feed(WARMUP + SWING_HIGH_SERIES[:4])
    pending = candidate(swing, high=True)
    assert pending["event_price"] == Decimal(21)
    assert pending["atr_at_extreme"] == Decimal(9)
    assert pending["threshold_points"] == Decimal("13.5")
    assert atr.state.values["atr"] == Decimal("2.9")


def test_same_bar_extreme_cannot_confirm_itself() -> None:
    _, swing = feed(WARMUP + SAME_BAR_SERIES[:1])
    assert swing.state.values["confirmed_count"] == 0
    seeded = candidate(swing, high=True)
    assert seeded["status"] == "unconfirmed"
    assert seeded["event_price"] == Decimal(20)
    _, swing = feed(WARMUP + SAME_BAR_SERIES)
    points = swing.confirmed_swing_points
    assert len(points) == 1
    assert points[0].event_time == START + timedelta(minutes=5)
    assert points[0].detection_time == START + timedelta(minutes=6)
    assert points[0].confirmation_close == Decimal(5)
    assert points[0].threshold_points == Decimal(15)


def test_equal_high_keeps_earliest_event_time() -> None:
    _, swing = feed(WARMUP + SWING_HIGH_SERIES[:3])
    pending = candidate(swing, high=True)
    assert pending["event_time"] == START + timedelta(minutes=6)
    assert pending["equal_extreme_count"] == 1
    assert pending["last_equal_extreme_time"] == START + timedelta(minutes=7)
    assert pending["threshold_points"] == Decimal("13.5")
    _, swing = feed(WARMUP + SWING_HIGH_SERIES)
    point = swing.confirmed_swing_points[0]
    assert point.event_time == START + timedelta(minutes=6)
    assert point.event_price == Decimal(21)


def test_replaced_candidate_is_never_confirmed() -> None:
    multiplier = Decimal("1.0")
    _, swing = feed(WARMUP + REPLACED_SERIES[:2], reversal_atr_multiplier=multiplier)
    assert swing.state.values["confirmed_count"] == 0
    pending = candidate(swing, high=True)
    assert pending["event_price"] == Decimal(21)
    assert pending["threshold_points"] == Decimal(18)
    _, swing = feed(WARMUP + REPLACED_SERIES, reversal_atr_multiplier=multiplier)
    points = swing.confirmed_swing_points
    assert len(points) == 1
    assert points[0].event_time == START + timedelta(minutes=6)
    assert points[0].event_price == Decimal(21)
    assert points[0].detection_time == START + timedelta(minutes=7)
    assert points[0].confirmation_close == Decimal(3)
    assert points[0].threshold_points == Decimal(18)


def test_confirmed_swings_alternate_high_and_low() -> None:
    _, swing = feed(WARMUP + SWING_HIGH_SERIES)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [
        SwingType.SWING_HIGH, SwingType.SWING_LOW, SwingType.SWING_HIGH,
    ]
    assert [point.swing_index for point in points] == [1, 2, 3]
    assert points[1].event_time == START + timedelta(minutes=11)
    assert points[1].event_price == Decimal(3)
    assert points[1].detection_time == START + timedelta(minutes=14)
    assert points[1].threshold_points == Decimal(6)
    assert points[2].event_time == START + timedelta(minutes=15)
    assert points[2].event_price == Decimal(22)
    assert points[2].detection_time == START + timedelta(minutes=17)
    assert points[2].threshold_points == Decimal("10.5")
    assert points[2].previous_swing == SwingPointReference(
        2, SwingType.SWING_LOW, START + timedelta(minutes=11), Decimal(3),
    )
    # After the final confirmed high the component searches the next low.
    assert swing.state.values["search_direction"] == "low"


def test_declares_atr_dependent_warmup_and_initializes_without_emitting() -> None:
    for atr_period in (1, 14):
        atr, swing = build(atr_period)
        assert swing.warmup_completed_bars == atr.warmup_completed_bars == 5 * atr_period + 1
        assert swing.state.is_warm is False
        assert swing.state.values["search_active"] is False
        assert swing.state.values["provisional_candidate_high"] is None
        assert swing.state.values["provisional_candidate_low"] is None
        assert swing.state.values["confirmed"] == ()
        atr.update(WARMUP[0])
        swing.update(WARMUP[0])
        assert swing.state.values["search_active"] is False
        assert swing.state.values["provisional_candidate_high"] is None
        _, swing = feed(WARMUP + SWING_HIGH_SERIES[:1])
        assert swing.state.is_warm is True
        assert swing.state.values["search_active"] is True
        seeded_high = candidate(swing, high=True)
        seeded_low = candidate(swing, high=False)
        assert seeded_high["event_price"] == Decimal(20)
        assert seeded_low["event_price"] == Decimal(9)
        assert seeded_high["candidate_bar_index"] == 5
        assert seeded_high["threshold_points"] == Decimal("16.5")
        assert swing.state.values["confirmed_count"] == 0
        assert swing.state.values["search_direction"] == "both"


def test_monotone_dataset_never_synthesizes_a_boundary_swing() -> None:
    _, swing = feed(WARMUP + MONOTONE_SERIES)
    assert swing.state.is_warm is True
    assert swing.confirmed_swing_points == ()
    assert swing.state.values["confirmed_count"] == 0
    rising_high = candidate(swing, high=True)
    assert rising_high["event_price"] == Decimal(29)
    assert rising_high["candidate_bar_index"] == 12
    assert rising_high["status"] == "unconfirmed"


def test_unconfirmed_candidate_at_end_of_dataset_never_emits() -> None:
    _, swing = feed(WARMUP + SWING_HIGH_SERIES[:4])
    assert swing.state.is_warm is True
    assert swing.confirmed_swing_points == ()
    assert swing.state.values["confirmed_count"] == 0
    assert candidate(swing, high=True)["event_price"] == Decimal(21)
    assert candidate(swing, high=True)["status"] == "unconfirmed"


def test_swing_point_is_invisible_before_its_detection_time() -> None:
    atr, swing = build()
    series = WARMUP + SWING_HIGH_SERIES
    for item in series[:9]:
        atr.update(item)
        swing.update(item)
    assert swing.state.values["confirmed"] == ()
    assert swing.state.values["confirmed_count"] == 0
    # The candidate extreme is already observable, but only as explicitly
    # unconfirmed provisional state.
    assert candidate(swing, high=True)["event_time"] == START + timedelta(minutes=6)
    atr.update(series[9])
    swing.update(series[9])
    entries = confirmed_values(swing)
    assert len(entries) == 1
    assert entries[0]["status"] == "confirmed"
    assert entries[0]["event_time"] == START + timedelta(minutes=6)
    assert entries[0]["detection_time"] == START + timedelta(minutes=9)
    assert swing.confirmed_swing_points[0].detection_time == series[9].timestamp


def test_reset_and_replay_reproduce_identical_swings_and_evidence() -> None:
    series = WARMUP + SWING_HIGH_SERIES
    atr, swing = build()
    snapshots = []
    for item in series:
        atr.update(item)
        swing.update(item)
        snapshots.append(swing.debug_json())
    baseline = swing.confirmed_swing_points

    fresh_atr, fresh_swing = build()
    replayed = []
    for item in series:
        fresh_atr.update(item)
        fresh_swing.update(item)
        replayed.append(fresh_swing.debug_json())
    assert replayed == snapshots
    assert fresh_swing.confirmed_swing_points == baseline

    swing.reset()
    atr.reset()
    assert swing.state.values["confirmed_count"] == 0
    assert swing.state.values["provisional_candidate_high"] is None
    for index, item in enumerate(series):
        atr.update(item)
        swing.update(item)
        assert swing.debug_json() == snapshots[index]
    assert swing.confirmed_swing_points == baseline
    assert json.loads(swing.debug_json())["values"]["confirmed_count"] == 3


def test_incremental_stepping_never_references_data_beyond_current_index() -> None:
    atr, swing = build()
    series = WARMUP + SWING_HIGH_SERIES
    for index, item in enumerate(series):
        atr.update(item)
        swing.update(item)
        current = item.timestamp
        for point in swing.confirmed_swing_points:
            assert point.event_time <= point.detection_time <= current
            assert point.confirmation_bar_index <= index
            assert point.candidate_bar_index < point.confirmation_bar_index
            assert point.bars_to_confirmation == (
                point.confirmation_bar_index - point.candidate_bar_index
            )
        for pending in (candidate(swing, high=True), candidate(swing, high=False)):
            if pending is None:
                continue
            assert pending["status"] == "unconfirmed"
            assert pending["event_time"] <= current
            assert pending["candidate_bar_index"] <= index
        entry_times = [
            (entry["event_time"], entry["detection_time"])
            for entry in confirmed_values(swing)
        ]
        assert all(detection <= current for _, detection in entry_times)


def test_multiplier_change_acts_only_through_the_documented_threshold() -> None:
    fast_multiplier = Decimal("1.0")
    _, fast = feed(WARMUP + MULTIPLIER_SERIES, reversal_atr_multiplier=fast_multiplier)
    fast_points = fast.confirmed_swing_points
    assert len(fast_points) == 1
    assert fast_points[0].event_time == START + timedelta(minutes=6)
    assert fast_points[0].event_price == Decimal("20.5")
    assert fast_points[0].detection_time == START + timedelta(minutes=7)
    assert fast_points[0].atr_at_extreme == Decimal("7.5")
    assert fast_points[0].threshold_points == Decimal("7.5")
    assert fast_points[0].reversal_atr_multiplier == fast_multiplier
    assert fast_points[0].confirmation_close == Decimal("12.6")

    _, slow = feed(WARMUP + MULTIPLIER_SERIES, reversal_atr_multiplier=MULTIPLIER)
    assert slow.confirmed_swing_points == ()
    pending = candidate(slow, high=True)
    assert pending["event_price"] == Decimal("20.5")
    assert pending["event_time"] == START + timedelta(minutes=6)
    assert pending["threshold_points"] == Decimal("11.25")
    assert pending["status"] == "unconfirmed"


def test_structured_output_contains_confirmation_evidence_and_lineage() -> None:
    expected_hash = detection_config_hash(run_config())
    _, swing = feed(WARMUP + SWING_HIGH_SERIES, pinned_config_hash=expected_hash)
    points = swing.confirmed_swing_points
    second = points[1]
    assert isinstance(second, SwingPoint)
    assert second.swing_index == 2
    assert second.swing_type == SwingType.SWING_LOW
    assert second.definition_id == SWING_POINT_DEFINITION_ID == "ATR_DIRECTIONAL_CHANGE_SWING_V1"
    assert second.event_time == START + timedelta(minutes=11)
    assert second.event_price == Decimal(3)
    assert second.detection_time == START + timedelta(minutes=14)
    assert second.confirmation_close == Decimal(18)
    assert second.candidate_bar_index == 11
    assert second.confirmation_bar_index == 14
    assert second.bars_to_confirmation == 3
    assert second.atr_period == 1
    assert second.atr_at_extreme == Decimal(4)
    assert second.reversal_atr_multiplier == MULTIPLIER
    assert second.threshold_points == Decimal(6)
    assert second.previous_swing == SwingPointReference(
        1, SwingType.SWING_HIGH, START + timedelta(minutes=6), Decimal(21),
    )
    assert second.detection_config_hash == expected_hash
    with pytest.raises(FrozenInstanceError):
        second.event_price = Decimal(1)  # type: ignore[misc]
    entry = confirmed_values(swing)[1]
    assert entry["definition_id"] == SWING_POINT_DEFINITION_ID
    assert entry["threshold_points"] == Decimal(6)
    assert entry["previous_swing"] == {
        "swing_index": 1,
        "swing_type": "SWING_HIGH",
        "event_time": START + timedelta(minutes=6),
        "event_price": Decimal(21),
    }
    assert entry["detection_config_hash"] == expected_hash
    assert swing.state.values["pinned_config_hash"] == expected_hash


def test_registered_defaults_resolve_and_participate_in_config_hash() -> None:
    implicit = DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(ComponentSelection(component_id="swing_point", component_version="1"),),
    )
    explicit = run_config(with_atr=False, atr_period=14)
    resolved_implicit = resolve_detection_config(implicit)
    resolved_explicit = resolve_detection_config(explicit)
    assert resolved_implicit == resolved_explicit
    assert {
        item.name: item.value for item in resolved_implicit.components[0].parameters
    } == selection_parameters(14)
    assert detection_config_hash(implicit) == detection_config_hash(explicit)
    varied = run_config(with_atr=False, reversal_atr_multiplier=Decimal("1.0"))
    assert detection_config_hash(explicit) != detection_config_hash(varied)


@pytest.mark.parametrize(
    "override",
    [
        {"extreme_source": "WICK"},
        {"confirmation_source": "HIGH"},
        {"equal_extreme_policy": "REPLACE_LATEST"},
        {"freeze_atr_at_extreme": False},
        {"allow_same_bar_confirmation": True},
        {"require_alternation": False},
    ],
)
def test_unsupported_v1_options_are_rejected_at_config_resolution(
    override: dict[str, object],
) -> None:
    config = run_config(with_atr=False, **override)
    with pytest.raises(ValueError, match="must be one of"):
        resolve_detection_config(config)


def test_component_requires_enabled_matching_selections_and_parameters() -> None:
    bare = run_config(with_swing_point=False)
    with pytest.raises(MarketStateError, match="enabled SwingPoint v1 selection"):
        SwingPointState(bare, AtrState(bare))
    with pytest.raises(MarketStateError, match="exactly its registered parameters"):
        SwingPointState(run_config(omit_parameter="require_alternation"), AtrState(run_config()))
    with pytest.raises(MarketStateError, match="exactly its registered parameters"):
        SwingPointState(run_config(extra_parameter="legacy"), AtrState(run_config()))
    with pytest.raises(MarketStateError, match="enabled SwingPoint v1 selection"):
        SwingPointState(run_config(component_version="2"), AtrState(run_config()))
    with pytest.raises(MarketStateError, match="enabled SwingPoint v1 selection"):
        SwingPointState(run_config(enabled=False), AtrState(run_config()))


@pytest.mark.parametrize(
    "overrides",
    [
        {"atr_period": 0},
        {"atr_period": True},
        {"atr_period": "14"},
        {"reversal_atr_multiplier": 0},
        {"reversal_atr_multiplier": "1.5"},
        {"extreme_source": "WICK"},
        {"confirmation_source": "WICK"},
        {"equal_extreme_policy": "REPLACE_LATEST"},
        {"freeze_atr_at_extreme": False},
        {"allow_same_bar_confirmation": True},
        {"require_alternation": False},
    ],
)
def test_component_rejects_unsupported_parameter_values(overrides: dict[str, object]) -> None:
    with pytest.raises(MarketStateError):
        build(**overrides)


def test_component_rejects_mismatched_or_foreign_atr_dependency() -> None:
    with pytest.raises(MarketStateError, match="injected AtrState"):
        SwingPointState(run_config(), object())  # type: ignore[arg-type]
    with pytest.raises(MarketStateError, match="run config differs"):
        SwingPointState(run_config(atr_period=2), AtrState(run_config()))
    with pytest.raises(MarketStateError, match="match the injected ATR period"):
        config = run_config(atr_period=1, atr_component_period=2)
        SwingPointState(config, AtrState(config))
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        build(pinned_config_hash="")


def test_atr_must_be_updated_with_each_bar_before_the_swing_component() -> None:
    atr, swing = build()
    with pytest.raises(MarketStateError, match="updated with each bar first"):
        swing.update(WARMUP[0])
    atr.update(WARMUP[0])
    swing.update(WARMUP[0])
    before = swing.debug_json()
    with pytest.raises(MarketStateError, match="updated with each bar first"):
        swing.update(WARMUP[1])
    assert swing.debug_json() == before


def test_observable_state_is_deterministic_read_only_and_detached() -> None:
    atr, swing = build()
    for item in WARMUP + SWING_HIGH_SERIES[:2]:
        atr.update(item)
        swing.update(item)
    snapshot = swing.state
    with pytest.raises(TypeError):
        snapshot.values["confirmed_count"] = 9  # type: ignore[index]
    with pytest.raises(TypeError):
        candidate(swing, high=True)["event_price"] = Decimal(1)  # type: ignore[index]
    assert snapshot == swing.state
    atr.update(SWING_HIGH_SERIES[2])
    swing.update(SWING_HIGH_SERIES[2])
    assert snapshot.values["confirmed_count"] == 0
    assert swing.state.values["confirmed_count"] == 0
    other_atr, other_swing = build()
    for item in WARMUP + SWING_HIGH_SERIES[:3]:
        other_atr.update(item)
        other_swing.update(item)
    assert swing.debug_json() == other_swing.debug_json()


WARMUP14 = tuple(bar(i, "11", "9", "10") for i in range(5 * 14 + 1))


def test_deep_wick_between_anchor_and_confirmation_is_carried() -> None:
    # After SWING_HIGH(21@6) confirms at bar 9, the next low candidate must
    # carry the deepest low since bar 6 — including bar 9's own wick (4) —
    # instead of reseeding from the confirmation bar's default view.
    _, swing = feed(WARMUP + SWING_HIGH_SERIES[:6])
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_HIGH]
    carried = candidate(swing, high=False)
    assert carried["event_price"] == Decimal(4)
    assert carried["candidate_bar_index"] == 9
    assert carried["atr_at_extreme"] == Decimal("14.5")
    assert carried["threshold_points"] == Decimal("21.75")


def test_opposite_window_resets_when_the_searched_anchor_moves() -> None:
    # A deep low that predates a later high replacement is excluded from the
    # carried window: the opposite running extreme re-anchors when the
    # searched candidate's anchor bar moves.
    series = (
        bar(5, "20", "12", "19"),
        bar(6, "19.9", "2", "18"),
        bar(7, "21", "18", "20.5"),
        bar(8, "20.9", "7", "8"),
    )
    _, swing = feed(WARMUP + series)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_HIGH]
    assert points[0].event_price == Decimal(21)
    assert points[0].candidate_bar_index == 7
    carried = candidate(swing, high=False)
    # The carried low is the confirmation bar's own low (7), not the deep low
    # (2) that predates the bar-7 high replacement: without the window reset
    # the carried candidate would be 2@6.
    assert carried["event_price"] == Decimal(7)
    assert carried["candidate_bar_index"] == 8


def test_dual_cross_close_confirms_high_first_and_defers_the_low() -> None:
    series = (
        bar(5, "20", "10", "15"),
        bar(6, "21", "5", "20"),
        bar(7, "14", "12", "13"),
        bar(8, "14", "12.5", "14"),
    )
    _, swing = feed(WARMUP + series[:3], reversal_atr_multiplier=Decimal("0.3"))
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_HIGH]
    assert points[0].event_price == Decimal(21)
    assert points[0].candidate_bar_index == 6
    assert points[0].detection_time == START + timedelta(minutes=7)
    assert points[0].confirmation_close == Decimal(13)
    assert swing.state.values["search_direction"] == "low"
    carried = candidate(swing, high=False)
    assert carried["event_price"] == Decimal(5)
    assert carried["candidate_bar_index"] == 6
    _, swing = feed(WARMUP + series, reversal_atr_multiplier=Decimal("0.3"))
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_HIGH, SwingType.SWING_LOW]
    assert points[1].event_price == Decimal(5)
    assert points[1].candidate_bar_index == 6
    assert points[1].detection_time == START + timedelta(minutes=8)


def test_default_config_warmup_feeds_real_bars_without_emitting() -> None:
    atr, swing = build(atr_period=14)
    for index, item in enumerate(WARMUP14):
        atr.update(item)
        swing.update(item)
        assert swing.state.values["confirmed_count"] == 0
        if index < len(WARMUP14) - 1:
            assert swing.state.values["search_active"] is False
            assert swing.state.values["provisional_candidate_high"] is None
    assert swing.state.is_warm is True
    assert swing.state.values["search_active"] is True
    seeded = candidate(swing, high=True)
    assert seeded["event_price"] == Decimal(11)
    assert seeded["candidate_bar_index"] == len(WARMUP14) - 1


def test_default_config_boundary_initialization_deviation_is_pinned() -> None:
    # Documented v1 deviation (SCRUM-74 acceptance criterion 8): v1 has no
    # pre-window observation, so a monotone window can confirm one swing
    # anchored at the first post-warm-up bar once the threshold reversal is
    # observable. Material changes to this initialization semantics require a
    # new definition version per the versioning contract.
    rising = tuple(
        bar(71 + offset, str(22 + offset), str(20 + offset), str(21 + offset))
        for offset in range(5)
    )
    _, swing = feed(WARMUP14 + rising, atr_period=14)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_LOW]
    assert points[0].candidate_bar_index == len(WARMUP14) - 1
    assert points[0].confirmation_bar_index == len(WARMUP14)
    falling = tuple(
        bar(71 + offset, "10.5", str(9 - offset), str(9.5 - offset))
        for offset in range(5)
    )
    _, swing = feed(WARMUP14 + falling, atr_period=14)
    points = swing.confirmed_swing_points
    assert [point.swing_type for point in points] == [SwingType.SWING_HIGH]
    assert points[0].candidate_bar_index == len(WARMUP14) - 1
    assert points[0].confirmation_bar_index == len(WARMUP14) + 2
