"""SCRUM-83 Trend Reversal v1 over completed canonical market-state frames.

The detector is stateless: the runtime-owned PatternInstance context contains
the frozen source leg identity and opening protected swing. Bind it with
``DetectorBinding(REVERSAL_V1, ReversalDetector(), reentrant=True)`` so a
terminal occurrence remains auditable and a later cross can start a new one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import cast

from market_analysis.indicators import (
    EmaCrossTransition,
    MarketEventType,
    OverallStructure,
    StructureBreak,
    SwingClassification,
    SwingLabel,
    SwingPoint,
    TrendDirection,
    TrendLegTransition,
    TrendLegTransitionType,
    market_event_semantic_ref,
)
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    TransitionSpec,
)

from .inputs import DetectorInput
from .records import DetectorOutput, TransitionIntent

REVERSAL_V1 = PatternDefinition(
    pattern_id="EMA_STRUCTURE_REVERSAL_V1",
    pattern_version="1",
    name="Trend Reversal v1",
    description=(
        "Opposing EMA close-cross in an aligned qualified leg, then its "
        "protected-swing close-break."
    ),
    required_components=("trend_leg", "trend_leg_qualification", "swing_structure"),
    required_market_events=(
        MarketEventType.EMA_CROSS.value,
        MarketEventType.SWING_LOW_CLOSE_BREAK.value,
        MarketEventType.SWING_HIGH_CLOSE_BREAK.value,
        MarketEventType.SWING_STRUCTURE_CLASSIFIED.value,
        MarketEventType.TREND_LEG_ENDED.value,
    ),
    parameters=(
        ParameterSpec(
            "max_candidate_age_bars",
            ParameterType.INTEGER,
            60,
            minimum=Decimal(1),
            description="Subsequent completed bars allowed before expiry; cross bar has age zero.",
        ),
    ),
    lifecycle_states=("INACTIVE", "CANDIDATE", "CONFIRMED", "INVALIDATED", "EXPIRED"),
    transitions=(
        TransitionSpec("INACTIVE", "CANDIDATE", "opposing_ema_cross"),
        TransitionSpec("CANDIDATE", "CONFIRMED", "protected_swing_break"),
        TransitionSpec("CANDIDATE", "INVALIDATED", "same_direction_extreme"),
        TransitionSpec("CANDIDATE", "EXPIRED", "candidate_age_limit"),
    ),
    condition_groups=(
        ConditionGroup("entry", ("opposing_ema_cross",)),
        ConditionGroup(
            "resolution",
            ("protected_swing_break", "same_direction_extreme", "candidate_age_limit"),
        ),
    ),
    simultaneous_precedence=(
        "protected_swing_break",
        "same_direction_extreme",
        "candidate_age_limit",
    ),
    context_schema=(
        ContextFieldSpec("source_direction", "string"),
        ContextFieldSpec("candidate_direction", "string"),
        ContextFieldSpec("source_leg_index", "int"),
        ContextFieldSpec("source_leg_ref", "event_ref"),
        ContextFieldSpec("source_duration_bars", "int"),
        ContextFieldSpec("source_movement_points", "decimal"),
        ContextFieldSpec("source_ema_efficiency_pct", "decimal"),
        ContextFieldSpec("protected_swing_index", "int"),
        ContextFieldSpec("protected_swing_event_time", "datetime"),
        ContextFieldSpec("protected_swing_price", "decimal"),
        ContextFieldSpec("candidate_bar_index", "int"),
        ContextFieldSpec("candidate_started_at", "datetime"),
        ContextFieldSpec("candidate_cross_ref", "event_ref"),
        ContextFieldSpec("source_structure", "string"),
        ContextFieldSpec("latest_ema", "decimal"),
    ),
    rationale_condition_ids=(
        "opposing_ema_cross",
        "protected_swing_break",
        "same_direction_extreme",
        "candidate_age_limit",
    ),
    terminal_states=("CONFIRMED", "INVALIDATED", "EXPIRED"),
    rationale_schema_version="detector-evidence-v1",
)


class ReversalDetector:
    """Evaluate only the approved v1 transition table, with no private indicators."""

    def __init__(
        self,
        *,
        trend_leg_instance_id: str = "trend_leg",
        qualification_instance_id: str = "trend_leg_qualification",
        structure_instance_id: str = "swing_structure",
    ) -> None:
        for value in (trend_leg_instance_id, qualification_instance_id, structure_instance_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("component instance ids must be non-empty")
        self.trend_leg_instance_id = trend_leg_instance_id
        self.qualification_instance_id = qualification_instance_id
        self.structure_instance_id = structure_instance_id

    def reset(self) -> None:
        """All occurrence state is runtime-owned; no private history exists."""

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
        if bar_input.definition is not REVERSAL_V1 and (
            bar_input.definition.semantic_fingerprint() != REVERSAL_V1.semantic_fingerprint()
        ):
            raise ValueError("ReversalDetector requires the Trend Reversal v1 definition")
        self._validate_sources(bar_input)
        instance = bar_input.instance
        if instance.state == "INACTIVE":
            return self._entry(bar_input)
        if instance.state == "CANDIDATE":
            return self._resolve(bar_input)
        return DetectorOutput(instance)

    def _validate_sources(self, bar_input: DetectorInput) -> None:
        components = bar_input.frame.components
        for key in (
            self.trend_leg_instance_id,
            self.qualification_instance_id,
            self.structure_instance_id,
        ):
            if key not in components:
                raise ValueError(f"required reversal source component {key!r} is absent")
        leg_parameters = _mapping(components[self.trend_leg_instance_id].get("parameters"))
        qualification_parameters = _mapping(
            components[self.qualification_instance_id].get("parameters")
        )
        if leg_parameters.get("structure_instance_id") != self.structure_instance_id:
            raise ValueError("TrendLeg is not bound to the selected SwingStructure")
        if qualification_parameters.get("trend_leg_instance_id") != self.trend_leg_instance_id:
            raise ValueError("qualification is not bound to the selected TrendLeg")
        ema_id = leg_parameters.get("ema_instance_id")
        if not isinstance(ema_id, str) or ema_id not in components:
            raise ValueError("TrendLeg bound EMA source is absent")

    def _entry(self, bar_input: DetectorInput) -> DetectorOutput:
        frame = bar_input.frame
        leg_state = _component(frame.components, self.trend_leg_instance_id)
        qualification_state = _component(frame.components, self.qualification_instance_id)
        structure_state = _component(frame.components, self.structure_instance_id)
        leg = _mapping(leg_state.get("active_leg"))
        qualification = _mapping(qualification_state.get("active_qualification"))
        if not leg or not qualification or qualification_state.get("status") != "QUALIFIED":
            return DetectorOutput(bar_input.instance)
        source_qual_leg = _mapping(qualification.get("source_leg"))
        direction = leg.get("direction")
        if (
            direction not in ("UP", "DOWN")
            or source_qual_leg.get("leg_index") != leg.get("leg_index")
            or source_qual_leg.get("direction") != direction
        ):
            return DetectorOutput(bar_input.instance)
        structure = structure_state.get("overall_structure")
        if (direction, structure) not in (
            ("UP", OverallStructure.BULLISH),
            ("DOWN", OverallStructure.BEARISH),
        ):
            return DetectorOutput(bar_input.instance)
        protected = _mapping(leg.get("protected_swing"))
        swing_index = protected.get("swing_index")
        event_time = protected.get("event_time")
        price = protected.get("event_price")
        leg_index = leg.get("leg_index")
        if (
            type(swing_index) is not int
            or type(leg_index) is not int
            or not isinstance(event_time, datetime)
            or not isinstance(price, Decimal)
        ):
            raise ValueError("active TrendLeg has incomplete protected-swing evidence")
        # The source leg must still be active after this bar's canonical break
        # handling. A break on the cross bar cannot spawn a candidate from an
        # already-ended leg, even if an earlier snapshot was eligible.
        if any(
            event.event_type is MarketEventType.TREND_LEG_ENDED
            and event.source_instance_id == self.trend_leg_instance_id
            for event in bar_input.market_events
        ):
            return DetectorOutput(bar_input.instance)
        opposing = TrendDirection.DOWN if direction == "UP" else TrendDirection.UP
        cross = next(
            (
                event
                for event in bar_input.market_events
                if event.event_type is MarketEventType.EMA_CROSS
                and event.source_instance_id == self.trend_leg_instance_id
                and isinstance(event.evidence, EmaCrossTransition)
                and event.evidence.direction is opposing
            ),
            None,
        )
        if cross is None:
            return DetectorOutput(bar_input.instance)
        cross_evidence = cast(EmaCrossTransition, cross.evidence)
        duration = leg.get("duration_bars")
        movement = leg.get("directional_movement_points")
        if type(duration) is not int or not isinstance(movement, Decimal):
            raise ValueError("qualified TrendLeg lacks duration or movement evidence")
        source_ref = _source_leg_ref(bar_input, self.trend_leg_instance_id, leg)
        cross_ref = market_event_semantic_ref(frame, cross)
        context: dict[str, object] = {
            "source_direction": str(direction),
            "candidate_direction": str(opposing),
            "source_leg_index": leg_index,
            "source_leg_ref": source_ref,
            "source_duration_bars": duration,
            "source_movement_points": movement,
            "protected_swing_index": swing_index,
            "protected_swing_event_time": event_time,
            "protected_swing_price": price,
            "candidate_bar_index": frame.completed_bars,
            "candidate_started_at": bar_input.detection_time,
            "candidate_cross_ref": cross_ref,
            "source_structure": str(structure),
            "latest_ema": cross_evidence.current_ema,
        }
        efficiency = (
            cross_evidence.ended_segment.efficiency_pct
            if cross_evidence.ended_segment is not None
            else None
        )
        if efficiency is not None:
            context["source_ema_efficiency_pct"] = efficiency
        features = {
            "source_leg_ref": _typed("event_ref", source_ref),
            "source_leg_index": _typed("integer", leg_index),
            "source_duration_bars": _typed("integer", duration),
            "source_movement_points": _typed("decimal", movement),
            "source_structure": _typed("string", str(structure)),
            "protected_swing_index": _typed("integer", swing_index),
            "protected_swing_price": _typed("decimal", price),
            "candidate_started_at": _typed("datetime", bar_input.detection_time),
            "previous_close": _typed("decimal", cross_evidence.previous_close),
            "previous_ema": _typed("decimal", cross_evidence.previous_ema),
            "current_close": _typed("decimal", cross_evidence.current_close),
            "current_ema": _typed("decimal", cross_evidence.current_ema),
            "ema_slope": _typed(
                "decimal", cross_evidence.current_ema - cross_evidence.previous_ema
            ),
        }
        if efficiency is not None:
            features["source_ema_efficiency_pct"] = _typed("decimal", efficiency)
        intent = _intent(
            bar_input,
            "INACTIVE",
            "CANDIDATE",
            "opposing_ema_cross",
            cross.event_time,
            (cross_ref,),
            _typed("string", str(opposing)),
            _typed("string", str(direction)),
            "CROSSES",
            features=features,
        )
        return DetectorOutput(
            replace(bar_input.instance, state="CANDIDATE", context=context), (intent,)
        )

    def _resolve(self, bar_input: DetectorInput) -> DetectorOutput:
        context = bar_input.instance.context
        direction = context["source_direction"]
        if direction not in ("UP", "DOWN"):
            raise ValueError("candidate has invalid source direction")
        expected_break = (
            MarketEventType.SWING_LOW_CLOSE_BREAK
            if direction == "UP"
            else MarketEventType.SWING_HIGH_CLOSE_BREAK
        )
        source_end = next(
            (
                event
                for event in bar_input.market_events
                if event.event_type is MarketEventType.TREND_LEG_ENDED
                and event.source_instance_id == self.trend_leg_instance_id
                and isinstance(event.evidence, TrendLegTransition)
                and event.evidence.transition_type is TrendLegTransitionType.TERMINATED
                and event.evidence.leg.leg_index == context["source_leg_index"]
                and event.evidence.leg.direction == direction
            ),
            None,
        )
        if source_end is not None:
            end_evidence = cast(TrendLegTransition, source_end.evidence)
            close = end_evidence.breaking_close
            threshold = end_evidence.leg.break_threshold
            if close is None or not (close < threshold if direction == "UP" else close > threshold):
                raise ValueError("source TrendLeg end lacks a valid protected-swing close-break")
            refs = [market_event_semantic_ref(bar_input.frame, source_end)]
            corroborating_break = next(
                (
                    event
                    for event in bar_input.market_events
                    if event.event_type is expected_break
                    and event.source_instance_id == self.structure_instance_id
                    and isinstance(event.evidence, StructureBreak)
                    and _same_swing(
                        event.evidence.reference_swing,
                        end_evidence.leg.protected_swing,
                    )
                ),
                None,
            )
            if corroborating_break is not None:
                refs.append(market_event_semantic_ref(bar_input.frame, corroborating_break))
            return self._terminal(
                bar_input,
                "CONFIRMED",
                "protected_swing_break",
                source_end.event_time,
                tuple(refs),
                _typed("decimal", close),
                _typed("decimal", threshold),
                ">" if direction == "DOWN" else "<",
                event_features={
                    "protected_swing_index_at_break": _typed(
                        "integer", end_evidence.leg.protected_swing.swing_index
                    ),
                    "protected_swing_price_at_break": _typed(
                        "decimal", end_evidence.leg.protected_swing.event_price
                    ),
                },
            )
        expected_label = SwingLabel.HH if direction == "UP" else SwingLabel.LL
        new_extreme = next(
            (
                event
                for event in bar_input.market_events
                if event.event_type is MarketEventType.SWING_STRUCTURE_CLASSIFIED
                and event.source_instance_id == self.structure_instance_id
                and isinstance(event.evidence, SwingClassification)
                and event.evidence.label is expected_label
                and event.detection_time > cast(datetime, context["candidate_started_at"])
            ),
            None,
        )
        if new_extreme is not None:
            ref = market_event_semantic_ref(bar_input.frame, new_extreme)
            classification = cast(SwingClassification, new_extreme.evidence)
            return self._terminal(
                bar_input,
                "INVALIDATED",
                "same_direction_extreme",
                new_extreme.event_time,
                (ref,),
                _typed("string", str(classification.label)),
                None,
                "PRESENT",
                event_features={
                    "invalidating_swing_index": _typed(
                        "integer", classification.source_swing.swing_index
                    ),
                    "invalidating_swing_price": _typed(
                        "decimal", classification.source_swing.event_price
                    ),
                },
            )
        age = bar_input.completed_bars - cast(int, context["candidate_bar_index"])
        maximum = bar_input.parameters["max_candidate_age_bars"]
        if type(maximum) is not int or maximum < 1:
            raise ValueError("max_candidate_age_bars must be a positive integer")
        if age < maximum:
            ema = self._current_ema(bar_input)
            if ema is None:
                return DetectorOutput(bar_input.instance)
            return DetectorOutput(
                replace(
                    bar_input.instance,
                    context={**context, "latest_ema": ema},
                )
            )
        return self._terminal(
            bar_input,
            "EXPIRED",
            "candidate_age_limit",
            bar_input.detection_time,
            (),
            _typed("integer", age),
            _typed("integer", maximum),
            ">=",
        )

    def _terminal(
        self,
        bar_input: DetectorInput,
        state: str,
        trigger: str,
        event_time: datetime,
        refs: tuple[str, ...],
        value: Mapping[str, object],
        threshold: Mapping[str, object] | None,
        operator: str,
        *,
        event_features: Mapping[str, Mapping[str, object]] | None = None,
    ) -> DetectorOutput:
        features = {**self._resolution_features(bar_input), **(event_features or {})}
        intent = _intent(
            bar_input,
            "CANDIDATE",
            state,
            trigger,
            event_time,
            refs,
            value,
            threshold,
            operator,
            features=features,
        )
        return DetectorOutput(replace(bar_input.instance, state=state), (intent,))

    def _current_ema(self, bar_input: DetectorInput) -> Decimal | None:
        leg_state = bar_input.frame.components[self.trend_leg_instance_id]
        ema_id = _mapping(leg_state.get("parameters")).get("ema_instance_id")
        ema_state = bar_input.frame.components.get(str(ema_id), {})
        ema = ema_state.get("ema")
        return ema if isinstance(ema, Decimal) else None

    def _resolution_features(self, bar_input: DetectorInput) -> Mapping[str, Mapping[str, object]]:
        context = bar_input.instance.context
        age = bar_input.completed_bars - cast(int, context["candidate_bar_index"])
        start = cast(datetime, context["candidate_started_at"])
        features: dict[str, Mapping[str, object]] = {
            "source_leg_ref": _typed("event_ref", context["source_leg_ref"]),
            "source_leg_index": _typed("integer", context["source_leg_index"]),
            "candidate_cross_ref": _typed("event_ref", context["candidate_cross_ref"]),
            "candidate_started_at": _typed("datetime", start),
            "bars_since_candidate": _typed("integer", age),
            "seconds_since_candidate": _typed(
                "integer", int((bar_input.detection_time - start).total_seconds())
            ),
            "protected_swing_price_at_entry": _typed("decimal", context["protected_swing_price"]),
        }
        structure = bar_input.frame.components[self.structure_instance_id]
        structure_break = next(
            (
                event.evidence
                for event in bar_input.market_events
                if event.source_instance_id == self.structure_instance_id
                and isinstance(event.evidence, StructureBreak)
            ),
            None,
        )
        if structure_break is not None:
            features["pre_break_structure"] = _typed(
                "string", str(structure_break.pre_break_overall_structure)
            )
            for key, label in (
                ("pre_break_high_label", structure_break.pre_break_high_label),
                ("pre_break_low_label", structure_break.pre_break_low_label),
            ):
                if label is not None:
                    features[key] = _typed("string", str(label))
        else:
            features["structure_at_resolution"] = _typed(
                "string", str(structure["overall_structure"])
            )
        ema = self._current_ema(bar_input)
        if ema is not None:
            features["current_ema"] = _typed("decimal", ema)
            features["close_to_ema_distance"] = _typed("decimal", bar_input.bar.close - ema)
            side = (
                "ABOVE"
                if bar_input.bar.close > ema
                else "BELOW"
                if bar_input.bar.close < ema
                else "ON"
            )
            features["close_ema_side"] = _typed("string", side)
            prior = context.get("latest_ema")
            if isinstance(prior, Decimal):
                features["ema_slope"] = _typed("decimal", ema - prior)
        return features


def _component(components: Mapping[str, Mapping[str, object]], key: str) -> Mapping[str, object]:
    return components.get(key, {})


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _same_swing(swing: SwingPoint, protected: SwingPoint) -> bool:
    return (
        swing.swing_index == protected.swing_index
        and swing.event_time == protected.event_time
        and swing.event_price == protected.event_price
    )


def _source_leg_ref(
    bar_input: DetectorInput, source_instance_id: str, leg: Mapping[str, object]
) -> str:
    initial = _mapping(leg.get("initial_protected_swing"))
    event_time = leg.get("event_time")
    initial_time = initial.get("event_time")
    if not isinstance(event_time, datetime) or not isinstance(initial_time, datetime):
        raise ValueError("TrendLeg semantic reference requires source event times")
    payload = {
        "version": "trend-leg-semantic-v1",
        "dataset_revision_id": bar_input.dataset_revision_id,
        "detection_config_hash": bar_input.detection_config_hash,
        "instrument_id": bar_input.instrument_id,
        "timeframe": bar_input.timeframe.value,
        "source_instance_id": source_instance_id,
        "leg_index": leg["leg_index"],
        "direction": str(leg["direction"]),
        "event_time": event_time.astimezone(UTC).isoformat(),
        "initial_protected_swing_index": initial["swing_index"],
        "initial_protected_swing_time": initial_time.astimezone(UTC).isoformat(),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _typed(kind: str, value: object) -> Mapping[str, object]:
    if kind == "decimal":
        value = format(cast(Decimal, value), "f")
    elif kind == "datetime":
        value = cast(datetime, value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {"type": kind, "value": value}


def _intent(
    bar_input: DetectorInput,
    from_state: str,
    to_state: str,
    trigger: str,
    event_time: datetime,
    refs: tuple[str, ...],
    value: Mapping[str, object],
    threshold: Mapping[str, object] | None,
    operator: str,
    *,
    features: Mapping[str, Mapping[str, object]] | None = None,
) -> TransitionIntent:
    rationale = {
        "schema": "detector-evidence-v1",
        "condition": trigger,
        "source_market_event_refs": refs,
        "items": (
            {
                "condition_id": trigger,
                "status": "PASS",
                "value": value,
                "operator": operator,
                "threshold": threshold,
                "units": None,
                "source_refs": refs,
                "features": {
                    "candidate_direction": _typed(
                        "string",
                        str(bar_input.instance.context.get("candidate_direction", "PENDING")),
                    ),
                    **(features or {}),
                },
            },
        ),
    }
    return TransitionIntent(
        pattern_id=bar_input.pattern_id,
        pattern_version=bar_input.pattern_version,
        instance_id=bar_input.instance.instance_id,
        from_state=from_state,
        to_state=to_state,
        trigger_id=trigger,
        event_time=event_time,
        detection_time=bar_input.detection_time,
        rationale=rationale,
    )
