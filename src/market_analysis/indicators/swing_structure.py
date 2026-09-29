"""Descriptive HH/HL/LH/LL structure and strict completed-close breaks, v1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.config.component_registry import SWING_STRUCTURE_V1_PARAMETERS
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import PatternDefinition

from .incremental import IncrementalMarketState, MarketStateError
from .swing_point import SWING_POINT_DEFINITION_ID, SwingPoint, SwingPointState, SwingType

SWING_STRUCTURE_DEFINITION_ID = "SWING_STRUCTURE_V1"
SWING_STRUCTURE_PRECISION = 34


class SwingLabel(StrEnum):
    HIGH_UNCLASSIFIED = "HIGH_UNCLASSIFIED"
    LOW_UNCLASSIFIED = "LOW_UNCLASSIFIED"
    HH = "HH"
    LH = "LH"
    EH = "EH"
    HL = "HL"
    LL = "LL"
    EL = "EL"


class OverallStructure(StrEnum):
    UNDEFINED = "UNDEFINED"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    MIXED = "MIXED"


class StructureBreakType(StrEnum):
    SWING_HIGH_CLOSE_BREAK = "SWING_HIGH_CLOSE_BREAK"
    SWING_LOW_CLOSE_BREAK = "SWING_LOW_CLOSE_BREAK"


@dataclass(frozen=True, slots=True)
class StructureLineage:
    run_id: str
    dataset_revision_id: str
    instrument_id: str
    timeframe: Timeframe
    calendar_id: str
    detection_config_hash: str


@dataclass(frozen=True, slots=True)
class SwingClassification:
    definition_id: str
    source_swing: SwingPoint
    previous_same_type_swing: SwingPoint | None
    label: SwingLabel
    price_delta: Decimal | None
    equal_level_atr_multiplier: Decimal
    equality_atr_anchor: str
    equality_tolerance: Decimal
    lower_equality_threshold: Decimal | None
    upper_equality_threshold: Decimal | None
    lineage: StructureLineage

    @property
    def event_time(self) -> datetime:
        return self.source_swing.event_time

    @property
    def detection_time(self) -> datetime:
        return self.source_swing.detection_time


@dataclass(frozen=True, slots=True)
class StructureBreak:
    definition_id: str
    break_type: StructureBreakType
    detection_time: datetime
    reference_swing: SwingPoint
    reference_price: Decimal
    reference_atr: Decimal
    break_buffer_atr_multiplier: Decimal
    break_atr_anchor: str
    break_confirmation_source: str
    buffer_points: Decimal
    threshold: Decimal
    breaking_close: Decimal
    pre_break_overall_structure: OverallStructure
    pre_break_high_label: SwingLabel | None
    pre_break_low_label: SwingLabel | None
    lineage: StructureLineage

    @property
    def event_time(self) -> datetime:
        return self.detection_time


class SwingStructureState(IncrementalMarketState):
    """Consume ATR → SwingPoint → structure in exact completed-bar lockstep.

    All labels become observable at source detection time. New confirmations
    replace active references before break checks. Breaks describe a close
    crossing a buffered reference, without assigning BOS/CHoCH semantics.
    Reset all three components together before replaying from their origin.
    """

    def __init__(
        self, run_config: DetectionAnalysisConfig, swing_point: SwingPointState, *,
        run_id: str, dataset_revision_id: str, instance_id: str = "swing_structure",
        pinned_config_hash: str | None = None,
        pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    ) -> None:
        selection = next((
            item for item in run_config.components
            if item.component_id == "swing_structure" and item.effective_instance_id == instance_id
        ), None)
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled SwingStructure v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != {item.parameter_id for item in SWING_STRUCTURE_V1_PARAMETERS}:
            raise MarketStateError("SwingStructure v1 requires exactly its registered parameters")
        for spec in SWING_STRUCTURE_V1_PARAMETERS:
            if spec.supported_values and parameters[spec.parameter_id] not in spec.supported_values:
                raise MarketStateError(f"unsupported SwingStructure {spec.parameter_id}")
        if parameters["allow_break_on_reference_detection_bar"] is not False:
            raise MarketStateError("SwingStructure v1 excludes reference detection-bar breaks")
        self.equal_level_atr_multiplier = _multiplier(parameters, "equal_level_atr_multiplier")
        self.break_buffer_atr_multiplier = _multiplier(parameters, "break_buffer_atr_multiplier")
        if not isinstance(swing_point, SwingPointState):
            raise MarketStateError("SwingStructure requires an injected SwingPointState")
        config_hash = detection_config_hash(run_config, pattern_definitions=pattern_definitions)
        if (swing_point.run_config != run_config
                or swing_point.atr.run_config != run_config
                or swing_point.detection_config_hash != config_hash):
            raise MarketStateError("SwingStructure dependency config/hash differs from run config")
        if pinned_config_hash is not None and pinned_config_hash != config_hash:
            raise MarketStateError(
                "pinned_config_hash does not match resolved detection config hash"
            )
        for name, value in (("run_id", run_id), ("dataset_revision_id", dataset_revision_id)):
            if not isinstance(value, str) or not value.strip():
                raise MarketStateError(f"{name} must be a nonempty string")
        self._lineage = StructureLineage(
            run_id, dataset_revision_id, run_config.instrument_id, run_config.timeframe,
            run_config.calendar_id, config_hash,
        )
        self.instance_id = instance_id
        self.swing_point = swing_point
        self.detection_config_hash = config_hash
        super().__init__(run_config, warmup_completed_bars=swing_point.warmup_completed_bars)

    @property
    def lineage(self) -> StructureLineage:
        return self._lineage

    @property
    def classifications(self) -> tuple[SwingClassification, ...]:
        return tuple(self._classifications)

    @property
    def break_events(self) -> tuple[StructureBreak, ...]:
        return tuple(self._breaks)

    @property
    def current_bar_classifications(self) -> tuple[SwingClassification, ...]:
        return self._current_classifications

    @property
    def current_bar_breaks(self) -> tuple[StructureBreak, ...]:
        return self._current_breaks

    @property
    def overall_structure(self) -> OverallStructure:
        high = self._latest_high
        low = self._latest_low
        if (high is None or low is None or high.previous_same_type_swing is None
                or low.previous_same_type_swing is None):
            return OverallStructure.UNDEFINED
        if high.label == SwingLabel.HH and low.label == SwingLabel.HL:
            return OverallStructure.BULLISH
        if high.label == SwingLabel.LH and low.label == SwingLabel.LL:
            return OverallStructure.BEARISH
        return OverallStructure.MIXED

    def _reset_state(self) -> None:
        self._classifications: list[SwingClassification] = []
        self._breaks: list[StructureBreak] = []
        self._current_classifications: tuple[SwingClassification, ...] = ()
        self._current_breaks: tuple[StructureBreak, ...] = ()
        self._latest_high: SwingClassification | None = None
        self._previous_high: SwingClassification | None = None
        self._latest_low: SwingClassification | None = None
        self._previous_low: SwingClassification | None = None
        self._high_broken = False
        self._low_broken = False
        self._dependency_generations: tuple[int, int] | None = None
        self._last_swing_index = 0
        self._last_structure_update_detection_time: datetime | None = None

    def _update_completed_bar(self, bar: Bar) -> None:
        swing = self.swing_point
        atr = swing.atr
        generations = (swing.reset_generation, atr.reset_generation)
        if self._dependency_generations is not None and generations != self._dependency_generations:
            raise MarketStateError("upstream reset requires SwingStructure reset and replay")
        if (swing._completed_bars != self._completed_bars + 1
                or atr._completed_bars != self._completed_bars + 1
                or swing.last_completed_bar != bar or atr.last_completed_bar != bar):
            raise MarketStateError("ATR and SwingPoint must update with this exact bar first")
        if (swing.run_config != self.run_config or atr.run_config != self.run_config
                or swing.detection_config_hash != self.detection_config_hash):
            raise MarketStateError("upstream config/hash changed")
        points = swing.current_bar_swing_points
        expected_index = self._last_swing_index + 1
        for point in points:
            if (point.swing_index != expected_index or point.detection_time != bar.timestamp
                    or point.confirmation_bar_index != self._completed_bars
                    or point.event_time >= point.detection_time
                    or point.candidate_bar_index < 0
                    or point.candidate_bar_index >= point.confirmation_bar_index
                    or point.definition_id != SWING_POINT_DEFINITION_ID
                    or point.detection_config_hash != self.detection_config_hash
                    or point.atr_period != swing.atr_period
                    or not point.atr_at_extreme.is_finite() or point.atr_at_extreme < 0
                    or not point.event_price.is_finite()
                    or point.swing_type not in (SwingType.SWING_HIGH, SwingType.SWING_LOW)):
                raise MarketStateError("invalid, skipped or future SwingPoint confirmation")
            expected_index += 1
        # Validate the whole batch before changing any observable structure.
        self._dependency_generations = generations
        new_classifications = []
        new_breaks = []
        with localcontext() as context:
            context.prec = SWING_STRUCTURE_PRECISION
            for point in points:
                classification = self._classify(point)
                new_classifications.append(classification)
                self._classifications.append(classification)
                self._last_swing_index = point.swing_index
            for high, reference, broken in (
                (True, self._latest_high, self._high_broken),
                (False, self._latest_low, self._low_broken),
            ):
                if reference is None or broken:
                    continue
                point = reference.source_swing
                if point.detection_time == bar.timestamp:
                    continue
                buffer = self.break_buffer_atr_multiplier * point.atr_at_extreme
                threshold = point.event_price + buffer if high else point.event_price - buffer
                if not (bar.close > threshold if high else bar.close < threshold):
                    continue
                event = StructureBreak(
                    SWING_STRUCTURE_DEFINITION_ID,
                    StructureBreakType.SWING_HIGH_CLOSE_BREAK if high
                    else StructureBreakType.SWING_LOW_CLOSE_BREAK,
                    bar.timestamp, point, point.event_price, point.atr_at_extreme,
                    self.break_buffer_atr_multiplier, "REFERENCE_SWING_ATR_AT_EXTREME", "CLOSE",
                    buffer, threshold, bar.close, self.overall_structure,
                    self._latest_high.label if self._latest_high else None,
                    self._latest_low.label if self._latest_low else None, self.lineage,
                )
                new_breaks.append(event)
                self._breaks.append(event)
                if high:
                    self._high_broken = True
                else:
                    self._low_broken = True
        self._current_classifications = tuple(new_classifications)
        self._current_breaks = tuple(new_breaks)
        if new_classifications or new_breaks:
            self._last_structure_update_detection_time = bar.timestamp

    def _classify(self, point: SwingPoint) -> SwingClassification:
        high = point.swing_type == SwingType.SWING_HIGH
        previous = self._latest_high if high else self._latest_low
        prior = previous.source_swing if previous else None
        tolerance = self.equal_level_atr_multiplier * point.atr_at_extreme
        delta = point.event_price - prior.event_price if prior else None
        if delta is None:
            label = SwingLabel.HIGH_UNCLASSIFIED if high else SwingLabel.LOW_UNCLASSIFIED
        elif delta > tolerance:
            label = SwingLabel.HH if high else SwingLabel.HL
        elif delta < -tolerance:
            label = SwingLabel.LH if high else SwingLabel.LL
        else:
            label = SwingLabel.EH if high else SwingLabel.EL
        result = SwingClassification(
            SWING_STRUCTURE_DEFINITION_ID, point, prior, label, delta,
            self.equal_level_atr_multiplier, "NEW_SWING_ATR_AT_EXTREME", tolerance,
            prior.event_price - tolerance if prior else None,
            prior.event_price + tolerance if prior else None, self.lineage,
        )
        if high:
            self._previous_high, self._latest_high = previous, result
            self._high_broken = False
        else:
            self._previous_low, self._latest_low = previous, result
            self._low_broken = False
        return result

    def _state_values(self) -> Mapping[str, object]:
        with localcontext() as context:
            context.prec = SWING_STRUCTURE_PRECISION
            high_threshold = (
                self._latest_high.source_swing.event_price + self.break_buffer_atr_multiplier
                * self._latest_high.source_swing.atr_at_extreme if self._latest_high else None
            )
            low_threshold = (
                self._latest_low.source_swing.event_price - self.break_buffer_atr_multiplier
                * self._latest_low.source_swing.atr_at_extreme if self._latest_low else None
            )
        return {
            "definition_id": SWING_STRUCTURE_DEFINITION_ID, "lineage": _values(self.lineage),
            "equal_level_atr_multiplier": self.equal_level_atr_multiplier,
            "equality_atr_anchor": "NEW_SWING_ATR_AT_EXTREME",
            "break_buffer_atr_multiplier": self.break_buffer_atr_multiplier,
            "break_atr_anchor": "REFERENCE_SWING_ATR_AT_EXTREME",
            "break_confirmation_source": "CLOSE",
            "allow_break_on_reference_detection_bar": False,
            "overall_structure": self.overall_structure,
            "latest_high": _values(self._latest_high),
            "previous_high": _values(self._previous_high),
            "latest_low": _values(self._latest_low),
            "previous_low": _values(self._previous_low),
            "high_broken": self._high_broken, "low_broken": self._low_broken,
            "active_high_break_threshold": high_threshold,
            "active_low_break_threshold": low_threshold,
            "last_structure_update_detection_time": self._last_structure_update_detection_time,
            "classifications": tuple(_values(item) for item in self._classifications),
            "break_events": tuple(_values(item) for item in self._breaks),
            "current_bar_classifications": tuple(
                _values(item) for item in self._current_classifications
            ),
            "current_bar_breaks": tuple(_values(item) for item in self._current_breaks),
        }


def _multiplier(parameters: Mapping[str, object], name: str) -> Decimal:
    value = parameters[name]
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise MarketStateError(f"SwingStructure {name} must be a decimal")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise MarketStateError(f"SwingStructure {name} must be finite and non-negative")
    return result


def _values(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        result = {field.name: _values(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, SwingClassification | StructureBreak):
            result.update(event_time=value.event_time, detection_time=value.detection_time)
        return result
    return value
