"""Observable per-bar MarketState aggregate, canonical event feed, debug JSON.

The aggregator owns the component instances selected by the resolved
:class:`DetectionAnalysisConfig` (an unresolved input is expanded once before
construction; resolution is idempotent) and advances every one of them exactly
once per completed canonical bar. It computes nothing itself: frames reference the
components' own detached read-only ``state.values`` and their own frozen
evidence records, so detector and structural-outcome consumers never
re-derive primitive logic.

Canonical per-bar update order (the within-bar event ordinal follows it):

1. ``SessionComponent`` — derives local/session context from the pinned
   versioned calendar; depends on no other component.
2. ``EmaState`` instances — independent completed-close indicators.
3. ``AtrState`` instances — independent volatility; also the input for
   SwingPoint and RangeState thresholds.
4. ``SwingPointState`` — consumes ATR (requires it exactly one bar ahead).
5. ``SwingStructureState`` — consumes SwingPoint plus ATR in exact lockstep.
6. ``TrendLegState`` — consumes the bound EMA plus SwingStructure (and its
   SwingPoint/ATR upstream); raw EMA-cross evidence first, then termination,
   then establishment/protection progress.
7. ``TrendLegQualificationState`` — consumes the active TrendLeg and the
   same bar's structural transitions once the leg chain has settled.
8. ``RangeState`` — consumes ATR True Range only; it is independent of the
   structural chain and runs last so structural evidence is already current.

Families absent from the resolved config are simply absent from frames; the
explicit availability map makes partial warm-up and partial availability
visible instead of overloading null or zero values. Availability words are
taken from each component's own fields: ``value_ready`` for EMA/ATR,
``search_active`` for SwingPoint, ``overall_structure`` for SwingStructure,
``active_leg`` presence for TrendLeg (``ACTIVE``/``NO_ACTIVE_LEG``),
``QualificationStatus`` for TrendLeg qualification, and the strictest of the
three RangeState axis statuses for RangeState.

Current-bar events are collected from the components' existing per-bar
accessors (``current_bar_swing_points``, ``current_bar_classifications``,
``current_bar_breaks``, ``current_bar_ema_crosses``,
``current_bar_transitions``, ``current_bar_qualifications``,
``current_bar_ended``); payloads are the very evidence objects those
components emitted, never a reconstruction. Within each component the
accessor order is its own emission order; across components the canonical
order above applies. Past events are never rebuilt retroactively, and every
event carries the source record's own ``event_time``/``detection_time`` so
the detection-time rule (bar ``i`` observes only what is known by the close
of bar ``i``) holds by construction.

A failed update leaves the components fail-closed (they validate their whole
batch before mutating); continue only after a full reset and replay.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from market_analysis.config import (
    ComponentSelection,
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.domain import Bar, TradingCalendar
from market_analysis.patterns import PatternDefinition

from .atr import AtrState
from .ema import EmaState
from .incremental import IncrementalMarketState, MarketStateError
from .range_state import RangeState
from .session import SessionComponent
from .swing_point import SwingPoint, SwingPointState
from .swing_structure import (
    StructureBreak,
    StructureBreakType,
    SwingClassification,
    SwingStructureState,
)
from .trend_leg import (
    EmaCrossTransition,
    TrendLegState,
    TrendLegTransition,
    TrendLegTransitionType,
)
from .trend_leg_qualification import (
    EndedQualification,
    QualificationEarned,
    TrendLegQualificationState,
)

SESSION_COMPONENT_KEY = "session"

WARMING_UP = "WARMING_UP"
AVAILABLE = "AVAILABLE"
DEGENERATE = "DEGENERATE"
ACTIVE = "ACTIVE"
NO_ACTIVE_LEG = "NO_ACTIVE_LEG"

_RANGE_AXES = ("chop", "bandwidth_evidence", "compression")

MarketEventEvidence = (
    SwingPoint
    | SwingClassification
    | StructureBreak
    | EmaCrossTransition
    | TrendLegTransition
    | EndedQualification
    | QualificationEarned
)


class MarketEventType(StrEnum):
    """Canonical market-state event vocabulary for downstream consumers.

    TrendLeg transitions map from the structural component's own
    ``TrendLegTransitionType``: ``ESTABLISHED`` to ``TREND_LEG_STARTED``,
    ``TERMINATED`` to ``TREND_LEG_ENDED`` and ``PROTECTION_ADVANCED`` to
    ``TREND_LEG_PROTECTION_ADVANCED`` (a v1 leg never changes direction; an
    opposite leg starts a new ``TREND_LEG_STARTED``). Break events reuse the
    SwingStructure component's own ``StructureBreakType`` values.
    """

    SWING_POINT_CONFIRMED = "SWING_POINT_CONFIRMED"
    SWING_STRUCTURE_CLASSIFIED = "SWING_STRUCTURE_CLASSIFIED"
    SWING_HIGH_CLOSE_BREAK = "SWING_HIGH_CLOSE_BREAK"
    SWING_LOW_CLOSE_BREAK = "SWING_LOW_CLOSE_BREAK"
    EMA_CROSS = "EMA_CROSS"
    TREND_LEG_STARTED = "TREND_LEG_STARTED"
    TREND_LEG_PROTECTION_ADVANCED = "TREND_LEG_PROTECTION_ADVANCED"
    TREND_LEG_ENDED = "TREND_LEG_ENDED"
    TREND_LEG_QUALIFIED = "TREND_LEG_QUALIFIED"
    TREND_LEG_QUALIFICATION_ENDED = "TREND_LEG_QUALIFICATION_ENDED"


_LEG_TRANSITIONS: Mapping[TrendLegTransitionType, MarketEventType] = MappingProxyType({
    TrendLegTransitionType.ESTABLISHED: MarketEventType.TREND_LEG_STARTED,
    TrendLegTransitionType.PROTECTION_ADVANCED: MarketEventType.TREND_LEG_PROTECTION_ADVANCED,
    TrendLegTransitionType.TERMINATED: MarketEventType.TREND_LEG_ENDED,
})

_BREAKS: Mapping[StructureBreakType, MarketEventType] = MappingProxyType({
    StructureBreakType.SWING_HIGH_CLOSE_BREAK: MarketEventType.SWING_HIGH_CLOSE_BREAK,
    StructureBreakType.SWING_LOW_CLOSE_BREAK: MarketEventType.SWING_LOW_CLOSE_BREAK,
})


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One canonical market-state transition observable on exactly one bar.

    ``ordinal`` is the deterministic within-bar position in canonical
    processing order. ``evidence`` is the source component's own frozen
    record, so payload identity is preserved without re-derivation.
    """

    event_type: MarketEventType
    event_time: datetime
    detection_time: datetime
    ordinal: int
    source_instance_id: str
    evidence: MarketEventEvidence


