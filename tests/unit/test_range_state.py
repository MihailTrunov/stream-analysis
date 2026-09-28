from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from market_analysis.api.app import app
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.component_registry import RANGE_STATE_V1_PARAMETERS
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    RANGE_STATE_DEFINITION_ID,
    AtrState,
    ChopCategory,
    CompressionCategory,
    MarketStateError,
    RangeAvailability,
    RangeState,
)
from market_analysis.patterns import PatternDefinitionError

START = datetime(2026, 1, 5, 12, tzinfo=UTC)


def config(*, alias: str | None = None, atr_period: int = 14,
           **parameters: object) -> DetectionAnalysisConfig:
    return resolve_detection_config(DetectionAnalysisConfig(
        instrument_id="US30", timeframe=Timeframe.M1, calendar_id="cal-v1",
        components=(
            ComponentSelection(component_id="atr", component_version="1",
                               parameters=(ConfigParameter(name="period", value=atr_period),)),
            ComponentSelection(component_id="range_state", component_version="1",
                               instance_id=alias, parameters=tuple(
                                   ConfigParameter(name=k, value=cast(Any, v))
                                   for k, v in parameters.items()
                               )),
        ),
    ))


def build(cfg: DetectionAnalysisConfig | None = None,
          *, instance_id: str = "range_state") -> tuple[AtrState, RangeState]:
    cfg = cfg or config()
    atr = AtrState(cfg)
    return atr, RangeState(cfg, atr, run_id="run-1", dataset_revision_id="revision-1",
                           instance_id=instance_id, pinned_config_hash=detection_config_hash(cfg))


def bar(index: int, close: str | int = 10, high: str | int | None = None,
        low: str | int | None = None) -> Bar:
    value = Decimal(close)
    return Bar("US30", Timeframe.M1, START + timedelta(minutes=index),
               value, value if high is None else Decimal(high),
               value if low is None else Decimal(low), value)


def update(chain: tuple[AtrState, RangeState], item: Bar) -> None:
    for component in chain:
        component.update(item)


def test_hand_calculated_true_range_chop_and_population_bandwidth() -> None:
    chain = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2))
    # TRs: 4, max(4, |14-8|, |10-8|)=6. Envelope 14-6=8.
    # CHOP = 100*log10(10/8)/log10(2) = 32.19280948873623478703194294893902.
    update(chain, bar(0, 8, 10, 6))
    update(chain, bar(1, 12, 14, 10))
    result = chain[1].range_state
    assert result.chop.sum_true_range == 10
    assert result.chop.window_range == 8
    assert result.choppiness_score == Decimal("32.19280948873623478703194294893902")
    assert result.choppiness_regime == ChopCategory.DIRECTIONAL
    # Closes 8,12: mean10, populationvariance4, stddev2, bands14/6, relativewidth0.8.
    width = result.bandwidth_evidence
    assert (width.basis, width.variance, width.stddev, width.upper_band,
            width.lower_band, result.bandwidth) == tuple(map(Decimal, (10, 4, 2, 14, 6, "0.8")))
    assert chain[0].volatility_state.atr is None  # ATR smoothing never gates current TR.
    assert result.compression_regime == CompressionCategory.UNAVAILABLE
    assert result.chop.window[1].timestamp == bar(1).timestamp
    assert result.definition_id == RANGE_STATE_DEFINITION_ID


@pytest.mark.parametrize(("value", "expected"), [
    ("38.199999", ChopCategory.DIRECTIONAL), ("38.2", ChopCategory.TRANSITIONAL),
    ("61.8", ChopCategory.TRANSITIONAL), ("61.800001", ChopCategory.RANGE_LIKE),
])
def test_exact_default_choppiness_cutoffs(value: str, expected: ChopCategory) -> None:
    assert build()[1]._chop_category(Decimal(value)) == expected


@pytest.mark.parametrize(("value", "expected"), [
    ("80", CompressionCategory.COMPRESSED), ("79.999999", CompressionCategory.NORMAL),
    ("20.000001", CompressionCategory.NORMAL), ("20", CompressionCategory.EXPANDED),
])
def test_exact_default_compression_cutoffs(value: str, expected: CompressionCategory) -> None:
    assert build()[1]._compression_category(Decimal(value)) == expected


