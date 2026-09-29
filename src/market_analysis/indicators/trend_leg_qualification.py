"""Live, sticky qualification of an independently established structural leg.

Source metrics retain the initial protected-swing anchor. This observer neither
reconstructs structural history nor vetoes context on EMA crosses/retracements.
Only the active leg and the current bar's qualification/termination evidence are
retained; consumers persist events when they need a history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.config.component_registry import TREND_LEG_QUALIFICATION_V1_PARAMETERS
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import PatternDefinition

from .incremental import IncrementalMarketState, MarketStateError
from .swing_structure import StructureLineage, _values
from .trend_leg import (
    TREND_LEG_DEFINITION_ID,
    TrendLeg,
    TrendLegState,
    TrendLegTransition,
    TrendLegTransitionType,
)

TREND_LEG_QUALIFICATION_DEFINITION_ID = "TREND_LEG_QUALIFICATION_V1"


class QualificationStatus(StrEnum):
    NO_ACTIVE_LEG = "NO_ACTIVE_LEG"
    UNQUALIFIED = "UNQUALIFIED"
    QUALIFIED = "QUALIFIED"


@dataclass(frozen=True, slots=True)
class QualificationThresholds:
    min_duration_bars: int
    min_directional_move_points: Decimal
    qualification_mode: str = "LIVE_STICKY_V1"


@dataclass(frozen=True, slots=True)
class QualificationEarned:
    definition_id: str
    instance_id: str
    source_instance_id: str
    event_time: datetime
    detection_time: datetime
    bar_index: int
    source_leg: TrendLeg
    thresholds: QualificationThresholds
    lineage: StructureLineage
    transition_type: QualificationStatus = QualificationStatus.QUALIFIED


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    definition_id: str
    instance_id: str
    source_instance_id: str
    status: QualificationStatus
    source_leg: TrendLeg
    thresholds: QualificationThresholds
    duration_gate_passed: bool
    directional_move_gate_passed: bool
    first_earned: QualificationEarned | None
    lineage: StructureLineage


@dataclass(frozen=True, slots=True)
class EndedQualification:
    evidence: QualificationEvidence
    source_transition: TrendLegTransition


class TrendLegQualificationState(IncrementalMarketState):
    """Update once after the bound structural source on the exact same bar."""

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        trend_leg: TrendLegState,
        *,
        run_id: str,
        dataset_revision_id: str,
        instance_id: str = "trend_leg_qualification",
        pinned_config_hash: str | None = None,
        pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    ) -> None:
        if not isinstance(run_config, DetectionAnalysisConfig):
            raise MarketStateError("run_config must be a frozen DetectionAnalysisConfig")
        selection = next(
            (
                item
                for item in run_config.components
                if item.component_id == "trend_leg_qualification"
                and item.effective_instance_id == instance_id
            ),
            None,
        )
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled TrendLeg qualification v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != {spec.parameter_id for spec in TREND_LEG_QUALIFICATION_V1_PARAMETERS}:
            raise MarketStateError(
                "TrendLeg qualification v1 requires exactly registered parameters"
            )
        duration, move = parameters["min_duration_bars"], parameters["min_directional_move_points"]
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise MarketStateError("min_duration_bars must be an integer >= 1")
        if not isinstance(move, Decimal) or not move.is_finite() or move < 0:
            raise MarketStateError("min_directional_move_points must be a finite Decimal >= 0")
        binding = parameters["trend_leg_instance_id"]
        if not isinstance(binding, str) or not binding.strip():
            raise MarketStateError("trend_leg_instance_id must be nonempty")
        if parameters["qualification_mode"] != "LIVE_STICKY_V1":
            raise MarketStateError("unsupported qualification_mode")
        if run_config.timeframe != Timeframe.M1:
            raise MarketStateError("TrendLeg qualification v1 requires canonical M1 bars")
        if not isinstance(trend_leg, TrendLegState):
            raise MarketStateError("qualification requires an injected TrendLegState")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (run_id, dataset_revision_id)
        ):
            raise MarketStateError("qualification requires nonempty run/dataset lineage")
        config_hash = detection_config_hash(run_config, pattern_definitions=pattern_definitions)
        if pinned_config_hash is not None and pinned_config_hash != config_hash:
            raise MarketStateError("pinned_config_hash differs from resolved detection config hash")
        self.instance_id = instance_id
        self.trend_leg = trend_leg
        self.detection_config_hash = config_hash
        self._parameters = parameters
        self._binding = binding
        self._thresholds = QualificationThresholds(duration, move)
        self._lineage = StructureLineage(
            run_id,
            dataset_revision_id,
            run_config.instrument_id,
            run_config.timeframe,
            run_config.calendar_id,
            config_hash,
        )
        self._dependencies = self._source_dependencies()
        self._validate_source(run_config)
        super().__init__(
            run_config,
            warmup_completed_bars=max(trend_leg.warmup_completed_bars, duration),
        )

    @property
    def lineage(self) -> StructureLineage:
        return self._lineage

    @property
    def thresholds(self) -> QualificationThresholds:
        return self._thresholds

    @property
    def status(self) -> QualificationStatus:
        return self._active.status if self._active else QualificationStatus.NO_ACTIVE_LEG

    @property
    def active_qualification(self) -> QualificationEvidence | None:
        return self._active

    @property
    def current_bar_qualifications(self) -> tuple[QualificationEarned, ...]:
        return self._current_qualifications

    @property
    def current_bar_ended(self) -> EndedQualification | None:
        return self._ended

    def _source_dependencies(self) -> tuple[IncrementalMarketState, ...]:
        source = self.trend_leg
        structure = source.structure
        return source, source.ema, structure, structure.swing_point, structure.swing_point.atr

    def _validate_source(self, config: DetectionAnalysisConfig) -> None:
        source = self.trend_leg
        if (
            source.run_config != config
            or source.instance_id != self._binding
            or source.detection_config_hash != self.detection_config_hash
            or source.lineage != self.lineage
        ):
            raise MarketStateError("qualification source config/hash/lineage/binding differs")
        selection = next(
            (item for item in config.components if item.effective_instance_id == self._binding),
            None,
        )
        if (
            selection is None
            or not selection.enabled
            or selection.component_id != "trend_leg"
            or selection.component_version != "1"
            or source._parameters != {item.name: item.value for item in selection.parameters}
        ):
            raise MarketStateError("qualification source definition/parameters differ")
        source._validate_dependencies(config)
        source._validate_initial_settings(config)

    def _reset_state(self) -> None:
        self._active: QualificationEvidence | None = None
        self._ended: EndedQualification | None = None
        self._current_qualifications: tuple[QualificationEarned, ...] = ()
        self._dependency_generations: tuple[int, ...] | None = None

    @staticmethod
    def _same_occurrence(left: TrendLeg, right: TrendLeg) -> bool:
        return (left.definition_id, left.leg_index, left.lineage) == (
            right.definition_id,
            right.leg_index,
            right.lineage,
        )

    def _validate_leg(self, leg: TrendLeg, bar: Bar) -> None:
        if (
            not isinstance(leg, TrendLeg)
            or leg.definition_id != TREND_LEG_DEFINITION_ID
            or leg.lineage != self.lineage
            or leg.current_time != bar.timestamp
            or leg.current_close != bar.close
            or isinstance(leg.duration_bars, bool)
            or not isinstance(leg.duration_bars, int)
            or leg.duration_bars < 1
            or not isinstance(leg.directional_movement_points, Decimal)
            or not leg.directional_movement_points.is_finite()
            or leg.detection_time > bar.timestamp
        ):
            raise MarketStateError("invalid qualification source leg observation")

    def _evidence(
        self,
        leg: TrendLeg,
        earned: QualificationEarned | None,
    ) -> QualificationEvidence:
        return QualificationEvidence(
            TREND_LEG_QUALIFICATION_DEFINITION_ID,
            self.instance_id,
            self._binding,
            QualificationStatus.QUALIFIED if earned else QualificationStatus.UNQUALIFIED,
            leg,
            self.thresholds,
            leg.duration_bars >= self.thresholds.min_duration_bars,
            leg.directional_movement_points >= self.thresholds.min_directional_move_points,
            earned,
            self.lineage,
        )

    def _update_completed_bar(self, bar: Bar) -> None:
        dependencies = self._source_dependencies()
        if any(
            current is not original
            for current, original in zip(dependencies, self._dependencies, strict=True)
        ):
            raise MarketStateError("qualification injected dependency was replaced")
        self._validate_source(self.run_config)
        generations = tuple(item.reset_generation for item in dependencies)
        if self._dependency_generations is not None and generations != self._dependency_generations:
            raise MarketStateError(
                "upstream reset requires qualification reset and full-chain replay"
            )
        if any(
            item._completed_bars != self._completed_bars + 1 or item.last_completed_bar != bar
            for item in dependencies
        ):
            raise MarketStateError(
                "qualification dependencies must update with this exact bar first"
            )
        source = self.trend_leg
        leg = source.active_leg
        terminated = tuple(
            item
            for item in source.current_bar_transitions
            if item.transition_type == TrendLegTransitionType.TERMINATED
        )
        if len(terminated) > 1 or (terminated and leg is not None):
            raise MarketStateError("invalid qualification source termination batch")
        for item in source.current_bar_transitions:
            self._validate_leg(item.leg, bar)
            if item.lineage != self.lineage or item.detection_time != bar.timestamp:
                raise MarketStateError("invalid qualification source transition")
        if leg is not None:
            self._validate_leg(leg, bar)
        previous = self._active
        ended = None
        events: tuple[QualificationEarned, ...] = ()
        active = None
        if terminated:
            transition = terminated[0]
            if previous is None or not self._same_occurrence(previous.source_leg, transition.leg):
                raise MarketStateError("terminated source differs from prior active qualification")
            ended = EndedQualification(
                self._evidence(transition.leg, previous.first_earned),
                transition,
            )
        elif leg is not None:
            earned = (
                previous.first_earned
                if previous is not None and self._same_occurrence(previous.source_leg, leg)
                else None
            )
            active = self._evidence(leg, earned)
            if (
                earned is None
                and active.duration_gate_passed
                and active.directional_move_gate_passed
            ):
                earned = QualificationEarned(
                    TREND_LEG_QUALIFICATION_DEFINITION_ID,
                    self.instance_id,
                    self._binding,
                    bar.timestamp,
                    bar.timestamp,
                    self._completed_bars,
                    leg,
                    self.thresholds,
                    self.lineage,
                )
                events = (earned,)
                active = self._evidence(leg, earned)
        # Every dependency/observation guard precedes mutation.
        self._dependency_generations = generations
        self._active, self._ended, self._current_qualifications = active, ended, events

    def _state_values(self) -> Mapping[str, object]:
        return {
            "component_id": "trend_leg_qualification",
            "instance_id": self.instance_id,
            "definition_id": TREND_LEG_QUALIFICATION_DEFINITION_ID,
            "lineage": _values(self.lineage),
            "parameters": self._parameters,
            "status": self.status,
            "active_qualification": _values(self.active_qualification),
            "current_bar_qualifications": tuple(
                _values(item) for item in self.current_bar_qualifications
            ),
            "current_bar_ended": _values(self.current_bar_ended),
        }