@dataclass(frozen=True, slots=True)
class MarketStateFrame:
    """Immutable observable market state for exactly one completed bar.

    ``components`` maps each enabled instance's effective instance id (and
    ``session`` for the optional session component) to that component's own
    detached read-only state values. ``availability`` uses the same keys with
    the component's own status words. Frames expose no mutation path.
    """

    bar: Bar
    completed_bars: int
    run_id: str
    dataset_revision_id: str
    detection_config_hash: str
    pinned_config_hash: str | None
    component_versions: Mapping[str, str]
    availability: Mapping[str, str]
    components: Mapping[str, Mapping[str, object]]
    market_events_this_bar: tuple[MarketEvent, ...]

    def debug_json(self) -> str:
        """Serialize with stable sorted keys for byte-identical comparisons."""
        payload = {
            "bar": dict(self.bar.to_canonical_dict()),
            "completed_bars": self.completed_bars,
            "run_id": self.run_id,
            "dataset_revision_id": self.dataset_revision_id,
            "detection_config_hash": self.detection_config_hash,
            "pinned_config_hash": self.pinned_config_hash,
            "component_versions": dict(self.component_versions),
            "availability": dict(self.availability),
            "components": {
                key: _json_value(values) for key, values in self.components.items()
            },
            "market_events_this_bar": [
                {
                    "ordinal": event.ordinal,
                    "event_type": event.event_type.value,
                    "event_time": _json_value(event.event_time),
                    "detection_time": _json_value(event.detection_time),
                    "source_instance_id": event.source_instance_id,
                    "evidence": _json_value(event.evidence),
                }
                for event in self.market_events_this_bar
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class MarketStateAggregator:
    """Advance the selected components in canonical order once per bar.

    The aggregator follows :class:`SwingPointState`'s verified-pin pattern:
    an optional ``pinned_config_hash`` must equal the recomputed resolved
    detection config hash and is rejected otherwise. The pin, the run id and
    the dataset revision id are propagated to every component that records
    lineage. Dependencies bind through each component's own parameters
    (``ema_instance_id``, ``structure_instance_id``, ``trend_leg_instance_id``,
    ``atr_period``); families without a binding parameter (SwingStructure,
    RangeState) require exactly one instance of their dependency family so
    the binding stays unambiguous. A run config that selects pattern
    definitions requires the registered ``pattern_definitions`` mapping so
    resolution succeeds. Note the canonical hash covers the SELECTED pattern
    versions only: a detector runtime executing additional registered
    versions under the same config shares this hash, so the executed binding
    set must be pinned separately (DetectorRuntime.binding_fingerprint;
    SCRUM-81 persistence owns that pinning).
    """

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        *,
        run_id: str,
        dataset_revision_id: str,
        calendar: TradingCalendar | None = None,
        pinned_calendar_version: str | None = None,
        pinned_config_hash: str | None = None,
        pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    ) -> None:
        if not isinstance(run_config, DetectionAnalysisConfig):
            raise MarketStateError("run_config must be a frozen DetectionAnalysisConfig")
        run_config = resolve_detection_config(
            run_config, pattern_definitions=pattern_definitions
        )
        for name, value in (("run_id", run_id), ("dataset_revision_id", dataset_revision_id)):
            if not isinstance(value, str) or not value.strip():
                raise MarketStateError(f"{name} must be a nonempty string")
        session: SessionComponent | None = None
        if calendar is None:
            if pinned_calendar_version is not None:
                raise MarketStateError("pinned_calendar_version requires a calendar")
        else:
            if (
                not isinstance(pinned_calendar_version, str)
                or not pinned_calendar_version.strip()
            ):
                raise MarketStateError("a calendar requires its pinned version string")
            session = SessionComponent(
                run_config, calendar, pinned_calendar_version=pinned_calendar_version
            )
        config_hash = detection_config_hash(run_config, pattern_definitions=pattern_definitions)
        if pinned_config_hash is not None and pinned_config_hash != config_hash:
            raise MarketStateError(
                f"pinned_config_hash {pinned_config_hash!r} does not match the resolved "
                f"detection config hash {config_hash!r}"
            )
        self._run_config = run_config
        self._run_id = run_id
        self._dataset_revision_id = dataset_revision_id
        self._pinned_config_hash = pinned_config_hash
        self._detection_config_hash = config_hash
        self._session = session
        selections: dict[str, ComponentSelection] = {
            item.effective_instance_id: item
            for item in run_config.components
            if item.enabled
        }
        if SESSION_COMPONENT_KEY in selections:
            raise MarketStateError(
                f"instance id {SESSION_COMPONENT_KEY!r} is reserved for the session component"
            )
        self._ema_states: dict[str, EmaState] = {
            key: EmaState(run_config, instance_id=key) for key in _family(selections, "ema")
        }
        self._atr_states: dict[str, AtrState] = {
            key: AtrState(run_config, instance_id=key) for key in _family(selections, "atr")
        }
        self._swing_points: dict[str, SwingPointState] = {
            key: SwingPointState(
                run_config,
                self._atr_for_swing_point(
                    _positive_integer(_parameters(selections[key]), "atr_period", key)
                ),
                instance_id=key,
                pinned_config_hash=pinned_config_hash,
                pattern_definitions=pattern_definitions,
            )
            for key in _family(selections, "swing_point")
        }
        self._structures: dict[str, SwingStructureState] = {
            key: SwingStructureState(
                run_config,
                self._single("swing_point", self._swing_points),
                run_id=run_id,
                dataset_revision_id=dataset_revision_id,
                instance_id=key,
                pinned_config_hash=pinned_config_hash,
                pattern_definitions=pattern_definitions,
            )
            for key in _family(selections, "swing_structure")
        }
        self._trend_legs: dict[str, TrendLegState] = {
            key: TrendLegState(
                run_config,
                self._bound(_parameters(selections[key]), "ema_instance_id", self._ema_states),
                self._bound(
                    _parameters(selections[key]), "structure_instance_id", self._structures
                ),
                run_id=run_id,
                dataset_revision_id=dataset_revision_id,
                instance_id=key,
                pinned_config_hash=pinned_config_hash,
                pattern_definitions=pattern_definitions,
            )
            for key in _family(selections, "trend_leg")
        }
        self._qualifications: dict[str, TrendLegQualificationState] = {
            key: TrendLegQualificationState(
                run_config,
                self._bound(
                    _parameters(selections[key]), "trend_leg_instance_id", self._trend_legs
                ),
                run_id=run_id,
                dataset_revision_id=dataset_revision_id,
                instance_id=key,
                pinned_config_hash=pinned_config_hash,
                pattern_definitions=pattern_definitions,
            )
            for key in _family(selections, "trend_leg_qualification")
        }
        self._ranges: dict[str, RangeState] = {
            key: RangeState(
                run_config,
                self._single("atr", self._atr_states),
                run_id=run_id,
                dataset_revision_id=dataset_revision_id,
                instance_id=key,
                pinned_config_hash=pinned_config_hash,
                pattern_definitions=pattern_definitions,
            )
            for key in _family(selections, "range_state")
        }
        self._order: tuple[IncrementalMarketState, ...] = (
            *((session,) if session else ()),
            *self._ordered(self._ema_states),
            *self._ordered(self._atr_states),
            *self._ordered(self._swing_points),
            *self._ordered(self._structures),
            *self._ordered(self._trend_legs),
            *self._ordered(self._qualifications),
            *self._ordered(self._ranges),
        )
        versions = {
            key: selection.component_version for key, selection in selections.items()
        }
        if calendar is not None and session is not None:
            versions[SESSION_COMPONENT_KEY] = calendar.version
        self._component_versions: dict[str, str] = versions
        self._completed_bars = 0
        self._last_bar: Bar | None = None

    @property
    def run_config(self) -> DetectionAnalysisConfig:
        return self._run_config

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def dataset_revision_id(self) -> str:
        return self._dataset_revision_id

    @property
    def detection_config_hash(self) -> str:
        return self._detection_config_hash

    @property
    def pinned_config_hash(self) -> str | None:
        return self._pinned_config_hash

    @property
    def session(self) -> SessionComponent | None:
        return self._session

    @property
    def components(self) -> tuple[IncrementalMarketState, ...]:
        """The owned components in canonical update order."""
        return self._order

    @property
    def completed_bars(self) -> int:
        return self._completed_bars

    @property
    def last_completed_bar(self) -> Bar | None:
        return self._last_bar

    def reset(self) -> None:
        """Reset every owned component and return to the initial state."""
        for component in self._order:
            component.reset()
        self._completed_bars = 0
        self._last_bar = None

    def update(self, bar: Bar) -> MarketStateFrame:
        """Advance every selected component once, then return this bar's frame."""
        if not isinstance(bar, Bar):
            raise MarketStateError("update requires a canonical Bar")
        if not bar.is_complete:
            raise MarketStateError("bar must be completed")
        if (
            bar.instrument_id != self._run_config.instrument_id
            or bar.timeframe != self._run_config.timeframe
        ):
            raise MarketStateError("bar instrument/timeframe does not match run config")
        if self._last_bar is not None and bar.timestamp <= self._last_bar.timestamp:
            raise MarketStateError("bar timestamps must be strictly increasing")
        for component in self._order:
            component.update(bar)
        self._completed_bars += 1
        self._last_bar = bar
        return self._frame(bar)

    def _frame(self, bar: Bar) -> MarketStateFrame:
        values: dict[str, Mapping[str, object]] = {}
        availability: dict[str, str] = {}
        if self._session is not None:
            state = self._session.state
            values[SESSION_COMPONENT_KEY] = state.values
            availability[SESSION_COMPONENT_KEY] = (
                AVAILABLE
                if state.is_warm and self._session.session_state is not None
                else WARMING_UP
            )
        for key, ema in self._ema_states.items():
            values[key] = ema.state.values
            availability[key] = _ready(values[key])
        for key, atr in self._atr_states.items():
            values[key] = atr.state.values
            availability[key] = _ready(values[key])
        for key, swing in self._swing_points.items():
            values[key] = swing.state.values
            availability[key] = AVAILABLE if values[key]["search_active"] is True else WARMING_UP
        for key, structure in self._structures.items():
            values[key] = structure.state.values
            availability[key] = _status_text(values[key]["overall_structure"])
        for key, leg in self._trend_legs.items():
            values[key] = leg.state.values
            availability[key] = ACTIVE if values[key]["active_leg"] is not None else NO_ACTIVE_LEG
        for key, qualification in self._qualifications.items():
            values[key] = qualification.state.values
            availability[key] = _status_text(values[key]["status"])
        for key, range_component in self._ranges.items():
            values[key] = range_component.state.values
            availability[key] = _range_availability(values[key])
        return MarketStateFrame(
            bar=bar,
            completed_bars=self._completed_bars,
            run_id=self._run_id,
            dataset_revision_id=self._dataset_revision_id,
            detection_config_hash=self._detection_config_hash,
            pinned_config_hash=self._pinned_config_hash,
            component_versions=MappingProxyType(dict(self._component_versions)),
            availability=MappingProxyType(availability),
            components=MappingProxyType(values),
            market_events_this_bar=self._events(),
        )

    def _events(self) -> tuple[MarketEvent, ...]:
        events: list[MarketEvent] = []

        def record(
            event_type: MarketEventType,
            event_time: datetime,
            detection_time: datetime,
            source_instance_id: str,
            evidence: MarketEventEvidence,
        ) -> None:
            events.append(
                MarketEvent(
                    event_type,
                    event_time,
                    detection_time,
                    len(events),
                    source_instance_id,
                    evidence,
                )
            )

        for key, swing in self._swing_points.items():
            for point in swing.current_bar_swing_points:
                record(
                    MarketEventType.SWING_POINT_CONFIRMED,
                    point.event_time,
                    point.detection_time,
                    key,
                    point,
                )
        for key, structure in self._structures.items():
            for classification in structure.current_bar_classifications:
                record(
                    MarketEventType.SWING_STRUCTURE_CLASSIFIED,
                    classification.event_time,
                    classification.detection_time,
                    key,
                    classification,
                )
            for break_event in structure.current_bar_breaks:
                record(
                    _BREAKS[break_event.break_type],
                    break_event.event_time,
                    break_event.detection_time,
                    key,
                    break_event,
                )
        for key, leg in self._trend_legs.items():
            for cross in leg.current_bar_ema_crosses:
                record(
                    MarketEventType.EMA_CROSS,
                    cross.event_time,
                    cross.detection_time,
                    key,
                    cross,
                )
            for transition in leg.current_bar_transitions:
                record(
                    _LEG_TRANSITIONS[transition.transition_type],
                    transition.event_time,
                    transition.detection_time,
                    key,
                    transition,
                )
        for key, qualification in self._qualifications.items():
            ended = qualification.current_bar_ended
            if ended is not None:
                record(
                    MarketEventType.TREND_LEG_QUALIFICATION_ENDED,
                    ended.source_transition.event_time,
                    ended.source_transition.detection_time,
                    key,
                    ended,
                )
            for earned in qualification.current_bar_qualifications:
                record(
                    MarketEventType.TREND_LEG_QUALIFIED,
                    earned.event_time,
                    earned.detection_time,
                    key,
                    earned,
                )
        return tuple(events)

    def _ordered(
        self,
        components: Mapping[str, IncrementalMarketState],
    ) -> tuple[IncrementalMarketState, ...]:
        return tuple(components[key] for key in sorted(components))

    def _atr_for_swing_point(self, atr_period: int) -> AtrState:
        candidates = [
            component
            for component in self._atr_states.values()
            if component.period == atr_period
        ]
        if len(candidates) != 1:
            raise MarketStateError(
                f"SwingPoint atr_period {atr_period} must match exactly one ATR instance"
            )
        return candidates[0]

    def _single[ComponentT: IncrementalMarketState](
        self,
        family: str,
        components: Mapping[str, ComponentT],
    ) -> ComponentT:
        if len(components) != 1:
            raise MarketStateError(
                f"{family} dependencies require exactly one {family} instance"
            )
        return next(iter(components.values()))

    def _bound[ComponentT: IncrementalMarketState](
        self,
        parameters: Mapping[str, object],
        name: str,
        components: Mapping[str, ComponentT],
    ) -> ComponentT:
        binding = parameters[name]
        if not isinstance(binding, str) or binding not in components:
            raise MarketStateError(f"binding {name}={binding!r} has no enabled instance")
        return components[binding]


def _family(
    selections: Mapping[str, ComponentSelection],
    component_id: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key, selection in selections.items()
            if selection.component_id == component_id
        )
    )


def _parameters(selection: ComponentSelection) -> Mapping[str, object]:
    return {item.name: item.value for item in selection.parameters}


def _positive_integer(parameters: Mapping[str, object], name: str, key: str) -> int:
    value = parameters[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MarketStateError(f"{key} {name} must be a positive integer")
    return value


def _ready(values: Mapping[str, object]) -> str:
    return AVAILABLE if values["value_ready"] is True else WARMING_UP


def _status_text(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MarketStateError("range axis state must be a mapping")
    return value


def _range_availability(values: Mapping[str, object]) -> str:
    statuses = [_status_text(_mapping(values[axis])["status"]) for axis in _RANGE_AXES]
    if any(status == WARMING_UP for status in statuses):
        return WARMING_UP
    if any(status == DEGENERATE for status in statuses):
        return DEGENERATE
    return AVAILABLE


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        result: dict[str, object] = {
            field.name: _json_value(getattr(value, field.name)) for field in fields(value)
        }
        for name in ("event_time", "detection_time"):
            if name not in result and hasattr(value, name):
                result[name] = _json_value(getattr(value, name))
        return result
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketStateError("event datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, str | bool | int):
        return value
    raise MarketStateError(f"unsupported observable state value: {type(value).__name__}")