def test_default_independent_14_20_139_warmup_and_bounded_evidence() -> None:
    chain = build()
    assert chain[1].warmup_completed_bars == 139
    initial = chain[1].range_state
    assert initial.choppiness_regime == ChopCategory.UNAVAILABLE
    assert initial.compression_regime == CompressionCategory.UNAVAILABLE
    for index in range(150):
        update(chain, bar(index, 10 + index % 2, 12, 9))
        result = chain[1].range_state
        assert result.chop.status == (RangeAvailability.AVAILABLE if index >= 13
                                      else RangeAvailability.WARMING_UP)
        assert result.bandwidth_evidence.status == (RangeAvailability.AVAILABLE if index >= 19
                                                    else RangeAvailability.WARMING_UP)
        assert result.compression.status == (RangeAvailability.AVAILABLE if index >= 138
                                             else RangeAvailability.WARMING_UP)
        assert chain[1].state.is_warm == (index >= 138)
    assert len(result.chop.window) == 14
    assert len(result.bandwidth_evidence.window) == 20
    assert len(result.compression.reference) == 120
    assert result.chop.window[0].timestamp == bar(136).timestamp
    assert result.compression.reference[0].timestamp == bar(30).timestamp
    assert result.compression.tie_count == 119
    assert result.compression.strict_lower_count == 0
    assert result.bandwidth_percentile == 0
    assert result.compression_score == 100
    assert result.compression_regime == CompressionCategory.COMPRESSED
    payload = json.loads(chain[1].debug_json())["values"]
    for name in ("choppiness_score", "choppiness_regime", "bandwidth", "bandwidth_percentile",
                 "compression_score", "compression_regime"):
        assert name in payload
    assert payload["bandwidth_evidence"]["variance"] != "0"


def test_zero_range_and_zero_basis_are_independent_unavailable_axes() -> None:
    chain = build(config(chop_period=2, bandwidth_period=1, compression_reference_bars=2))
    update(chain, bar(0))
    update(chain, bar(1))
    result = chain[1].range_state
    assert result.chop.status == RangeAvailability.DEGENERATE
    assert result.chop.reason == "ZERO_WINDOW_RANGE"
    assert result.chop.window_range == 0
    assert result.choppiness_score is None
    assert result.choppiness_regime == ChopCategory.UNAVAILABLE
    assert result.bandwidth == 0
    assert result.compression_regime == CompressionCategory.COMPRESSED
    reference = result.compression.reference
    update(chain, bar(2, 0))
    result = chain[1].range_state
    assert result.chop.status == RangeAvailability.AVAILABLE
    assert result.bandwidth_evidence.status == RangeAvailability.DEGENERATE
    assert result.bandwidth_evidence.reason == "ZERO_BASIS"
    assert result.bandwidth is result.bandwidth_percentile is result.compression_score is None
    assert result.compression_regime == CompressionCategory.UNAVAILABLE
    assert result.compression.status == RangeAvailability.DEGENERATE
    assert result.compression.reference == reference  # Invalid values do not evict valid history.
    update(chain, bar(3))
    assert chain[1].range_state.compression.status == RangeAvailability.AVAILABLE
    assert [item.timestamp for item in chain[1].range_state.compression.reference] == [
        bar(1).timestamp, bar(3).timestamp,
    ]


def test_population_variance_nonzero_at_zero_basis_preserves_diagnostics() -> None:
    chain = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2))
    update(chain, bar(0, -1))
    update(chain, bar(1, 1))
    width = chain[1].range_state.bandwidth_evidence
    assert width.status == RangeAvailability.DEGENERATE
    assert (width.basis, width.variance, width.stddev, width.upper_band, width.lower_band) == (
        Decimal(0), Decimal(1), Decimal(1), Decimal(2), Decimal(-2),
    )


def test_dependency_preflight_does_not_gate_early_axes_and_zero_multiplier_is_supported() -> None:
    chain = build(config(atr_period=100, chop_period=2, bandwidth_period=2,
                         compression_reference_bars=2, bandwidth_stddev_multiplier=Decimal(0)))
    assert chain[1].warmup_completed_bars == 501
    for index, close in enumerate([8, 12, 10]):
        update(chain, bar(index, close, 14, 6))
    result = chain[1].range_state
    assert not chain[1].state.is_warm
    assert chain[0].volatility_state.atr is None
    assert result.chop.status == RangeAvailability.AVAILABLE
    assert result.bandwidth_evidence.status == RangeAvailability.AVAILABLE
    assert result.bandwidth == 0
    assert result.bandwidth_evidence.stddev == 1
    assert result.bandwidth_evidence.upper_band == result.bandwidth_evidence.lower_band == 11
    assert result.compression.status == RangeAvailability.AVAILABLE


