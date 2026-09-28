"""Independent causal CHOP and relative BandWidth/compression research axes, v1."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from typing import cast

from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.config.component_registry import (
    RANGE_STATE_V1_PARAMETERS,
    validate_component_parameters,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import ParameterType, PatternDefinitionError

from .atr import AtrState
from .incremental import IncrementalMarketState, MarketStateError

RANGE_STATE_DEFINITION_ID = "CHOP_BANDWIDTH_RANGE_STATE_V1"
RANGE_STATE_PRECISION = 34


class RangeAvailability(StrEnum):
    WARMING_UP = "WARMING_UP"
    AVAILABLE = "AVAILABLE"
    DEGENERATE = "DEGENERATE"


class ChopCategory(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    DIRECTIONAL = "DIRECTIONAL"
    TRANSITIONAL = "TRANSITIONAL"
    RANGE_LIKE = "RANGE_LIKE"


class CompressionCategory(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    COMPRESSED = "COMPRESSED"
    NORMAL = "NORMAL"
    EXPANDED = "EXPANDED"


@dataclass(frozen=True, slots=True)
class RangeParameters:
    chop_period: int
    chop_directional_threshold: Decimal
    chop_range_threshold: Decimal
    bandwidth_period: int
    bandwidth_stddev_multiplier: Decimal
    compression_reference_bars: int
    compressed_score_threshold: Decimal
    expanded_score_threshold: Decimal
    bandwidth_stddev_mode: str
    compression_percentile_mode: str


@dataclass(frozen=True, slots=True)
class RangeLineage:
    run_id: str
    dataset_revision_id: str
    instrument_id: str
    timeframe: Timeframe
    calendar_id: str
    detection_config_hash: str


@dataclass(frozen=True, slots=True)
class ChopInput:
    timestamp: datetime
    true_range: Decimal
    high: Decimal
    low: Decimal


@dataclass(frozen=True, slots=True)
class CloseObservation:
    timestamp: datetime
    close: Decimal


@dataclass(frozen=True, slots=True)
class BandwidthObservation:
    timestamp: datetime
    bandwidth: Decimal


@dataclass(frozen=True, slots=True)
class ChopState:
    status: RangeAvailability
    reason: str | None
    window: tuple[ChopInput, ...]
    sum_true_range: Decimal | None = None
    window_high: Decimal | None = None
    window_low: Decimal | None = None
    window_range: Decimal | None = None
    score: Decimal | None = None
    category: ChopCategory = ChopCategory.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class BandwidthState:
    status: RangeAvailability
    reason: str | None
    window: tuple[CloseObservation, ...]
    basis: Decimal | None = None
    variance: Decimal | None = None
    stddev: Decimal | None = None
    upper_band: Decimal | None = None
    lower_band: Decimal | None = None
    bandwidth: Decimal | None = None
    bandwidth_squared: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CompressionState:
    status: RangeAvailability
    reason: str | None
    reference: tuple[BandwidthObservation, ...]
    strict_lower_count: int | None = None
    tie_count: int | None = None
    percentile: Decimal | None = None
    score: Decimal | None = None
    category: CompressionCategory = CompressionCategory.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class RangeObservation:
    definition_id: str
    component_id: str
    instance_id: str
    definition_version: str
    calculation_time: datetime | None
    detection_time: datetime | None
    parameters: RangeParameters
    lineage: RangeLineage
    chop: ChopState
    bandwidth_evidence: BandwidthState
    compression: CompressionState

    @property
    def choppiness_score(self) -> Decimal | None:
        return self.chop.score

    @property
    def choppiness_regime(self) -> ChopCategory:
        return self.chop.category

    @property
    def bandwidth(self) -> Decimal | None:
        return self.bandwidth_evidence.bandwidth

    @property
    def bandwidth_percentile(self) -> Decimal | None:
        return self.compression.percentile

    @property
    def compression_score(self) -> Decimal | None:
        return self.compression.score

    @property
    def compression_regime(self) -> CompressionCategory:
        return self.compression.category


class RangeState(IncrementalMarketState):
    """Drive ATR → RangeState once per completed bar; reset both before replay.

    Full preflight warm-up is distinct from each axis's availability. Timestamp
    gaps carry state: the caller must supply canonical, preflight-validated
    intervals. Unexpected warm-up/detection gaps block launch in the driver;
    elapsed timestamps alone cannot identify scheduled calendar closures.
    """

    def __init__(
        self, run_config: DetectionAnalysisConfig, atr: AtrState, *,
        run_id: str, dataset_revision_id: str, instance_id: str = "range_state",
        pinned_config_hash: str | None = None,
    ) -> None:
        selection = next((item for item in run_config.components
                          if item.component_id == "range_state"
                          and item.effective_instance_id == instance_id), None)
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled RangeState v1 selection is required")
        values = {item.name: item.value for item in selection.parameters}
        if set(values) != {spec.parameter_id for spec in RANGE_STATE_V1_PARAMETERS}:
            raise MarketStateError("RangeState v1 requires exactly its registered parameters")
        normalized: dict[str, object] = {}
        try:
            for spec in RANGE_STATE_V1_PARAMETERS:
                value = values[spec.parameter_id]
                if (spec.value_type == ParameterType.DECIMAL
                        and (isinstance(value, bool) or not isinstance(value, Decimal | int))):
                    raise MarketStateError(f"RangeState {spec.parameter_id} must be a decimal")
                normalized[spec.parameter_id] = spec.normalize(value)
            validate_component_parameters(("range_state", "1"), normalized)
        except PatternDefinitionError as exc:
            raise MarketStateError(str(exc)) from exc
        self._parameters = RangeParameters(
            cast(int, normalized["chop_period"]),
            cast(Decimal, normalized["chop_directional_threshold"]),
            cast(Decimal, normalized["chop_range_threshold"]),
            cast(int, normalized["bandwidth_period"]),
            cast(Decimal, normalized["bandwidth_stddev_multiplier"]),
            cast(int, normalized["compression_reference_bars"]),
            cast(Decimal, normalized["compressed_score_threshold"]),
            cast(Decimal, normalized["expanded_score_threshold"]),
            cast(str, normalized["bandwidth_stddev_mode"]),
            cast(str, normalized["compression_percentile_mode"]),
        )
        if not isinstance(atr, AtrState):
            raise MarketStateError("RangeState requires an injected AtrState")
        config_hash = detection_config_hash(run_config)
        if atr.run_config != run_config:
            raise MarketStateError("RangeState dependency config/hash differs from run config")
        if pinned_config_hash is not None and pinned_config_hash != config_hash:
            raise MarketStateError(
                "pinned_config_hash does not match resolved detection config hash"
            )
        for name, value in (("run_id", run_id), ("dataset_revision_id", dataset_revision_id)):
            if not isinstance(value, str) or not value.strip():
                raise MarketStateError(f"{name} must be a nonempty string")
        self._lineage = RangeLineage(run_id, dataset_revision_id, run_config.instrument_id,
                                    run_config.timeframe, run_config.calendar_id, config_hash)
        self.instance_id = instance_id
        self.atr = atr
        self.detection_config_hash = config_hash
        self._dependency_config_hash = config_hash
        p = self.parameters
        super().__init__(run_config, warmup_completed_bars=max(
            p.chop_period, p.bandwidth_period + p.compression_reference_bars - 1,
            atr.warmup_completed_bars,
        ))

    @property
    def parameters(self) -> RangeParameters:
        return self._parameters

    @property
    def lineage(self) -> RangeLineage:
        return self._lineage

    @property
    def range_state(self) -> RangeObservation:
        return RangeObservation(
            RANGE_STATE_DEFINITION_ID, "range_state", self.instance_id, "1",
            self._last_timestamp, self._last_timestamp, self.parameters, self.lineage,
            self._chop, self._bandwidth, self._compression,
        )

    def _reset_state(self) -> None:
        p = self.parameters
        self._chop_window: deque[ChopInput] = deque(maxlen=p.chop_period)
        self._close_window: deque[CloseObservation] = deque(maxlen=p.bandwidth_period)
        self._width_reference: deque[BandwidthObservation] = deque(
            maxlen=p.compression_reference_bars,
        )
        self._dependency_generation: int | None = None
        self._chop = ChopState(RangeAvailability.WARMING_UP, "INSUFFICIENT_CHOP_BARS", ())
        self._bandwidth = BandwidthState(
            RangeAvailability.WARMING_UP, "INSUFFICIENT_CLOSE_BARS", (),
        )
        self._compression = CompressionState(
            RangeAvailability.WARMING_UP, "INSUFFICIENT_VALID_BANDWIDTH_OBSERVATIONS", (),
        )

    def _update_completed_bar(self, bar: Bar) -> None:
        atr = self.atr
        if (self._dependency_generation is not None
                and self._dependency_generation != atr.reset_generation):
            raise MarketStateError("upstream reset requires RangeState reset and replay")
        if (atr._completed_bars != self._completed_bars + 1
                or atr.last_completed_bar != bar):
            raise MarketStateError("ATR must update with this exact bar first")
        if (atr.run_config != self.run_config
                or self.detection_config_hash != self._dependency_config_hash):
            raise MarketStateError("upstream config/hash changed")
        tr = atr.volatility_state.true_range
        if tr is None or not tr.is_finite() or tr < 0:
            raise MarketStateError("ATR must expose a finite non-negative current True Range")
        self._dependency_generation = atr.reset_generation
        self._chop_window.append(ChopInput(bar.timestamp, tr, bar.high, bar.low))
        self._close_window.append(CloseObservation(bar.timestamp, bar.close))
        with localcontext(Context(prec=RANGE_STATE_PRECISION)):
            self._chop = self._calculate_chop()
            self._bandwidth = self._calculate_bandwidth()
            self._compression = self._calculate_compression(bar.timestamp)

    def _calculate_chop(self) -> ChopState:
        window = tuple(self._chop_window)
        if len(window) < self.parameters.chop_period:
            return ChopState(RangeAvailability.WARMING_UP, "INSUFFICIENT_CHOP_BARS", window)
        total = sum((item.true_range for item in window), Decimal(0))
        high, low = max(item.high for item in window), min(item.low for item in window)
        envelope = high - low
        if envelope == 0:
            return ChopState(RangeAvailability.DEGENERATE, "ZERO_WINDOW_RANGE", window,
                             total, high, low, envelope)
        score = Decimal(100) * (total / envelope).log10() / Decimal(len(window)).log10()
        return ChopState(RangeAvailability.AVAILABLE, None, window, total, high, low,
                         envelope, score, self._chop_category(score))

    def _chop_category(self, score: Decimal) -> ChopCategory:
        p = self.parameters
        return (ChopCategory.DIRECTIONAL if score < p.chop_directional_threshold else
                ChopCategory.RANGE_LIKE if score > p.chop_range_threshold else
                ChopCategory.TRANSITIONAL)

    def _calculate_bandwidth(self) -> BandwidthState:
        window = tuple(self._close_window)
        if len(window) < self.parameters.bandwidth_period:
            return BandwidthState(RangeAvailability.WARMING_UP, "INSUFFICIENT_CLOSE_BARS", window)
        # Exact rational population moments avoid order-dependent rounded sums.
        # Computing the dimensionless width ratio before Decimal conversion
        # also preserves scale-equivalent ties and avoids band subtraction
        # cancellation. Only this bounded close window is inspected.
        count = len(window)
        closes = tuple(Fraction(item.close) for item in window)
        total = sum(closes, Fraction(0))
        squares = sum((close * close for close in closes), Fraction(0))
        scatter = count * squares - total * total
        basis = _decimal_fraction(total / count)
        variance = _decimal_fraction(scatter / (count * count))
        stddev = variance.sqrt()
        spread = self.parameters.bandwidth_stddev_multiplier * stddev
        upper, lower = basis + spread, basis - spread
        if basis == 0:
            return BandwidthState(RangeAvailability.DEGENERATE, "ZERO_BASIS", window,
                                  basis, variance, stddev, upper, lower)
        multiplier = Fraction(self.parameters.bandwidth_stddev_multiplier)
        width_squared = _decimal_fraction(4 * multiplier * multiplier * scatter / (total * total))
        width = width_squared.sqrt()
        if total < 0:
            width = -width
        return BandwidthState(RangeAvailability.AVAILABLE, None, window,
                              basis, variance, stddev, upper, lower, width, width_squared)

    def _calculate_compression(self, timestamp: datetime) -> CompressionState:
        width = self._bandwidth.bandwidth
        if width is None:
            return CompressionState(self._bandwidth.status,
                                    "CURRENT_BANDWIDTH_" + self._bandwidth.status,
                                    tuple(self._width_reference))
        self._width_reference.append(BandwidthObservation(timestamp, width))
        reference = tuple(self._width_reference)
        if len(reference) < self.parameters.compression_reference_bars:
            return CompressionState(RangeAvailability.WARMING_UP,
                                    "INSUFFICIENT_VALID_BANDWIDTH_OBSERVATIONS", reference)
        previous = reference[:-1]
        lower = sum(item.bandwidth < width for item in previous)
        ties = sum(item.bandwidth == width for item in previous)
        percentile = Decimal(lower) / Decimal(len(previous))
        score = Decimal(100) * (1 - percentile)
        return CompressionState(RangeAvailability.AVAILABLE, None, reference,
                                lower, ties, percentile, score, self._compression_category(score))

    def _compression_category(self, score: Decimal) -> CompressionCategory:
        p = self.parameters
        return (CompressionCategory.COMPRESSED if score >= p.compressed_score_threshold else
                CompressionCategory.EXPANDED if score <= p.expanded_score_threshold else
                CompressionCategory.NORMAL)

    def _state_values(self) -> Mapping[str, object]:
        current = self.range_state
        return {
            **cast(Mapping[str, object], _values(current)),
            "choppiness_score": current.choppiness_score,
            "choppiness_regime": current.choppiness_regime,
            "bandwidth": self._bandwidth.bandwidth,
            "bandwidth_evidence": _values(self._bandwidth),
            "bandwidth_percentile": current.bandwidth_percentile,
            "compression_score": current.compression_score,
            "compression_regime": current.compression_regime,
        }


def _values(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _values(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return tuple(_values(item) for item in value)
    return value


def _decimal_fraction(value: Fraction) -> Decimal:
    """Round an exact moment/ratio once inside the fixed calculation context."""
    return Decimal(value.numerator) / Decimal(value.denominator)
