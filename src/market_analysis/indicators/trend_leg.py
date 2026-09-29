"""Bounded structural TrendLeg v1 and independent raw EMA-cross evidence.

The favorable extreme includes the confirmed directional source swing at
establishment and completed bars observed from establishment onward. No hidden
historical bar scan is performed. Duration/movement retain the initial protected
swing anchor even when protection advances. Consumers persist current-bar events
if they need history; this component retains no event or completed-leg lists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Context, Decimal, localcontext
from enum import StrEnum

from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.config.component_registry import TREND_LEG_V1_PARAMETERS
from market_analysis.domain import Bar
from market_analysis.patterns import PatternDefinition

from .ema import EmaState
from .incremental import IncrementalMarketState, MarketStateError
from .swing_point import SWING_POINT_DEFINITION_ID, SwingPoint, SwingType
from .swing_structure import (
    SWING_STRUCTURE_DEFINITION_ID,
    StructureLineage,
    SwingClassification,
    SwingLabel,
    SwingStructureState,
    _values,
)

TREND_LEG_DEFINITION_ID = "TREND_LEG_V1"
_CONTEXT = Context(prec=34)


class TrendDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


class TrendLegTransitionType(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    PROTECTION_ADVANCED = "PROTECTION_ADVANCED"
    TERMINATED = "TERMINATED"


@dataclass(frozen=True, slots=True)
class TrendLeg:
    definition_id: str
    leg_index: int
    direction: TrendDirection
    initial_protected_swing: SwingPoint
    protected_swing: SwingPoint
    directional_swing: SwingPoint
    event_time: datetime
    detection_time: datetime
    current_time: datetime
    current_close: Decimal
    duration_bars: int
    directional_movement_points: Decimal
    favorable_extreme: Decimal
    favorable_extreme_observation_start: datetime
    favorable_extreme_mode: str
    close_retracement_points: Decimal
    protection_detection_time: datetime
    break_buffer_points: Decimal
    break_threshold: Decimal
    lineage: StructureLineage


@dataclass(frozen=True, slots=True)
class TrendLegTransition:
    definition_id: str
    transition_type: TrendLegTransitionType
    event_time: datetime
    detection_time: datetime
    bar_index: int
    previous_leg: TrendLeg | None
    leg: TrendLeg
    breaking_close: Decimal | None
    lineage: StructureLineage


@dataclass(frozen=True, slots=True)
class EmaCrossSegment:
    definition_id: str
    direction: TrendDirection
    start_time: datetime
    current_time: datetime
    end_time: datetime | None
    start_bar_index: int
    bars: int
    start_close: Decimal
    current_close: Decimal
    high: Decimal
    low: Decimal
    net_points: Decimal
    directional_net_points: Decimal
    span_points: Decimal
    efficiency_pct: Decimal | None
    lineage: StructureLineage
    end_close: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EmaCrossTransition:
    definition_id: str
    direction: TrendDirection
    event_time: datetime
    detection_time: datetime
    bar_index: int
    previous_close: Decimal
    previous_ema: Decimal
    current_close: Decimal
    current_ema: Decimal
    ended_segment: EmaCrossSegment | None
    started_segment: EmaCrossSegment | None
    lineage: StructureLineage


class TrendLegState(IncrementalMarketState):
    """Drive after ATR → SwingPoint → SwingStructure and the bound EMA."""

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        ema: EmaState,
        structure: SwingStructureState,
        *,
        run_id: str,
        dataset_revision_id: str,
        instance_id: str = "trend_leg",
        pinned_config_hash: str | None = None,
        pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    ) -> None:
        selection = next(
            (
                item
                for item in run_config.components
                if item.component_id == "trend_leg" and item.effective_instance_id == instance_id
            ),
            None,
        )
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled TrendLeg v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != {spec.parameter_id for spec in TREND_LEG_V1_PARAMETERS}:
            raise MarketStateError("TrendLeg v1 requires exactly its registered parameters")
        for spec in TREND_LEG_V1_PARAMETERS:
            value = parameters[spec.parameter_id]
            if (
                not isinstance(value, str)
                or not value.strip()
                or (spec.supported_values and value not in spec.supported_values)
            ):
                raise MarketStateError(f"unsupported TrendLeg {spec.parameter_id}")
        if not isinstance(ema, EmaState) or not isinstance(structure, SwingStructureState):
            raise MarketStateError("TrendLeg requires injected EMA and SwingStructure states")
        if (
            parameters["ema_instance_id"] != ema.instance_id
            or parameters["structure_instance_id"] != structure.instance_id
        ):
            raise MarketStateError("TrendLeg dependency instance bindings differ")
        config_hash = detection_config_hash(run_config, pattern_definitions=pattern_definitions)
        if pinned_config_hash is not None and pinned_config_hash != config_hash:
            raise MarketStateError("pinned_config_hash differs from resolved detection config hash")
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(dataset_revision_id, str)
            or not dataset_revision_id.strip()
        ):
            raise MarketStateError("TrendLeg requires nonempty run/dataset lineage")
        self._lineage = StructureLineage(
            run_id,
            dataset_revision_id,
            run_config.instrument_id,
            run_config.timeframe,
            run_config.calendar_id,
            config_hash,
        )
        self.ema = ema
        self.structure = structure
        self.instance_id = instance_id
        self.detection_config_hash = config_hash
        self._bindings = (ema.instance_id, structure.instance_id)
        self._parameters = parameters
        self._validate_dependencies(run_config)
        self._validate_initial_settings(run_config)
        self._dependency_settings = self._settings()
        self._dependencies = (ema, structure, structure.swing_point, structure.swing_point.atr)
        super().__init__(
            run_config,
            warmup_completed_bars=max(
                ema.warmup_completed_bars,
                structure.warmup_completed_bars,
            ),
        )

    @property
    def lineage(self) -> StructureLineage:
        return self._lineage

    @property
    def active_leg(self) -> TrendLeg | None:
        return self._active_leg

    @property
    def current_bar_transitions(self) -> tuple[TrendLegTransition, ...]:
        return self._current_transitions

    @property
    def active_ema_segment(self) -> EmaCrossSegment | None:
        return self._raw_segment

    @property
    def current_bar_ema_crosses(self) -> tuple[EmaCrossTransition, ...]:
        return self._current_crosses

    def _settings(self) -> tuple[object, ...]:
        return (
            self.ema.period,
            self.ema.alpha,
            self.structure.equal_level_atr_multiplier,
            self.structure.break_buffer_atr_multiplier,
            self.structure.swing_point.atr_period,
            self.structure.swing_point.reversal_atr_multiplier,
            self.structure.swing_point.atr.period,
        )

    def _validate_initial_settings(self, run_config: DetectionAnalysisConfig) -> None:
        expected: list[object] = []
        for component, names in (
            (self.ema, ("period",)),
            (self.structure, ("equal_level_atr_multiplier", "break_buffer_atr_multiplier")),
            (self.structure.swing_point, ("atr_period", "reversal_atr_multiplier")),
            (self.structure.swing_point.atr, ("period",)),
        ):
            selection = next(
                (
                    item
                    for item in run_config.components
                    if item.effective_instance_id == component.instance_id
                ),
                None,
            )
            if selection is None or not selection.enabled or selection.component_version != "1":
                raise MarketStateError("TrendLeg dependency selection is unavailable")
            values = {item.name: item.value for item in selection.parameters}
            if any(name not in values for name in names):
                raise MarketStateError("TrendLeg dependency parameters are unavailable")
            expected.extend(values[name] for name in names)
        period = expected[0]
        if not isinstance(period, int) or isinstance(period, bool) or period < 1:
            raise MarketStateError("TrendLeg dependency EMA period is invalid")
        with localcontext(_CONTEXT):
            expected.insert(1, Decimal(2) / Decimal(period + 1))
        if self._settings() != tuple(expected):
            raise MarketStateError("TrendLeg dependency settings differ from resolved config")

    def _validate_dependencies(self, run_config: DetectionAnalysisConfig) -> None:
        structure = self.structure
        swing = structure.swing_point
        if (
            any(item.run_config != run_config for item in (self.ema, structure, swing, swing.atr))
            or structure.detection_config_hash != self.detection_config_hash
            or swing.detection_config_hash != self.detection_config_hash
            or structure.lineage != self.lineage
            or (self.ema.instance_id, structure.instance_id) != self._bindings
        ):
            raise MarketStateError("TrendLeg dependency config/hash/lineage/bindings differ")

    def _reset_state(self) -> None:
        self._active_leg: TrendLeg | None = None
        self._pending: SwingClassification | None = None
        self._raw_segment: EmaCrossSegment | None = None
        self._previous_close: Decimal | None = None
        self._previous_ema: Decimal | None = None
        self._current_transitions: tuple[TrendLegTransition, ...] = ()
        self._current_crosses: tuple[EmaCrossTransition, ...] = ()
        self._dependency_generations: tuple[int, ...] | None = None
        self._last_swing_index = 0
        self._leg_index = 0

    def _update_completed_bar(self, bar: Bar) -> None:
        structure = self.structure
        swing = structure.swing_point
        dependencies = (self.ema, structure, swing, swing.atr)
        if any(
            current is not original
            for current, original in zip(dependencies, self._dependencies, strict=True)
        ):
            raise MarketStateError("TrendLeg injected dependency was replaced")
        generations = tuple(item.reset_generation for item in dependencies)
        self._validate_dependencies(self.run_config)
        if self._settings() != self._dependency_settings:
            raise MarketStateError("TrendLeg dependency settings changed")
        if self._dependency_generations is not None and generations != self._dependency_generations:
            raise MarketStateError("upstream reset requires TrendLeg reset and full-chain replay")
        if any(
            item._completed_bars != self._completed_bars + 1 or item.last_completed_bar != bar
            for item in dependencies
        ):
            raise MarketStateError("TrendLeg dependencies must update with this exact bar first")
        classifications = structure.current_bar_classifications
        with localcontext(_CONTEXT):
            self._validate_classifications(classifications, bar)
            ema = self.ema.state.values["ema"]
            if ema is not None and (not isinstance(ema, Decimal) or not ema.is_finite()):
                raise MarketStateError("invalid EMA observation")
            # All guards precede mutation, including validation of the entire event batch.
            self._dependency_generations = generations
            self._current_transitions = ()
            self._current_crosses = ()
            self._update_raw(bar, ema)
            leg = self._active_leg
            terminated = False
            if leg is not None:
                updated = self._measure(leg, bar)
                point = leg.protected_swing
                broken = point.detection_time < bar.timestamp and (
                    bar.close < leg.break_threshold
                    if leg.direction == TrendDirection.UP
                    else bar.close > leg.break_threshold
                )
                if broken:
                    self._emit(TrendLegTransitionType.TERMINATED, leg, updated, bar, bar.close)
                    self._active_leg = None
                    self._pending = None
                    terminated = True
                else:
                    self._active_leg = updated
            for classification in classifications:
                self._last_swing_index = classification.source_swing.swing_index
                if not terminated:
                    self._progress(classification, bar)

    def _validate_classifications(
        self,
        classifications: tuple[SwingClassification, ...],
        bar: Bar,
    ) -> None:
        expected = self._last_swing_index + 1
        for item in classifications:
            if not isinstance(item, SwingClassification):
                raise MarketStateError("invalid SwingStructure classification")
            point = item.source_swing
            if (
                not isinstance(point, SwingPoint)
                or not point.event_price.is_finite()
                or not point.atr_at_extreme.is_finite()
                or point.atr_at_extreme < 0
            ):
                raise MarketStateError("invalid SwingStructure source swing")
            high = point.swing_type == SwingType.SWING_HIGH
            tolerance = self.structure.equal_level_atr_multiplier * point.atr_at_extreme
            prior = item.previous_same_type_swing
            if prior is not None and not prior.event_price.is_finite():
                raise MarketStateError("invalid previous SwingStructure price")
            delta = point.event_price - prior.event_price if prior else None
            expected_label = SwingLabel.HIGH_UNCLASSIFIED if high else SwingLabel.LOW_UNCLASSIFIED
            if delta is not None:
                if delta > tolerance:
                    expected_label = SwingLabel.HH if high else SwingLabel.HL
                elif delta < -tolerance:
                    expected_label = SwingLabel.LH if high else SwingLabel.LL
                else:
                    expected_label = SwingLabel.EH if high else SwingLabel.EL
            if (
                point.swing_index != expected
                or point.detection_time != bar.timestamp
                or point.confirmation_bar_index != self._completed_bars
                or not 0 <= point.candidate_bar_index < point.confirmation_bar_index
                or point.event_time >= point.detection_time
                or point.definition_id != SWING_POINT_DEFINITION_ID
                or point.detection_config_hash != self.detection_config_hash
                or not point.event_price.is_finite()
                or not point.atr_at_extreme.is_finite()
                or point.atr_at_extreme < 0
                or point.swing_type not in (SwingType.SWING_HIGH, SwingType.SWING_LOW)
                or item.definition_id != SWING_STRUCTURE_DEFINITION_ID
                or item.lineage != self.lineage
                or item.label != expected_label
                or item.price_delta != delta
                or item.equality_tolerance != tolerance
                or item.equal_level_atr_multiplier != self.structure.equal_level_atr_multiplier
                or item.equality_atr_anchor != "NEW_SWING_ATR_AT_EXTREME"
                or (
                    prior is not None
                    and (
                        prior.swing_type != point.swing_type
                        or prior.swing_index >= point.swing_index
                        or prior.detection_time >= point.detection_time
                    )
                )
            ):
                raise MarketStateError("invalid, skipped or future SwingStructure confirmation")
            expected += 1
        if (
            tuple(item.source_swing for item in classifications)
            != self.structure.swing_point.current_bar_swing_points
        ):
            raise MarketStateError("SwingStructure confirmations differ from SwingPoint batch")

    def _progress(self, item: SwingClassification, bar: Bar) -> None:
        leg = self._active_leg
        direction = leg.direction if leg else None
        if item.label in (SwingLabel.HL, SwingLabel.LH):
            proposed = TrendDirection.UP if item.label == SwingLabel.HL else TrendDirection.DOWN
            if direction is None or direction == proposed:
                advances = leg is None or (
                    item.source_swing.event_price > leg.protected_swing.event_price
                    if proposed == TrendDirection.UP
                    else item.source_swing.event_price < leg.protected_swing.event_price
                )
                if advances:
                    self._pending = item
            else:
                self._pending = None
            return
        pending = self._pending
        if pending is None:
            return
        proposed = TrendDirection.UP if pending.label == SwingLabel.HL else TrendDirection.DOWN
        wanted = SwingLabel.HH if proposed == TrendDirection.UP else SwingLabel.LL
        self._pending = None
        if (
            item.label != wanted
            or item.detection_time <= pending.detection_time
            or (
                item.source_swing.event_price <= pending.source_swing.event_price
                if proposed == TrendDirection.UP
                else item.source_swing.event_price >= pending.source_swing.event_price
            )
        ):
            return
        point = pending.source_swing
        buffer = self.structure.break_buffer_atr_multiplier * point.atr_at_extreme
        threshold = (
            point.event_price - buffer
            if proposed == TrendDirection.UP
            else point.event_price + buffer
        )
        if point.detection_time < bar.timestamp and (
            bar.close < threshold if proposed == TrendDirection.UP else bar.close > threshold
        ):
            return
        if leg is None:
            self._leg_index += 1
            extreme = (
                max(item.source_swing.event_price, bar.high)
                if proposed == TrendDirection.UP
                else min(item.source_swing.event_price, bar.low)
            )
            new = TrendLeg(
                TREND_LEG_DEFINITION_ID,
                self._leg_index,
                proposed,
                point,
                point,
                item.source_swing,
                point.event_time,
                bar.timestamp,
                bar.timestamp,
                bar.close,
                0,
                Decimal(0),
                extreme,
                bar.timestamp,
                "DIRECTIONAL_SWING_AND_OBSERVED_BARS_V1",
                Decimal(0),
                bar.timestamp,
                buffer,
                threshold,
                self.lineage,
            )
            new = self._measure(new, bar)
            self._active_leg = new
            self._emit(TrendLegTransitionType.ESTABLISHED, None, new, bar)
        else:
            # Strict protection monotonicity is independent of tolerant structure labels.
            new = replace(
                leg,
                protected_swing=point,
                directional_swing=item.source_swing,
                protection_detection_time=bar.timestamp,
                break_buffer_points=buffer,
                break_threshold=threshold,
            )
            self._active_leg = new
            self._emit(TrendLegTransitionType.PROTECTION_ADVANCED, leg, new, bar)

    def _measure(self, leg: TrendLeg, bar: Bar) -> TrendLeg:
        up = leg.direction == TrendDirection.UP
        extreme = (
            max(leg.favorable_extreme, bar.high) if up else min(leg.favorable_extreme, bar.low)
        )
        move = bar.close - leg.initial_protected_swing.event_price
        return replace(
            leg,
            current_time=bar.timestamp,
            current_close=bar.close,
            duration_bars=self._completed_bars
            - leg.initial_protected_swing.candidate_bar_index
            + 1,
            directional_movement_points=move if up else -move,
            favorable_extreme=extreme,
            close_retracement_points=extreme - bar.close if up else bar.close - extreme,
        )

    def _emit(
        self,
        kind: TrendLegTransitionType,
        previous: TrendLeg | None,
        leg: TrendLeg,
        bar: Bar,
        breaking_close: Decimal | None = None,
    ) -> None:
        self._current_transitions += (
            TrendLegTransition(
                TREND_LEG_DEFINITION_ID,
                kind,
                leg.event_time if kind == TrendLegTransitionType.ESTABLISHED else bar.timestamp,
                bar.timestamp,
                self._completed_bars,
                previous,
                leg,
                breaking_close,
                self.lineage,
            ),
        )

    def _update_raw(self, bar: Bar, ema: Decimal | None) -> None:
        direction = None
        if ema is not None and self._previous_ema is not None and self._previous_close is not None:
            if self._previous_close <= self._previous_ema and bar.close > ema:
                direction = TrendDirection.UP
            elif self._previous_close >= self._previous_ema and bar.close < ema:
                direction = TrendDirection.DOWN
        segment = self._raw_segment
        if segment is not None:
            segment = self._raw_measure(segment, bar)
            self._raw_segment = segment
        if direction is not None:
            ended = None
            started = None
            if segment is None or segment.direction != direction:
                ended = (
                    replace(segment, end_time=bar.timestamp, end_close=bar.close)
                    if segment
                    else None
                )
                started = EmaCrossSegment(
                    "EMA_CROSS_SEGMENT_V1",
                    direction,
                    bar.timestamp,
                    bar.timestamp,
                    None,
                    self._completed_bars,
                    1,
                    bar.close,
                    bar.close,
                    bar.high,
                    bar.low,
                    Decimal(0),
                    Decimal(0),
                    bar.high - bar.low,
                    Decimal(0) if bar.high != bar.low else None,
                    self.lineage,
                )
                self._raw_segment = started
            assert (
                self._previous_close is not None
                and self._previous_ema is not None
                and ema is not None
            )
            self._current_crosses = (
                EmaCrossTransition(
                    "EMA_CROSS_SEGMENT_V1",
                    direction,
                    bar.timestamp,
                    bar.timestamp,
                    self._completed_bars,
                    self._previous_close,
                    self._previous_ema,
                    bar.close,
                    ema,
                    ended,
                    started,
                    self.lineage,
                ),
            )
        self._previous_close = bar.close
        self._previous_ema = ema

    def _raw_measure(self, segment: EmaCrossSegment, bar: Bar) -> EmaCrossSegment:
        high, low = max(segment.high, bar.high), min(segment.low, bar.low)
        net, span = bar.close - segment.start_close, high - low
        return replace(
            segment,
            current_time=bar.timestamp,
            current_close=bar.close,
            bars=self._completed_bars - segment.start_bar_index + 1,
            high=high,
            low=low,
            net_points=net,
            directional_net_points=net if segment.direction == TrendDirection.UP else -net,
            span_points=span,
            efficiency_pct=Decimal(100) * abs(net) / span if span else None,
        )

    def _state_values(self) -> Mapping[str, object]:
        return {
            "component_id": "trend_leg",
            "instance_id": self.instance_id,
            "definition_id": TREND_LEG_DEFINITION_ID,
            "lineage": _values(self.lineage),
            "parameters": self._parameters,
            "active_leg": _values(self.active_leg),
            "pending_corrective": _values(self._pending),
            "active_ema_segment": _values(self.active_ema_segment),
            "previous_close": self._previous_close,
            "previous_ema": self._previous_ema,
            "current_bar_transitions": tuple(
                _values(item) for item in self.current_bar_transitions
            ),
            "current_bar_ema_crosses": tuple(
                _values(item) for item in self.current_bar_ema_crosses
            ),
        }