def test_strict_rank_rolling_eviction_and_inclusive_score_boundaries_from_real_widths() -> None:
    # With period2, multiplier2: width=4*abs(a-b)/(a+b). [1,1,2,1,1,2,1]
    # produces widths [0,4/3,4/3,0,4/3,4/3], with 2 lower and 3 equal prior widths.
    chain = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=6))
    for index, close in enumerate([1, 1, 2, 1, 1, 2, 1]):
        update(chain, bar(index, close))
    result = chain[1].range_state.compression
    assert result.strict_lower_count == 2
    assert result.tie_count == 3
    assert result.percentile == Decimal("0.4")
    assert result.score == 60
    assert result.category == CompressionCategory.NORMAL
    update(chain, bar(7, 1))
    result = chain[1].range_state.compression
    assert result.strict_lower_count == 0
    assert result.tie_count == 1
    assert result.category == CompressionCategory.COMPRESSED
    assert result.reference[0].timestamp == bar(2).timestamp
    # Exact expanded20 and compressed80 from a full 6-observation reference.
    for sequence, score, category in (
        ([1, 1, 1, 1, 1, 2, 1], 20, CompressionCategory.EXPANDED),
        ([1, 1, 2, 1, 2, 1, 2], 80, CompressionCategory.COMPRESSED),
    ):
        boundary = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=6))
        for index, close in enumerate(sequence):
            update(boundary, bar(index, close))
        assert boundary[1].range_state.compression.score == score
        assert boundary[1].range_state.compression.category == category


def test_wide_noisy_and_quiet_drift_describe_two_independent_axes() -> None:
    noisy = build(config(chop_period=3, bandwidth_period=3, compression_reference_bars=2))
    drift = build(config(chop_period=3, bandwidth_period=3, compression_reference_bars=2))
    for index, close in enumerate([9, 11, 9, 14]):
        update(noisy, bar(index, close, 16 if index == 3 else 12, 8))
    for index, close in enumerate([100, 101, 102, 103]):
        update(drift, bar(index, close))
    assert noisy[1].range_state.choppiness_regime == ChopCategory.RANGE_LIKE
    assert noisy[1].range_state.compression_regime == CompressionCategory.EXPANDED
    assert drift[1].range_state.choppiness_regime == ChopCategory.DIRECTIONAL
    noisy_width, drift_width = noisy[1].range_state.bandwidth, drift[1].range_state.bandwidth
    assert noisy_width is not None and drift_width is not None
    assert noisy_width > drift_width
    assert drift[1].range_state.compression_regime == CompressionCategory.COMPRESSED


def test_multiplier_configuration_changes_actual_width_without_changing_chop() -> None:
    normal = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2))
    wider = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2,
                         bandwidth_stddev_multiplier=Decimal(3)))
    for index, close in enumerate([8, 12]):
        for chain in (normal, wider):
            update(chain, bar(index, close, 14, 6))
    assert normal[1].range_state.bandwidth == Decimal("0.8")
    assert wider[1].range_state.bandwidth == Decimal("1.2")
    assert normal[1].range_state.chop == wider[1].range_state.chop


def test_rolling_permutations_preserve_population_width_and_strict_empirical_ties() -> None:
    chain = build(config(chop_period=3, bandwidth_period=3, compression_reference_bars=2))
    widths = []
    windows = []
    for index, close in enumerate([1, 3, 7, 1, 3, 7]):
        update(chain, bar(index, close))
        result = chain[1].range_state
        if index < 2:
            continue
        widths.append(result.bandwidth)
        windows.append(tuple(item.close for item in result.bandwidth_evidence.window))
        # Every complete rolling window is the same multiset: mean11/3,
        # population variance56/9. Temporal evidence retains its input order.
        assert sorted(windows[-1]) == [Decimal(1), Decimal(3), Decimal(7)]
        assert result.bandwidth_evidence.basis == Decimal("3.666666666666666666666666666666667")
        variance = result.bandwidth_evidence.variance
        assert variance is not None
        assert abs(variance - Decimal("6.222222222222222222222222222222222")) <= Decimal("1e-32")
        if index >= 3:
            assert result.compression.strict_lower_count == 0
            assert result.compression.tie_count == 1
            assert result.bandwidth_percentile == 0
            assert result.compression_score == 100
            assert result.compression_regime == CompressionCategory.COMPRESSED
    assert len(set(widths)) == 1
    assert len(set(windows)) == 3


