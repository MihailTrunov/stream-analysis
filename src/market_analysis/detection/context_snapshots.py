"""Versioned, immutable X-side observations; never accepts outcomes or UI state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from market_analysis.indicators import (
    EmaCrossSegment,
    EmaCrossTransition,
    EndedQualification,
    MarketStateFrame,
    QualificationEarned,
    QualificationEvidence,
    QualificationThresholds,
    StructureBreak,
    StructureLineage,
    SwingClassification,
    SwingPoint,
    SwingPointReference,
    TrendLeg,
    TrendLegTransition,
    market_event_semantic_ref,
)
from market_analysis.patterns import PatternDefinition
from market_analysis.patterns.instance_context import encode_context, utc_text, utc_time

from .records import DetectorEvent, PatternInstance, freeze_mapping, json_value

CONTEXT_SCHEMA_VERSION = "detection-time-context-v1"

# Inclusion rules are part of v1. In particular, growing historical event lists
# are excluded: current state and current-bar evidence are sufficient and bounded.
COMPONENT_FIELDS = {
    "session": "calendar_id calendar_version timezone_name local_timestamp trading_date "
    "session_name elapsed_seconds is_break is_holiday is_open",
    "ema": "component_id instance_id period alpha seed_method ema value_ready",
    "atr": "component_id instance_id period smoothing seed_method true_range atr value_ready",
    "swing_point": "component_id instance_id definition_id atr_instance_id atr_period "
    "reversal_atr_multiplier extreme_source confirmation_source freeze_atr_at_extreme "
    "allow_same_bar_confirmation equal_extreme_policy require_alternation search_active "
    "search_direction provisional_candidate_high provisional_candidate_low "
    "opposite_running_high opposite_running_low confirmed_count",
    "swing_structure": "definition_id lineage equal_level_atr_multiplier equality_atr_anchor "
    "break_buffer_atr_multiplier break_atr_anchor break_confirmation_source "
    "allow_break_on_reference_detection_bar overall_structure latest_high previous_high "
    "latest_low previous_low high_broken low_broken active_high_break_threshold "
    "active_low_break_threshold last_structure_update_detection_time "
    "current_bar_classifications current_bar_breaks",
    "trend_leg": "component_id instance_id definition_id lineage parameters active_leg "
    "pending_corrective active_ema_segment previous_close previous_ema "
    "current_bar_transitions current_bar_ema_crosses",
    "trend_leg_qualification": "component_id instance_id definition_id lineage parameters "
    "status active_qualification current_bar_qualifications current_bar_ended",
    "range_state": "definition_id component_id instance_id definition_version calculation_time "
    "detection_time parameters lineage chop bandwidth_evidence compression choppiness_score "
    "choppiness_regime bandwidth bandwidth_percentile compression_score compression_regime",
}
_VALUE_FIELDS = frozenset({
    "bar", "completed_bars", "components", "availability", "optional_components",
    "market_events", "detector",
})
_LINEAGE_FIELDS = frozenset({
    "dataset_revision_id", "timeframe", "detection_config_hash", "calendar_version",
    "config_schema_version", "build_id", "code_revision", "code_dirty", "code_capture_status",
    "component_versions", "pattern_id", "pattern_version", "definition_fingerprint",
})
_DETECTOR_FIELDS = frozenset({
    "pattern_id", "pattern_version", "from_state", "state", "trigger_id", "sequence",
    "context_status", "context", "rationale",
})
_EVIDENCE_TYPES = (
    SwingPoint, SwingPointReference, StructureLineage, SwingClassification, StructureBreak,
    TrendLeg, TrendLegTransition, EmaCrossSegment, EmaCrossTransition,
    QualificationThresholds, QualificationEarned, QualificationEvidence, EndedQualification,
)


class ContextSnapshotError(ValueError):
    """Context is unsupported, inconsistent, or includes a future observation."""


def canonical_json(value: object) -> str:
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _observable(value: object, cutoff: datetime, *, lineage: bool = False) -> object:
    """Detach only typed causal values; execution UUIDs never enter analytical X."""
    if isinstance(value, datetime):
        if utc_time(value, "observation time") > cutoff:
            raise ContextSnapshotError("future observation cannot enter detection-time context")
        return utc_text(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ContextSnapshotError("context decimals must be finite")
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        if not isinstance(value, _EVIDENCE_TYPES):
            raise ContextSnapshotError(f"unsupported context value: {type(value).__name__}")
        data = {field.name: getattr(value, field.name) for field in fields(value)}
        for name in ("event_time", "detection_time"):
            if hasattr(value, name):
                data[name] = getattr(value, name)
        return _observable(data, cutoff, lineage=lineage)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContextSnapshotError("context keys must be strings")
            if lineage and key == "run_id":
                continue
            if key.lower() in {"outcome", "outcomes", "outcome_observations", "future_label"}:
                raise ContextSnapshotError("outcome-side information cannot enter context")
            result[key] = _observable(item, cutoff, lineage=key == "lineage")
        return result
    if isinstance(value, tuple | list):
        return tuple(_observable(item, cutoff) for item in value)
    if isinstance(value, str):
        value = str(value)  # StrEnum values must round-trip as ordinary JSON strings.
    if isinstance(value, str) and "T" in value:
        # Typed evidence represents datetime values as ISO text rather than objects.
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return _observable(timestamp, cutoff)
    if value is None or isinstance(value, str | bool | int):
        return value
    raise ContextSnapshotError(f"unsupported context value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class DetectionTimeContextSnapshot:
    snapshot_id: str
    event_id: str
    run_id: str
    source_event_semantic_ref: str
    instance_semantic_key: str
    instrument_id: str
    detection_time: datetime
    available_at: datetime
    within_bar_ordinal: int
    context_schema_version: str
    lineage: Mapping[str, object]
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.context_schema_version != CONTEXT_SCHEMA_VERSION:
            raise ContextSnapshotError("unsupported context_schema_version")
        if set(self.values) != _VALUE_FIELDS:
            raise ContextSnapshotError("unsupported context value fields")
        if set(self.lineage) != _LINEAGE_FIELDS:
            raise ContextSnapshotError("unsupported context lineage fields")
        for name in _LINEAGE_FIELDS - {"component_versions", "code_revision", "code_dirty"}:
            if not isinstance(self.lineage[name], str) or not self.lineage[name]:
                raise ContextSnapshotError(f"lineage {name} must be non-empty text")
        versions = self.lineage["component_versions"]
        if not isinstance(versions, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value
            for key, value in versions.items()
        ):
            raise ContextSnapshotError("component versions must be a mapping of version strings")
        phase = self.values["detector"]
        if not isinstance(phase, Mapping) or set(phase) != _DETECTOR_FIELDS:
            raise ContextSnapshotError("unsupported detector phase fields")
        if phase["context_status"] not in {"AVAILABLE", "UNAVAILABLE"} or (
            (phase["context_status"] == "AVAILABLE") != isinstance(phase["context"], Mapping)
        ):
            raise ContextSnapshotError("detector context availability is inconsistent")
        optional = self.values["optional_components"]
        if not isinstance(optional, Mapping) or set(optional) != set(COMPONENT_FIELDS) or any(
            status not in ("PRESENT", "NOT_CONFIGURED") for status in optional.values()
        ):
            raise ContextSnapshotError("optional component statuses are invalid")
        for name in ("detection_time", "available_at"):
            object.__setattr__(self, name, utc_time(getattr(self, name), name))
        if self.available_at <= self.detection_time:
            raise ContextSnapshotError("completed candle must become available after its label")
        if type(self.within_bar_ordinal) is not int or self.within_bar_ordinal < 0:
            raise ContextSnapshotError("within_bar_ordinal must be non-negative")
        for name in ("snapshot_id", "event_id", "run_id", "instrument_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ContextSnapshotError(f"{name} must be non-empty")
        for name in ("source_event_semantic_ref", "instance_semantic_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ContextSnapshotError(f"{name} must be a SHA-256 digest")
        object.__setattr__(self, "lineage", freeze_mapping(self.lineage))
        values = _observable(self.values, self.detection_time)
        assert isinstance(values, Mapping)
        object.__setattr__(self, "values", freeze_mapping(values))

    def to_dict(self) -> dict[str, object]:
        return {field.name: json_value(getattr(self, field.name)) for field in fields(self)}

    @property
    def content_hash(self) -> str:
        return sha256(canonical_json(self.to_dict()).encode()).hexdigest()

    @property
    def semantic_hash(self) -> str:
        payload = self.to_dict()
        for name in ("snapshot_id", "event_id", "run_id"):
            del payload[name]
        return sha256(canonical_json(payload).encode()).hexdigest()


def capture_values(
    frame: MarketStateFrame,
    event: DetectorEvent,
    instance: PatternInstance | None,
    definition: PatternDefinition,
) -> Mapping[str, object]:
    """Capture the actual emitting frame, never a later occurrence projection.

    Legacy/custom multi-transition detectors that do not supply intermediate
    contexts explicitly report unavailable context rather than borrowing final state.
    """
    if (
        not frame.bar.is_complete
        or event.detection_time != frame.bar.timestamp
        or event.event_time > event.detection_time
        or event.run_id != frame.run_id
        or event.dataset_revision_id != frame.dataset_revision_id
        or event.detection_config_hash != frame.detection_config_hash
        or event.instrument_id != frame.bar.instrument_id
        or event.timeframe != frame.bar.timeframe.value
        or (event.pattern_id, event.pattern_version) != definition.identity
    ):
        raise ContextSnapshotError("source event does not belong to the emitting frame")
    context: object = None
    if instance is not None:
        if instance.identity != (event.pattern_id, event.pattern_version, event.instance_id) or (
            instance.state != event.to_state
        ):
            raise ContextSnapshotError("detector phase does not match emitted transition")
        encode_context(definition, instance.context)
        context = instance.context
    components: dict[str, object] = {}
    families = set()
    for key, state in frame.components.items():
        family = "session" if key == "session" else str(state.get("component_id", ""))
        # SwingStructure state predates component_id in its observable projection.
        if not family and state.get("definition_id") == "SWING_STRUCTURE_V1":
            family = "swing_structure"
        if family not in COMPONENT_FIELDS:
            raise ContextSnapshotError(f"unsupported context component: {family or key}")
        if family != "session" and frame.component_versions.get(key) != "1":
            raise ContextSnapshotError("unsupported context component version")
        families.add(family)
        components[key] = {
            name: state[name] for name in COMPONENT_FIELDS[family].split() if name in state
        }
    market_events = tuple({
        "semantic_ref": market_event_semantic_ref(frame, item),
        "event_type": item.event_type.value,
        "event_time": item.event_time,
        "detection_time": item.detection_time,
        "ordinal": item.ordinal,
        "source_instance_id": item.source_instance_id,
        "evidence": item.evidence,
    } for item in frame.market_events_this_bar)
    values = {
        "bar": dict(frame.bar.to_canonical_dict()),
        "completed_bars": frame.completed_bars,
        "components": components,
        "availability": frame.availability,
        "optional_components": {
            family: "PRESENT" if family in families else "NOT_CONFIGURED"
            for family in COMPONENT_FIELDS
        },
        "market_events": market_events,
        "detector": {
            "pattern_id": event.pattern_id, "pattern_version": event.pattern_version,
            "from_state": event.from_state, "state": event.to_state,
            "trigger_id": event.trigger_id, "sequence": event.sequence,
            "context_status": "AVAILABLE" if instance is not None else "UNAVAILABLE",
            "context": context, "rationale": event.rationale,
        },
    }
    result = _observable(values, utc_time(event.detection_time, "detection_time"))
    assert isinstance(result, Mapping)
    return freeze_mapping(result)
