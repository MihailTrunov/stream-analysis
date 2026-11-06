"""Small, typed primary-population predicates over event-native and approved X fields."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from market_analysis.config import DetectionAnalysisConfig, EvaluationPlan, EventSetSelection
from market_analysis.detection.context_snapshots import DetectionTimeContextSnapshot
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.pattern_instances import PersistedDetectorEvent

NATIVE_FIELDS = {
    "event.pattern_id": "string",
    "event.pattern_version": "string",
    "event.instrument_id": "string",
    "event.timeframe": "string",
    "event.event_kind": "string",
    "event.transition_reason": "string",
    "event.sequence": "int",
    "event.within_bar_ordinal": "int",
    "event.event_time": "datetime",
    "event.detection_time": "datetime",
    "event.available_at": "datetime",
    "context.completed_bars": "int",
    "context.detector.state": "string",
    "context.detector.from_state": "string",
    "context.detector.trigger_id": "string",
}
COMPONENT_FIELDS = {
    "session": {"session_name": "string", "is_open": "bool", "is_break": "bool"},
    "ema": {"ema": "decimal", "period": "int", "value_ready": "bool"},
    "atr": {"atr": "decimal", "true_range": "decimal", "period": "int", "value_ready": "bool"},
    "swing_structure": {"overall_structure": "string"},
    "trend_leg": {
        "active_leg.direction": "string",
        "active_leg.duration_bars": "int",
        "active_leg.directional_movement_points": "decimal",
        "active_leg.protected_swing.event_price": "decimal",
    },
    "trend_leg_qualification": {
        "status": "string",
        "active_qualification.duration_gate_passed": "bool",
        "active_qualification.directional_move_gate_passed": "bool",
    },
    "range_state": {
        "choppiness_score": "decimal",
        "choppiness_regime": "string",
        "compression_score": "decimal",
        "compression_regime": "string",
        "bandwidth_percentile": "decimal",
    },
}
MISSING = object()


class EventSetError(ValueError):
    """A primary EventSet attempts unsupported or non-observable selection."""


def field_types(
    configs: Sequence[DetectionAnalysisConfig],
    definitions: Mapping[tuple[str, str], PatternDefinition],
) -> dict[str, str]:
    result = dict(NATIVE_FIELDS)
    for family in (*COMPONENT_FIELDS, "swing_point"):
        result[f"context.optional_components.{family}"] = "string"
    result.update(
        {
            f"context.components.session.{key}": kind
            for key, kind in COMPONENT_FIELDS["session"].items()
        }
    )
    result["context.availability.session"] = "string"
    for config in configs:
        for component in config.components:
            if not component.enabled:
                continue
            key = component.effective_instance_id
            result[f"context.availability.{key}"] = "string"
            for name, kind in COMPONENT_FIELDS.get(component.component_id, {}).items():
                result[f"context.components.{key}.{name}"] = kind
        for pattern in config.patterns:
            if not pattern.enabled:
                continue
            for field in definitions[(pattern.pattern_id, pattern.pattern_version)].context_schema:
                path = f"context.detector.context.{field.field_id}"
                kind = "string" if field.type_name == "event_ref" else field.type_name
                if path in result and result[path] != kind:
                    raise EventSetError(f"ambiguous typed EventSet field: {path}")
                result[path] = kind
    return result


def _typed(value: object, kind: str) -> object:
    if value is None:
        return None
    if kind == "decimal":
        if isinstance(value, bool):
            raise EventSetError("boolean is not a decimal predicate value")
        try:
            converted = value if isinstance(value, Decimal) else Decimal(str(value))
        except (ValueError, InvalidOperation) as exc:
            raise EventSetError("invalid decimal EventSet value") from exc
        if not converted.is_finite():
            raise EventSetError("EventSet decimals must be finite")
        return converted
    if kind == "datetime":
        converted_time = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(converted_time, datetime) or converted_time.tzinfo is None:
            raise EventSetError("EventSet datetime must be timezone-aware")
        return converted_time.astimezone(UTC)
    expected = {"int": int, "bool": bool, "string": str}[kind]
    if type(value) is not expected:
        raise EventSetError(f"EventSet value must have type {kind}")
    return value


def normalize_event_set(plan: EvaluationPlan, fields: Mapping[str, str]) -> EvaluationPlan:
    if plan.event_set is None:
        return plan
    predicates = []
    for item in plan.event_set.predicates:
        kind = fields.get(item.field)
        if kind is None:
            raise EventSetError(
                f"EventSet field is not approved detection-time information: {item.field}"
            )
        if item.operator not in {"EQ", "NE", "IS_UNAVAILABLE"} and (
            kind not in {"int", "decimal", "datetime"} or item.value is None
        ):
            raise EventSetError("ordered EventSet predicates require numeric/datetime operands")
        predicates.append(item.model_copy(update={"value": _typed(item.value, kind)}))
    return plan.model_copy(update={"event_set": EventSetSelection(predicates=tuple(predicates))})


def event_set_decision(
    event: PersistedDetectorEvent,
    snapshot: DetectionTimeContextSnapshot,
    plan: EvaluationPlan,
    *,
    selected_start: datetime,
    selected_end: datetime,
    fields: Mapping[str, str],
) -> tuple[bool, str | None]:
    if (
        event.event_id != snapshot.event_id
        or event.event_semantic_key != (snapshot.source_event_semantic_ref)
        or event.instance_semantic_key != snapshot.instance_semantic_key
    ):
        raise EventSetError("EventSet source and detection-time context disagree")
    if not selected_start <= event.detection_time < selected_end or (
        snapshot.available_at > selected_end
    ):
        return False, "source event is outside the selected detection interval"
    normalized = normalize_event_set(plan, fields)
    if normalized.event_set is None:
        return True, None
    for predicate in normalized.event_set.predicates:
        parts = predicate.field.split(".")
        actual: object
        if parts[0] == "event":
            actual = (
                snapshot.available_at if parts[1] == "available_at" else getattr(event, parts[1])
            )
            if parts[1] == "timeframe":
                actual = event.timeframe.value
        else:
            actual = snapshot.values
            for part in parts[1:]:
                if not isinstance(actual, Mapping) or part not in actual:
                    actual = MISSING
                    break
                actual = actual[part]
        if predicate.operator == "IS_UNAVAILABLE":
            matches = actual is MISSING or actual is None
        elif actual is MISSING:
            matches = False
        else:
            actual = _typed(actual, fields[predicate.field])
            expected = predicate.value
            if actual is None and expected is not None:
                matches = False
            elif predicate.operator in {"EQ", "NE"}:
                matches = actual == expected
                if predicate.operator == "NE":
                    matches = not matches
            elif actual is None:
                matches = False
            else:
                # Both sides have the same validated scalar type at this point.
                left, right = cast(Decimal, actual), cast(Decimal, expected)
                matches = {
                    "LT": left < right,
                    "LTE": left <= right,
                    "GT": left > right,
                    "GTE": left >= right,
                }[predicate.operator]
        if not matches:
            return False, f"predicate did not match: {predicate.field} {predicate.operator}"
    return True, None