def test_scale_equivalent_population_windows_preserve_exact_empirical_ties() -> None:
    chain = build(config(chop_period=3, bandwidth_period=3, compression_reference_bars=2))
    widths = []
    closes = ["10", "11", "12.1", "13.31", "14.641", "16.1051"]
    for index, close in enumerate(closes):
        update(chain, bar(index, close))
        if index < 2:
            continue
        result = chain[1].range_state
        window = [Fraction(item.close) for item in result.bandwidth_evidence.window]
        # Independent exact oracle: each window is a 10% scaling of its prior
        # window, and its relative population BandWidth squared is 32/331.
        mean = sum(window) / 3
        population_variance = sum((value - mean) ** 2 for value in window) / 3
        assert 16 * population_variance / (mean * mean) == Fraction(32, 331)
        assert result.bandwidth_evidence.bandwidth_squared == Decimal(
            "0.09667673716012084592145015105740181"
        )
        widths.append(result.bandwidth)
        if index >= 3:
            assert result.compression.strict_lower_count == 0
            assert result.compression.tie_count == 1
            assert result.bandwidth_percentile == 0
            assert result.compression_score == 100
            assert result.compression_regime == CompressionCategory.COMPRESSED
    assert len(set(widths)) == 1


def test_exact_relative_width_avoids_band_cancellation_and_preserves_basis_sign() -> None:
    chain = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2))
    for index, close in enumerate([
        "100000000000000000000000000000000000",
        "100000000000000000000000000000000001",
    ]):
        update(chain, bar(index, close))
    result = chain[1].range_state
    assert result.bandwidth_evidence.variance == Decimal("0.25")
    assert result.bandwidth_evidence.stddev == Decimal("0.5")
    # Rounded bands collapse at this scale, but exact relative width is positive.
    assert result.bandwidth_evidence.upper_band == result.bandwidth_evidence.lower_band
    assert result.bandwidth == Decimal("2e-35")
    negative = build(config(chop_period=2, bandwidth_period=2, compression_reference_bars=2))
    update(negative, bar(0, -8))
    update(negative, bar(1, -12))
    assert negative[1].range_state.bandwidth_evidence.basis == -10
    assert negative[1].range_state.bandwidth == Decimal("-0.8")


@pytest.mark.parametrize(("name", "value"), [
    ("chop_period", 1), ("chop_period", True), ("bandwidth_period", 0),
    ("compression_reference_bars", 1), ("bandwidth_stddev_multiplier", Decimal(-1)),
    ("chop_directional_threshold", Decimal(-1)), ("chop_range_threshold", Decimal(101)),
    ("expanded_score_threshold", Decimal(-1)), ("compressed_score_threshold", Decimal(101)),
    ("bandwidth_stddev_mode", "SAMPLE"), ("compression_percentile_mode", "INCLUSIVE"),
    ("chop_directional_threshold", Decimal("61.8")),
    ("expanded_score_threshold", Decimal(80)),
])
def test_configuration_resolution_rejects_invalid_and_ambiguous_parameters(
    name: str, value: object,
) -> None:
    with pytest.raises(PatternDefinitionError):
        config(**{name: value})


@pytest.mark.parametrize(("name", "value"), [
    ("chop_period", True), ("chop_period", 1), ("chop_period", "14"),
    ("bandwidth_stddev_multiplier", "2"), ("bandwidth_stddev_multiplier", Decimal("NaN")),
    ("bandwidth_stddev_multiplier", Decimal("Infinity")), ("bandwidth_period", 0),
    ("compression_reference_bars", 1), ("compressed_score_threshold", Decimal(101)),
    ("chop_directional_threshold", Decimal("61.8")),
    ("expanded_score_threshold", Decimal(80)),
    ("bandwidth_stddev_mode", "SAMPLE"), ("compression_percentile_mode", "INCLUSIVE"),
])
def test_constructor_independently_validates_forged_unresolved_parameters(
    name: str, value: object,
) -> None:
    cfg = config()
    atr = AtrState(cfg)
    selection = next(item for item in cfg.components if item.component_id == "range_state")
    changed = selection.model_copy(update={"parameters": tuple(
        item.model_copy(update={"value": value}) if item.name == name else item
        for item in selection.parameters
    )})
    cfg = cfg.model_copy(update={"components": tuple(
        changed if item.component_id == "range_state" else item for item in cfg.components
    )})
    with pytest.raises(MarketStateError):
        RangeState(cfg, atr, run_id="run", dataset_revision_id="revision")


