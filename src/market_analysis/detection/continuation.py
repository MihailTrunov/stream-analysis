"""SCRUM-85 EMA-reclaim Trend Continuation v1 over canonical completed frames.

Eligibility is not a lifecycle state. Each candidate freezes its source leg and
both confirmed SwingStructure references; no later structure update moves them.
Bind with ``reentrant=True`` to retain terminal research occurrences.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import cast

from market_analysis.domain import Bar
from market_analysis.indicators import (
    EmaCrossTransition,
    MarketEvent,
    MarketEventType,
    OverallStructure,
    StructureBreak,
    SwingClassification,
    SwingLabel,
    SwingPoint,
    SwingType,
    TrendDirection,
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

_CONTEXT = Context(prec=34)

CONTINUATION_V1 = PatternDefinition(
    pattern_id="EMA_RECLAIM_STRUCTURE_CONTINUATION_V1",
    pattern_version="1",
    name="Trend Continuation v1",
    description=(
        "Aligned qualified trend, opposing bound-EMA cross, reclaim and frozen swing break."
    ),
    required_components=("trend_leg", "trend_leg_qualification", "swing_structure"),
    required_market_events=(
        MarketEventType.EMA_CROSS.value,
        MarketEventType.SWING_HIGH_CLOSE_BREAK.value,
        MarketEventType.SWING_LOW_CLOSE_BREAK.value,
        MarketEventType.SWING_STRUCTURE_CLASSIFIED.value,
        MarketEventType.TREND_LEG_ENDED.value,
    ),
    parameters=(
        ParameterSpec(
            "require_qualified_trend_leg", ParameterType.BOOLEAN, True, supported_values=(True,)
        ),
        ParameterSpec(
            "require_aligned_swing_structure", ParameterType.BOOLEAN, True, supported_values=(True,)
        ),
        ParameterSpec(
            "candidate_trigger",
            ParameterType.STRING,
            "OPPOSING_BOUND_EMA_CLOSE",
            supported_values=("OPPOSING_BOUND_EMA_CLOSE",),
        ),
        ParameterSpec("require_ema_reclaim", ParameterType.BOOLEAN, True, supported_values=(True,)),
        ParameterSpec(
            "confirmation_trigger",
            ParameterType.STRING,
            "SAME_DIRECTION_STRUCTURAL_CLOSE_BREAK",
            supported_values=("SAME_DIRECTION_STRUCTURAL_CLOSE_BREAK",),
        ),
        ParameterSpec(
            "freeze_continuation_reference_at_candidate",
            ParameterType.BOOLEAN,
            True,
            supported_values=(True,),
        ),
        ParameterSpec(
            "invalidate_on_opposite_protected_swing_break",
            ParameterType.BOOLEAN,
            True,
            supported_values=(True,),
        ),
        ParameterSpec("max_candidate_age_bars", ParameterType.INTEGER, 60, minimum=Decimal(1)),
        ParameterSpec("require_hl_lh", ParameterType.BOOLEAN, False, supported_values=(False,)),
        ParameterSpec(
            "require_pullback_depth_filter", ParameterType.BOOLEAN, False, supported_values=(False,)
        ),
        ParameterSpec(
            "require_displacement", ParameterType.BOOLEAN, False, supported_values=(False,)
        ),
    ),
    lifecycle_states=("INACTIVE", "CANDIDATE", "RECLAIMED", "CONFIRMED", "INVALIDATED", "EXPIRED"),
    transitions=(
        TransitionSpec("INACTIVE", "CANDIDATE", "opposing_ema_cross"),
        TransitionSpec("CANDIDATE", "RECLAIMED", "ema_reclaim"),
        TransitionSpec("CANDIDATE", "INVALIDATED", "protected_swing_break"),
        TransitionSpec("RECLAIMED", "INVALIDATED", "protected_swing_break"),
        TransitionSpec("RECLAIMED", "CONFIRMED", "continuation_break"),
        TransitionSpec("CANDIDATE", "EXPIRED", "candidate_age_limit"),
        TransitionSpec("RECLAIMED", "EXPIRED", "candidate_age_limit"),
    ),
    condition_groups=(
        ConditionGroup("entry", ("opposing_ema_cross",)),
        ConditionGroup("reclaim", ("ema_reclaim",)),
        ConditionGroup(
            "resolution", ("protected_swing_break", "continuation_break", "candidate_age_limit")
        ),
    ),
    simultaneous_precedence=(
        "protected_swing_break",
        "ema_reclaim",
        "continuation_break",
        "candidate_age_limit",
    ),
    same_bar_chains=(
        ("ema_reclaim", "continuation_break"),
        ("ema_reclaim", "candidate_age_limit"),
    ),
    context_schema=(
        ContextFieldSpec("source_direction", "string"),
        ContextFieldSpec("source_leg_index", "int"),
        ContextFieldSpec("source_leg_ref", "event_ref"),
        ContextFieldSpec("source_duration_bars", "int"),
        ContextFieldSpec("source_movement_points", "decimal"),
        ContextFieldSpec("source_ema_efficiency_pct", "decimal"),
        ContextFieldSpec("source_ema_instance_id", "string"),
        ContextFieldSpec("source_ema_period", "int"),
        ContextFieldSpec("source_structure", "string"),
        ContextFieldSpec("candidate_bar_index", "int"),
        ContextFieldSpec("candidate_started_at", "datetime"),
        ContextFieldSpec("candidate_cross_ref", "event_ref"),
        ContextFieldSpec("continuation_swing_index", "int"),
        ContextFieldSpec("continuation_swing_type", "string"),
        ContextFieldSpec("continuation_swing_time", "datetime"),
        ContextFieldSpec("continuation_swing_price", "decimal"),
        ContextFieldSpec("continuation_swing_ref", "event_ref"),
        ContextFieldSpec("protected_swing_index", "int"),
        ContextFieldSpec("protected_swing_type", "string"),
        ContextFieldSpec("protected_swing_time", "datetime"),
        ContextFieldSpec("protected_swing_price", "decimal"),
        ContextFieldSpec("protected_swing_ref", "event_ref"),
        ContextFieldSpec("source_favorable_extreme", "decimal"),
        ContextFieldSpec("max_pullback_depth_points", "decimal"),
        ContextFieldSpec("max_pullback_depth_before_reclaim", "decimal"),
        ContextFieldSpec("reclaim_at", "datetime"),
        ContextFieldSpec("reclaim_bar_index", "int"),
        ContextFieldSpec("reclaim_cross_ref", "event_ref"),
        ContextFieldSpec("latest_ema", "decimal"),
        ContextFieldSpec("prior_ema", "decimal"),
        ContextFieldSpec("reaction_swing_ref", "event_ref"),
        ContextFieldSpec("reaction_swing_label", "string"),
        ContextFieldSpec("reaction_swing_price", "decimal"),
        ContextFieldSpec("reaction_swing_count", "int"),
    ),
    rationale_condition_ids=(
        "opposing_ema_cross",
        "ema_reclaim",
        "protected_swing_break",
        "continuation_break",
        "candidate_age_limit",
    ),
    terminal_states=("CONFIRMED", "INVALIDATED", "EXPIRED"),
    rationale_schema_version="detector-evidence-v1",
)


class ContinuationDetector:
    """Evaluate only the approved frozen-reference continuation hypothesis."""

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
        """All occurrence state lives in the runtime-owned PatternInstance."""

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
        if bar_input.definition is not CONTINUATION_V1 and (
            bar_input.definition.semantic_fingerprint() != CONTINUATION_V1.semantic_fingerprint()
        ):
            raise ValueError("ContinuationDetector requires the Trend Continuation v1 definition")
        self._validate_sources(bar_input)
        if bar_input.instance.state == "INACTIVE":
            return self._entry(bar_input)
        if bar_input.instance.state in ("CANDIDATE", "RECLAIMED"):
            return self._advance(bar_input)
        return DetectorOutput(bar_input.instance)

    def _validate_sources(self, bar_input: DetectorInput) -> None:
        components = bar_input.frame.components
        for key in (
            self.trend_leg_instance_id,
            self.qualification_instance_id,
            self.structure_instance_id,
        ):
            if key not in components:
                raise ValueError(f"required continuation source component {key!r} is absent")
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
        components = bar_input.frame.components
        leg = _mapping(components[self.trend_leg_instance_id].get("active_leg"))
        qualification_state = components[self.qualification_instance_id]
        qualification = _mapping(qualification_state.get("active_qualification"))
        source_qual_leg = _mapping(qualification.get("source_leg"))
        direction = leg.get("direction")
        if (
            not leg
            or not qualification
            or qualification_state.get("status") != "QUALIFIED"
            or direction not in ("UP", "DOWN")
            or source_qual_leg.get("leg_index") != leg.get("leg_index")
            or source_qual_leg.get("direction") != direction
        ):
            return DetectorOutput(bar_input.instance)
        structure_state = components[self.structure_instance_id]
        structure = structure_state.get("overall_structure")
        if (direction, structure) not in (
            ("UP", OverallStructure.BULLISH),
            ("DOWN", OverallStructure.BEARISH),
        ):
            return DetectorOutput(bar_input.instance)
        if any(
            event.event_type is MarketEventType.TREND_LEG_ENDED
            and event.source_instance_id == self.trend_leg_instance_id
            for event in bar_input.market_events
        ):
            return DetectorOutput(bar_input.instance)
        opposing = TrendDirection.DOWN if direction == "UP" else TrendDirection.UP
        cross = self._cross(bar_input, opposing)
        if cross is None:
            return DetectorOutput(bar_input.instance)
        continuation = _swing(
            _mapping(structure_state.get("latest_high" if direction == "UP" else "latest_low")),
            SwingType.SWING_HIGH if direction == "UP" else SwingType.SWING_LOW,
        )
        protected = _swing(
            _mapping(structure_state.get("latest_low" if direction == "UP" else "latest_high")),
            SwingType.SWING_LOW if direction == "UP" else SwingType.SWING_HIGH,
        )
        if continuation is None or protected is None:
            return DetectorOutput(bar_input.instance)
        leg_index = leg.get("leg_index")
        duration = leg.get("duration_bars")
        movement = leg.get("directional_movement_points")
        favorable = leg.get("favorable_extreme")
        if (
            type(leg_index) is not int
            or type(duration) is not int
            or not isinstance(movement, Decimal)
            or not isinstance(favorable, Decimal)
        ):
            raise ValueError("qualified TrendLeg lacks indexed movement evidence")
        cross_evidence = cast(EmaCrossTransition, cross.evidence)
        leg_parameters = _mapping(components[self.trend_leg_instance_id].get("parameters"))
        ema_id = cast(str, leg_parameters["ema_instance_id"])
        ema_period = components[ema_id].get("period")
        if type(ema_period) is not int or ema_period < 1:
            raise ValueError("bound EMA lacks a valid period")
        source_ref = _source_leg_ref(bar_input, self.trend_leg_instance_id, leg)
        cross_ref = market_event_semantic_ref(bar_input.frame, cross)
        context: dict[str, object] = {
            "source_direction": str(direction),
            "source_leg_index": leg_index,
            "source_leg_ref": source_ref,
            "source_duration_bars": duration,
            "source_movement_points": movement,
            "source_ema_instance_id": ema_id,
            "source_ema_period": ema_period,
            "source_structure": str(structure),
            "candidate_bar_index": bar_input.completed_bars,
            "candidate_started_at": bar_input.detection_time,
            "candidate_cross_ref": cross_ref,
            "source_favorable_extreme": favorable,
            "max_pullback_depth_points": _depth(str(direction), favorable, bar_input.bar),
            "latest_ema": cross_evidence.current_ema,
            **_frozen_fields("continuation", bar_input, self.structure_instance_id, continuation),
            **_frozen_fields("protected", bar_input, self.structure_instance_id, protected),
        }
        if (
            cross_evidence.ended_segment is not None
            and cross_evidence.ended_segment.efficiency_pct is not None
        ):
            context["source_ema_efficiency_pct"] = cross_evidence.ended_segment.efficiency_pct
        features = self._features(bar_input, context)
        features.update(_cross_features(cross_evidence))
        intent = _intent(
            bar_input,
            "INACTIVE",
            "CANDIDATE",
            "opposing_ema_cross",
            cross,
            features,
            (cross_ref,),
            _typed("string", str(opposing)),
            _typed("string", str(direction)),
            "CROSSES",
        )
        return DetectorOutput(
            replace(bar_input.instance, state="CANDIDATE", context=context), (intent,)
        )

    def _advance(self, bar_input: DetectorInput) -> DetectorOutput:
        context = dict(bar_input.instance.context)
        direction = cast(str, context["source_direction"])
        if direction not in ("UP", "DOWN"):
            raise ValueError("candidate has invalid source direction")
        self._observe_context(bar_input, context)
        protected_type = (
            MarketEventType.SWING_LOW_CLOSE_BREAK
            if direction == "UP"
            else MarketEventType.SWING_HIGH_CLOSE_BREAK
        )
        protected_break = self._matching_break(bar_input, protected_type, context, "protected")
        if protected_break is not None:
            return self._terminal(
                bar_input, context, "INVALIDATED", "protected_swing_break", protected_break
            )
        reclaim = (
            self._cross(bar_input, TrendDirection.UP if direction == "UP" else TrendDirection.DOWN)
            if bar_input.instance.state == "CANDIDATE"
            else None
        )
        continuation_type = (
            MarketEventType.SWING_HIGH_CLOSE_BREAK
            if direction == "UP"
            else MarketEventType.SWING_LOW_CLOSE_BREAK
        )
        continuation_break = self._matching_break(
            bar_input, continuation_type, context, "continuation"
        )
        if reclaim is not None:
            context["max_pullback_depth_before_reclaim"] = context["max_pullback_depth_points"]
            context["reclaim_at"] = reclaim.detection_time
            context["reclaim_bar_index"] = bar_input.completed_bars
            context["reclaim_cross_ref"] = market_event_semantic_ref(bar_input.frame, reclaim)
            context["latest_ema"] = cast(EmaCrossTransition, reclaim.evidence).current_ema
            features = self._features(bar_input, context)
            features.update(_cross_features(cast(EmaCrossTransition, reclaim.evidence)))
            reclaim_intent = _intent(
                bar_input,
                "CANDIDATE",
                "RECLAIMED",
                "ema_reclaim",
                reclaim,
                features,
                (cast(str, context["reclaim_cross_ref"]),),
                _typed("string", direction),
                None,
                "CROSSES",
            )
            if continuation_break is not None:
                confirmed = self._terminal(
                    bar_input,
                    context,
                    "CONFIRMED",
                    "continuation_break",
                    continuation_break,
                    from_state="RECLAIMED",
                )
                return DetectorOutput(confirmed.instance, (reclaim_intent, *confirmed.transitions))
            age = bar_input.completed_bars - cast(int, context["candidate_bar_index"])
            maximum = cast(int, bar_input.parameters["max_candidate_age_bars"])
            if age >= maximum:
                expiry_intent = self._expiry_intent(bar_input, context, "RECLAIMED", age, maximum)
                return DetectorOutput(
                    replace(bar_input.instance, state="EXPIRED", context=context),
                    (reclaim_intent, expiry_intent),
                )
            return DetectorOutput(
                replace(bar_input.instance, state="RECLAIMED", context=context), (reclaim_intent,)
            )
        if bar_input.instance.state == "RECLAIMED" and continuation_break is not None:
            return self._terminal(
                bar_input, context, "CONFIRMED", "continuation_break", continuation_break
            )
        age = bar_input.completed_bars - cast(int, context["candidate_bar_index"])
        maximum = cast(int, bar_input.parameters["max_candidate_age_bars"])
        if age >= maximum:
            intent = self._expiry_intent(bar_input, context, bar_input.instance.state, age, maximum)
            return DetectorOutput(
                replace(bar_input.instance, state="EXPIRED", context=context), (intent,)
            )
        return DetectorOutput(replace(bar_input.instance, context=context))

    def _expiry_intent(
        self,
        bar_input: DetectorInput,
        context: Mapping[str, object],
        from_state: str,
        age: int,
        maximum: int,
    ) -> TransitionIntent:
        return _intent(
            bar_input,
            from_state,
            "EXPIRED",
            "candidate_age_limit",
            None,
            self._features(bar_input, context),
            (),
            _typed("integer", age),
            _typed("integer", maximum),
            ">=",
        )

    def _observe_context(self, bar_input: DetectorInput, context: dict[str, object]) -> None:
        direction = cast(str, context["source_direction"])
        favorable = cast(Decimal, context["source_favorable_extreme"])
        depth = _depth(direction, favorable, bar_input.bar)
        context["max_pullback_depth_points"] = max(
            cast(Decimal, context["max_pullback_depth_points"]), depth
        )
        ema = self._current_ema(bar_input)
        if ema is not None:
            if isinstance(context.get("latest_ema"), Decimal):
                context["prior_ema"] = context["latest_ema"]
            context["latest_ema"] = ema
        wanted = SwingLabel.HL if direction == "UP" else SwingLabel.LH
        for event in bar_input.market_events:
            if (
                event.event_type is MarketEventType.SWING_STRUCTURE_CLASSIFIED
                and event.source_instance_id == self.structure_instance_id
                and isinstance(event.evidence, SwingClassification)
                and event.evidence.label is wanted
                and event.detection_time > cast(datetime, context["candidate_started_at"])
            ):
                context["reaction_swing_ref"] = market_event_semantic_ref(bar_input.frame, event)
                context["reaction_swing_label"] = str(wanted)
                context["reaction_swing_price"] = event.evidence.source_swing.event_price
                context["reaction_swing_count"] = (
                    cast(int, context.get("reaction_swing_count", 0)) + 1
                )

    def _cross(self, bar_input: DetectorInput, direction: TrendDirection) -> MarketEvent | None:
        return next(
            (
                event
                for event in bar_input.market_events
                if event.event_type is MarketEventType.EMA_CROSS
                and event.source_instance_id == self.trend_leg_instance_id
                and isinstance(event.evidence, EmaCrossTransition)
                and event.evidence.direction is direction
            ),
            None,
        )

    def _matching_break(
        self,
        bar_input: DetectorInput,
        event_type: MarketEventType,
        context: Mapping[str, object],
        prefix: str,
    ) -> MarketEvent | None:
        return next(
            (
                event
                for event in bar_input.market_events
                if event.event_type is event_type
                and event.source_instance_id == self.structure_instance_id
                and isinstance(event.evidence, StructureBreak)
                and _same_frozen_swing(event.evidence.reference_swing, context, prefix)
            ),
            None,
        )

    def _terminal(
        self,
        bar_input: DetectorInput,
        context: dict[str, object],
        state: str,
        trigger: str,
        event: MarketEvent,
        *,
        from_state: str | None = None,
    ) -> DetectorOutput:
        evidence = cast(StructureBreak, event.evidence)
        features = self._features(bar_input, context)
        with localcontext(_CONTEXT):
            distance = abs(evidence.breaking_close - evidence.threshold)
        features.update(
            {
                "source_break_ref": _typed(
                    "event_ref", market_event_semantic_ref(bar_input.frame, event)
                ),
                "reference_swing_index_at_break": _typed(
                    "integer", evidence.reference_swing.swing_index
                ),
                "reference_swing_price_at_break": _typed("decimal", evidence.reference_price),
                "break_threshold": _typed("decimal", evidence.threshold),
                "breaking_close": _typed("decimal", evidence.breaking_close),
                "distance_beyond_threshold": _typed("decimal", distance),
                "pre_break_structure": _typed("string", str(evidence.pre_break_overall_structure)),
            }
        )
        if state == "CONFIRMED":
            features["bars_candidate_to_confirmation"] = _typed(
                "integer", bar_input.completed_bars - cast(int, context["candidate_bar_index"])
            )
            features["bars_reclaim_to_confirmation"] = _typed(
                "integer", bar_input.completed_bars - cast(int, context["reclaim_bar_index"])
            )
        for name, label in (
            ("pre_break_high_label", evidence.pre_break_high_label),
            ("pre_break_low_label", evidence.pre_break_low_label),
        ):
            if label is not None:
                features[name] = _typed("string", str(label))
        ref = market_event_semantic_ref(bar_input.frame, event)
        intent = _intent(
            bar_input,
            from_state or bar_input.instance.state,
            state,
            trigger,
            event,
            features,
            (ref,),
            _typed("decimal", evidence.breaking_close),
            _typed("decimal", evidence.threshold),
            ">" if event.event_type is MarketEventType.SWING_HIGH_CLOSE_BREAK else "<",
        )
        return DetectorOutput(replace(bar_input.instance, state=state, context=context), (intent,))

    def _current_ema(self, bar_input: DetectorInput) -> Decimal | None:
        leg_parameters = _mapping(
            bar_input.frame.components[self.trend_leg_instance_id].get("parameters")
        )
        ema_id = leg_parameters.get("ema_instance_id")
        ema_state = bar_input.frame.components.get(str(ema_id), {})
        ema = ema_state.get("ema")
        return ema if isinstance(ema, Decimal) else None

    def _features(
        self, bar_input: DetectorInput, context: Mapping[str, object]
    ) -> dict[str, Mapping[str, object]]:
        age = bar_input.completed_bars - cast(int, context["candidate_bar_index"])
        features: dict[str, Mapping[str, object]] = {
            "source_direction": _typed("string", context["source_direction"]),
            "source_leg_ref": _typed("event_ref", context["source_leg_ref"]),
            "source_leg_index": _typed("integer", context["source_leg_index"]),
            "source_duration_bars": _typed("integer", context["source_duration_bars"]),
            "source_movement_points": _typed("decimal", context["source_movement_points"]),
            "source_ema_instance_id": _typed("string", context["source_ema_instance_id"]),
            "source_ema_period": _typed("integer", context["source_ema_period"]),
            "source_structure": _typed("string", context["source_structure"]),
            "candidate_started_at": _typed("datetime", context["candidate_started_at"]),
            "candidate_cross_ref": _typed("event_ref", context["candidate_cross_ref"]),
            "bars_since_candidate": _typed("integer", age),
            "max_candidate_age_bars": _typed(
                "integer", bar_input.parameters["max_candidate_age_bars"]
            ),
            "continuation_swing_ref": _typed("event_ref", context["continuation_swing_ref"]),
            "continuation_swing_index": _typed("integer", context["continuation_swing_index"]),
            "continuation_swing_type": _typed("string", context["continuation_swing_type"]),
            "continuation_swing_price": _typed("decimal", context["continuation_swing_price"]),
            "protected_swing_ref": _typed("event_ref", context["protected_swing_ref"]),
            "protected_swing_index": _typed("integer", context["protected_swing_index"]),
            "protected_swing_type": _typed("string", context["protected_swing_type"]),
            "protected_swing_price": _typed("decimal", context["protected_swing_price"]),
            "max_pullback_depth_points": _typed("decimal", context["max_pullback_depth_points"]),
            "pullback_depth_method": _typed("string", "BAR_ADVERSE_EXTREME_FROM_FROZEN_FAVORABLE"),
            "current_close": _typed("decimal", bar_input.bar.close),
        }
        for name in (
            "source_ema_efficiency_pct",
            "reclaim_at",
            "reclaim_cross_ref",
            "reaction_swing_ref",
            "reaction_swing_label",
            "reaction_swing_price",
            "reaction_swing_count",
            "max_pullback_depth_before_reclaim",
        ):
            value = context.get(name)
            if value is not None:
                kind = (
                    "datetime"
                    if name == "reclaim_at"
                    else "event_ref"
                    if name.endswith("_ref")
                    else "integer"
                    if name == "reaction_swing_count"
                    else "decimal"
                    if isinstance(value, Decimal)
                    else "string"
                )
                features[name] = _typed(kind, value)
        if "reclaim_bar_index" in context:
            reclaim_index = cast(int, context["reclaim_bar_index"])
            features["bars_candidate_to_reclaim"] = _typed(
                "integer", reclaim_index - cast(int, context["candidate_bar_index"])
            )
            features["bars_reclaim_to_current"] = _typed(
                "integer", bar_input.completed_bars - reclaim_index
            )
        movement = cast(Decimal, context["source_movement_points"])
        depth = cast(Decimal, context["max_pullback_depth_points"])
        if movement:
            with localcontext(_CONTEXT):
                features["pullback_pct_of_source_movement"] = _typed(
                    "decimal", Decimal(100) * depth / abs(movement)
                )
        ema = self._current_ema(bar_input)
        if ema is not None:
            features["current_ema"] = _typed("decimal", ema)
            with localcontext(_CONTEXT):
                features["close_to_ema_distance"] = _typed("decimal", bar_input.bar.close - ema)
            features["close_ema_side"] = _typed(
                "string",
                "ABOVE"
                if bar_input.bar.close > ema
                else "BELOW"
                if bar_input.bar.close < ema
                else "ON",
            )
            previous = context.get("prior_ema")
            if isinstance(previous, Decimal):
                with localcontext(_CONTEXT):
                    features["ema_slope"] = _typed("decimal", ema - previous)
        structure = bar_input.frame.components[self.structure_instance_id]
        features["structure_at_detection"] = _typed(
            "string", str(structure.get("overall_structure"))
        )
        for key in ("latest_high", "latest_low"):
            label = _mapping(structure.get(key)).get("label")
            if label is not None:
                features[f"{key}_label"] = _typed("string", str(label))
        leg = _mapping(bar_input.frame.components[self.trend_leg_instance_id].get("active_leg"))
        features["source_leg_active_at_detection"] = _typed(
            "boolean", leg.get("leg_index") == context["source_leg_index"]
        )
        for instance_id, component in bar_input.frame.components.items():
            if (
                component.get("component_id") == "atr"
                and bar_input.frame.availability.get(instance_id) == "AVAILABLE"
            ):
                atr = component.get("atr")
                if isinstance(atr, Decimal):
                    features[f"atr_{instance_id}"] = _typed("decimal", atr)
                    if atr:
                        with localcontext(_CONTEXT):
                            features[f"pullback_depth_in_atr_{instance_id}"] = _typed(
                                "decimal", depth / atr
                            )
        session = bar_input.frame.components.get("session")
        if session is not None and bar_input.frame.availability.get("session") == "AVAILABLE":
            for name in ("session_name", "trading_date"):
                value = session.get(name)
                if isinstance(value, str):
                    features[name] = _typed("string", value)
        return features


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _swing(
    classification: Mapping[str, object], expected_type: SwingType
) -> Mapping[str, object] | None:
    point = _mapping(classification.get("source_swing"))
    if (
        type(point.get("swing_index")) is int
        and point.get("swing_type") == expected_type
        and isinstance(point.get("event_time"), datetime)
        and isinstance(point.get("event_price"), Decimal)
    ):
        return point
    return None


def _frozen_fields(
    prefix: str, bar_input: DetectorInput, instance_id: str, swing: Mapping[str, object]
) -> dict[str, object]:
    event_time = cast(datetime, swing["event_time"])
    payload = {
        "version": "swing-reference-v1",
        "dataset_revision_id": bar_input.dataset_revision_id,
        "detection_config_hash": bar_input.detection_config_hash,
        "instrument_id": bar_input.instrument_id,
        "timeframe": bar_input.timeframe.value,
        "source_instance_id": instance_id,
        "swing_index": swing["swing_index"],
        "swing_type": str(swing["swing_type"]),
        "event_time": event_time.astimezone(UTC).isoformat(),
        "event_price": format(cast(Decimal, swing["event_price"]), "f"),
    }
    ref = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        f"{prefix}_swing_index": swing["swing_index"],
        f"{prefix}_swing_type": str(swing["swing_type"]),
        f"{prefix}_swing_time": event_time,
        f"{prefix}_swing_price": swing["event_price"],
        f"{prefix}_swing_ref": ref,
    }


def _same_frozen_swing(swing: SwingPoint, context: Mapping[str, object], prefix: str) -> bool:
    return (
        swing.swing_index == context[f"{prefix}_swing_index"]
        and swing.swing_type == context[f"{prefix}_swing_type"]
        and swing.event_time == context[f"{prefix}_swing_time"]
        and swing.event_price == context[f"{prefix}_swing_price"]
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


def _depth(direction: str, favorable: Decimal, bar: Bar) -> Decimal:
    with localcontext(_CONTEXT):
        return max(Decimal(0), favorable - bar.low if direction == "UP" else bar.high - favorable)


def _typed(kind: str, value: object) -> Mapping[str, object]:
    if kind == "decimal":
        value = format(cast(Decimal, value), "f")
    elif kind == "datetime":
        value = cast(datetime, value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {"type": kind, "value": value}


def _cross_features(cross: EmaCrossTransition) -> dict[str, Mapping[str, object]]:
    with localcontext(_CONTEXT):
        slope = cross.current_ema - cross.previous_ema
    return {
        "previous_close": _typed("decimal", cross.previous_close),
        "previous_ema": _typed("decimal", cross.previous_ema),
        "current_close": _typed("decimal", cross.current_close),
        "cross_ema": _typed("decimal", cross.current_ema),
        "ema_slope_at_cross": _typed("decimal", slope),
    }


def _intent(
    bar_input: DetectorInput,
    from_state: str,
    to_state: str,
    trigger: str,
    event: MarketEvent | None,
    features: Mapping[str, Mapping[str, object]],
    refs: tuple[str, ...],
    value: Mapping[str, object],
    threshold: Mapping[str, object] | None,
    operator: str,
) -> TransitionIntent:
    return TransitionIntent(
        pattern_id=bar_input.pattern_id,
        pattern_version=bar_input.pattern_version,
        instance_id=bar_input.instance.instance_id,
        from_state=from_state,
        to_state=to_state,
        trigger_id=trigger,
        event_time=event.event_time if event is not None else bar_input.detection_time,
        detection_time=bar_input.detection_time,
        rationale={
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
                    "features": features,
                },
            ),
        },
    )
