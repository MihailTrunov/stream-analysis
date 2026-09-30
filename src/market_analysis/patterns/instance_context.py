"""Version-pinned, typed detector context and cross-run occurrence identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from types import MappingProxyType

from .definition import PatternDefinition

CONTEXT_FORMAT_VERSION = "pattern-context-v1"
INSTANCE_KEY_VERSION = "pattern-instance-v1"
_TYPES = frozenset({"int", "decimal", "bool", "string", "datetime", "event_ref"})
_HEX = frozenset("0123456789abcdef")


class PatternInstanceError(ValueError):
    """A semantic identity, context, or lifecycle checkpoint is invalid."""


def utc_time(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PatternInstanceError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return utc_time(value, "timestamp").isoformat().replace("+00:00", "Z")


def digest_value(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _HEX for c in value):
        raise PatternInstanceError(f"{name} must be a lowercase SHA-256 digest")
    return value


def instance_semantic_key(
    *,
    dataset_revision_id: str,
    detection_config_hash: str,
    instrument_id: str,
    timeframe: str,
    definition: PatternDefinition,
    occurrence_event_time: datetime,
    occurrence_detection_time: datetime,
    occurrence_ordinal: int,
) -> str:
    """Exclude run UUID, persistence UUID, insertion time and build identity."""
    for name, value in (
        ("dataset_revision_id", dataset_revision_id),
        ("instrument_id", instrument_id),
        ("timeframe", timeframe),
    ):
        if not isinstance(value, str) or not value.strip():
            raise PatternInstanceError(f"{name} must be non-empty")
    digest_value(detection_config_hash, "detection_config_hash")
    event_time = utc_time(occurrence_event_time, "occurrence_event_time")
    detection_time = utc_time(occurrence_detection_time, "occurrence_detection_time")
    if event_time > detection_time:
        raise PatternInstanceError("occurrence event_time cannot follow detection_time")
    if type(occurrence_ordinal) is not int or occurrence_ordinal < 0:
        raise PatternInstanceError("occurrence ordinal must be non-negative")
    payload = {
        "dataset_revision_id": dataset_revision_id,
        "detection_config_hash": detection_config_hash,
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "pattern_id": definition.pattern_id,
        "pattern_version": definition.pattern_version,
        "definition_fingerprint": definition.semantic_fingerprint(),
        "occurrence_event_time": utc_text(event_time),
        "occurrence_detection_time": utc_text(detection_time),
        "occurrence_ordinal": occurrence_ordinal,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(f"{INSTANCE_KEY_VERSION}\n{canonical}".encode()).hexdigest()


def _specs(definition: PatternDefinition) -> dict[str, str]:
    specs = {item.field_id: item.type_name for item in definition.context_schema}
    unknown = set(specs.values()) - _TYPES
    if unknown:
        raise PatternInstanceError(f"unsupported detector context types: {sorted(unknown)}")
    return specs


def validate_context(
    definition: PatternDefinition, context: Mapping[str, object]
) -> Mapping[str, object]:
    if not isinstance(context, Mapping):
        raise PatternInstanceError("instance context must be a mapping")
    specs = _specs(definition)
    unknown = set(context) - set(specs)
    if unknown:
        raise PatternInstanceError(f"undeclared instance context fields: {sorted(unknown)}")
    values: dict[str, object] = {}
    for name, value in context.items():
        kind = specs[name]
        if kind == "int":
            if type(value) is not int:
                raise PatternInstanceError(f"{name} must be an integer")
            values[name] = value
        elif kind == "bool":
            if type(value) is not bool:
                raise PatternInstanceError(f"{name} must be a boolean")
            values[name] = value
        elif kind == "decimal":
            if not isinstance(value, Decimal) or not value.is_finite():
                raise PatternInstanceError(f"{name} must be a finite Decimal")
            values[name] = value
        elif kind == "string":
            if not isinstance(value, str) or not value.strip():
                raise PatternInstanceError(f"{name} must be a non-empty string")
            values[name] = value
        elif kind == "datetime":
            values[name] = utc_time(value, name)  # type: ignore[arg-type]
        else:
            values[name] = digest_value(value, name)  # type: ignore[arg-type]
    return MappingProxyType(values)


def encode_context(definition: PatternDefinition, context: Mapping[str, object]) -> str:
    values = validate_context(definition, context)
    specs = _specs(definition)
    fields = {
        name: {
            "type": specs[name],
            "value": (
                utc_text(value) if isinstance(value, datetime)
                else format(value, "f") if isinstance(value, Decimal)
                else value
            ),
        }
        for name, value in values.items()
    }
    payload = {
        "format_version": CONTEXT_FORMAT_VERSION,
        "definition_fingerprint": definition.semantic_fingerprint(),
        "fields": fields,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_context(definition: PatternDefinition, encoded: str) -> Mapping[str, object]:
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise PatternInstanceError("instance context is not JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"format_version", "definition_fingerprint", "fields"}
        or payload["format_version"] != CONTEXT_FORMAT_VERSION
        or payload["definition_fingerprint"] != definition.semantic_fingerprint()
        or not isinstance(payload["fields"], dict)
    ):
        raise PatternInstanceError("instance context schema/version differs")
    specs = _specs(definition)
    values: dict[str, object] = {}
    for name, item in payload["fields"].items():
        if name not in specs or not isinstance(item, dict) or set(item) != {"type", "value"}:
            raise PatternInstanceError("instance context field is undeclared or malformed")
        kind = specs[name]
        if item["type"] != kind:
            raise PatternInstanceError(f"instance context field {name} has wrong type tag")
        value = item["value"]
        if kind == "datetime":
            if not isinstance(value, str) or not value.endswith("Z"):
                raise PatternInstanceError(f"{name} must be a UTC timestamp")
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise PatternInstanceError(f"{name} must be a UTC timestamp") from exc
        elif kind == "decimal":
            if not isinstance(value, str):
                raise PatternInstanceError(f"{name} must be a decimal string")
            try:
                value = Decimal(value)
            except (InvalidOperation, ValueError) as exc:
                raise PatternInstanceError(f"{name} must be a decimal string") from exc
        values[name] = value
    normalized = validate_context(definition, values)
    if encode_context(definition, normalized) != encoded:
        raise PatternInstanceError("instance context JSON is not canonical")
    return normalized