def test_alias_defaults_and_each_parameter_enter_canonical_hash() -> None:
    cfg = config()
    assert len(next(s for s in cfg.components if s.component_id == "range_state").parameters) == 10
    aliased = config(alias="ranges")
    chain = build(aliased, instance_id="ranges")
    assert chain[1].range_state.instance_id == "ranges"
    assert chain[1].lineage.detection_config_hash == detection_config_hash(aliased)
    assert detection_config_hash(config(alias="range_state")) == detection_config_hash(cfg)
    assert detection_config_hash(aliased) != detection_config_hash(cfg)
    for name, value in {
        "chop_period": 15, "chop_directional_threshold": Decimal(38),
        "chop_range_threshold": Decimal(62), "bandwidth_period": 21,
        "bandwidth_stddev_multiplier": Decimal(3), "compression_reference_bars": 121,
        "compressed_score_threshold": Decimal(81), "expanded_score_threshold": Decimal(19),
    }.items():
        assert detection_config_hash(config(**{name: value})) != detection_config_hash(cfg)
    omitted = DetectionAnalysisConfig(instrument_id="US30", calendar_id="cal-v1", components=(
        ComponentSelection(component_id="atr", component_version="1"),
        ComponentSelection(component_id="range_state", component_version="1"),
    ))
    assert detection_config_hash(omitted) == detection_config_hash(cfg)


@pytest.mark.parametrize("field", ["run_id", "dataset_revision_id"])
@pytest.mark.parametrize("value", ["", " ", None, 4])
def test_lineage_must_be_explicit_nonempty_strings(field: str, value: object) -> None:
    cfg = config()
    args = {"run_id": "run", "dataset_revision_id": "revision", field: value}
    with pytest.raises(MarketStateError):
        RangeState(cfg, AtrState(cfg), **cast(Any, args))


def test_constructor_rejects_absent_disabled_version_missing_parameters_and_hash() -> None:
    cfg = config()
    selection = next(item for item in cfg.components if item.component_id == "range_state")
    for selection_change in ({"enabled": False}, {"component_version": "2"}, {"parameters": ()}):
        changed = cfg.model_copy(update={"components": (cfg.components[0],
                                 selection.model_copy(update=selection_change))})
        with pytest.raises(MarketStateError):
            RangeState(changed, AtrState(cfg), run_id="run", dataset_revision_id="revision")
    with pytest.raises(MarketStateError, match="selection"):
        RangeState(cfg.model_copy(update={"components": cfg.components[:1]}), AtrState(cfg),
                   run_id="run", dataset_revision_id="revision")
    with pytest.raises(MarketStateError, match="pinned_config_hash"):
        RangeState(cfg, AtrState(cfg), run_id="run", dataset_revision_id="revision",
                   pinned_config_hash="forged")
    with pytest.raises(MarketStateError, match="config/hash"):
        RangeState(config(chop_period=3), AtrState(cfg), run_id="run",
                   dataset_revision_id="revision")
    with pytest.raises(MarketStateError, match="AtrState"):
        RangeState(cfg, cast(Any, object()), run_id="run", dataset_revision_id="revision")


def test_exact_lockstep_negative_lookahead_skips_and_failed_update_are_atomic() -> None:
    chain = build()
    before = chain[1].debug_json()
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[1].update(bar(0))
    assert chain[1].debug_json() == before
    chain[0].update(bar(0))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[1].update(bar(0, 11))
    chain[1].update(bar(0))
    before = chain[1].debug_json()
    chain[0].update(bar(1))
    chain[0].update(bar(2))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[1].update(bar(1))
    with pytest.raises(MarketStateError, match="exact bar"):
        chain[1].update(bar(2))
    assert chain[1].debug_json() == before


def test_upstream_reset_same_count_and_config_hash_mutation_are_rejected() -> None:
    chain = build()
    update(chain, bar(0))
    chain[0].reset()
    chain[0].update(bar(0))
    chain[0].update(bar(1))
    before = chain[1].debug_json()
    with pytest.raises(MarketStateError, match="upstream reset"):
        chain[1].update(bar(1))
    assert chain[1].debug_json() == before
    chain = build()
    chain[0].update(bar(0))
    chain[1].detection_config_hash = "forged"
    with pytest.raises(MarketStateError, match="config/hash"):
        chain[1].update(bar(0))


def test_upstream_config_mutation_and_invalid_current_true_range_are_rejected_atomically() -> None:
    chain = build()
    chain[0].update(bar(0))
    before = chain[1].debug_json()
    chain[0]._run_config = config(chop_period=3)
    with pytest.raises(MarketStateError, match="config/hash"):
        chain[1].update(bar(0))
    assert chain[1].debug_json() == before
    for value in (None, Decimal(-1), Decimal("NaN"), Decimal("Infinity")):
        chain = build()
        chain[0].update(bar(0))
        before = chain[1].debug_json()
        chain[0]._true_range = value
        with pytest.raises(MarketStateError, match="True Range"):
            chain[1].update(bar(0))
        assert chain[1].debug_json() == before


def test_real_atr_chain_replay_precision_immutability_and_scheduled_gap_continuity() -> None:
    chain = build(config(chop_period=3, bandwidth_period=2, compression_reference_bars=3))
    bars = [bar(0, 8, 10, 6), bar(1, 12, 14, 10), bar(2, 12, 13, 11),
            replace(bar(3, 11, 13, 9), timestamp=START + timedelta(days=3)),
            replace(bar(4, 10, 12, 8), timestamp=START + timedelta(days=3, minutes=1))]
    for item in bars:
        update(chain, item)
    result = chain[1].range_state
    # Last three TRs are 2,4,4; their envelope13-8=5, so sum/range=2.
    assert result.chop.sum_true_range == 10
    assert result.chop.window_range == 5
    assert result.choppiness_score is not None
    # High-precision analytic constant; 34-digit intermediate logs can differ by one ulp.
    assert abs(result.choppiness_score - Decimal("63.09297535714574370995271143427607")) <= (
        Decimal("1e-32")
    )
    assert [item.timestamp for item in result.chop.window] == [b.timestamp for b in bars[-3:]]
    assert chain[1].state.completed_bars == 5
    snapshot = chain[1].state
    with pytest.raises(FrozenInstanceError):
        cast(Any, result.lineage).run_id = "changed"
    with pytest.raises(FrozenInstanceError):
        cast(Any, result.chop.window[0]).high = Decimal(99)
    with pytest.raises(TypeError):
        cast(Any, snapshot.values)["choppiness_score"] = Decimal(99)
    with pytest.raises(TypeError):
        cast(Any, snapshot.values["chop"])["window"][0]["high"] = Decimal(99)
    with pytest.raises(AttributeError):
        cast(Any, chain[1]).lineage = result.lineage
    before = chain[1].debug_json()
    for component in reversed(chain):
        component.reset()
    assert chain[1].range_state.chop.window == ()
    assert chain[1].range_state.bandwidth_evidence.window == ()
    assert chain[1].range_state.compression.reference == ()
    with localcontext() as context:
        context.prec = 5
        for item in bars:
            update(chain, item)
    assert chain[1].debug_json() == before
    assert snapshot.values == chain[1].state.values


def test_component_definition_and_server_preview_enforce_exact_registered_contract() -> None:
    client = TestClient(app)
    response = client.get("/component-definitions")
    assert response.status_code == 200
    definition = next(item for item in response.json()["components"]
                      if item["component_id"] == "range_state")
    assert definition["component_version"] == "1"
    published = {item["parameter_id"]: item for item in definition["parameters"]}
    assert set(published) == {spec.parameter_id for spec in RANGE_STATE_V1_PARAMETERS}
    assert len(published) == 10
    assert {name: entry["default"] for name, entry in published.items()} == {
        "chop_period": 14, "chop_directional_threshold": "38.2", "chop_range_threshold": "61.8",
        "bandwidth_period": 20, "bandwidth_stddev_multiplier": "2.0",
        "compression_reference_bars": 120, "compressed_score_threshold": "80",
        "expanded_score_threshold": "20", "bandwidth_stddev_mode": "POPULATION_V1",
        "compression_percentile_mode": "STRICT_EMPIRICAL_V1",
    }
    base = {"instrument_id": "US30", "calendar_id": "cal-v1", "components": [
        {"component_id": "range_state", "component_version": "1"},
    ]}
    preview = client.post("/config/preview", json=base)
    assert preview.status_code == 200
    assert len(preview.json()["detection_config"]["components"][0]["parameters"]) == 10
    for name, value in (("chop_directional_threshold", "61.8"),
                        ("expanded_score_threshold", "80")):
        invalid = base | {"components": [base["components"][0] | {
            "parameters": [{"name": name, "value": value}],
        }]}
        response = client.post("/config/preview", json=invalid)
        assert response.status_code == 422
        assert "strictly below" in response.json()["detail"]
