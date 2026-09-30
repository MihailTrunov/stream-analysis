"""Versioned, machine-readable detector transition rationale.

The wire format deliberately uses typed JSON values instead of relying on
JSON numbers, whose Decimal precision and datetime meaning are ambiguous.
Validation returns a detached canonical document suitable for immutable event
storage and deterministic replay comparison.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .definition import PatternDefinition


SCHEMA = "detector-evidence-v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_VALUE_TYPES = frozenset({"decimal", "integer", "boolean", "string", "datetime", "event_ref"})
_STATUSES = frozenset({"PASS", "FAIL", "NOT_APPLICABLE"})
_OPERATORS = frozenset({"<", "<=", "=", "!=", ">=", ">", "CROSSES", "PRESENT", "NONE"})


class EventEvidenceError(ValueError):
    """A detector-evidence-v1 document violates its declared schema."""


def _object(
    value: object, label: str, keys: frozenset[str], required: frozenset[str]
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise EventEvidenceError(f"{label} must be an object with string keys")
    if set(value) - keys or required - set(value):
        raise EventEvidenceError(f"{label} has unknown or missing fields")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventEvidenceError(f"{label} must be non-empty text")
    return value


def _refs(value: object, label: str) -> list[str]:
    if not isinstance(value, list | tuple) or any(
        not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in value
    ):
        raise EventEvidenceError(f"{label} must be semantic SHA-256 references")
    if len(value) != len(set(value)):
        raise EventEvidenceError(f"{label} contains duplicates")
    return list(value)


def _typed(value: object, label: str) -> dict[str, object]:
    source = _object(value, label, frozenset({"type", "value"}), frozenset({"type", "value"}))
    kind = source["type"]
    raw = source["value"]
    if not isinstance(kind, str) or kind not in _VALUE_TYPES:
        raise EventEvidenceError(f"{label} has an unsupported type")
    if kind == "decimal":
        if not isinstance(raw, str):
            raise EventEvidenceError(f"{label} decimal must be a string")
        try:
            number = Decimal(raw)
        except InvalidOperation as exc:
            raise EventEvidenceError(f"{label} decimal is invalid") from exc
        if not number.is_finite() or format(number, "f") != raw:
            raise EventEvidenceError(f"{label} decimal is not canonical")
    elif kind == "integer":
        if type(raw) is not int:
            raise EventEvidenceError(f"{label} integer must be an integer")
    elif kind == "boolean":
        if type(raw) is not bool:
            raise EventEvidenceError(f"{label} boolean must be a boolean")
    elif kind == "event_ref":
        if not isinstance(raw, str) or _DIGEST.fullmatch(raw) is None:
            raise EventEvidenceError(f"{label} event_ref must be a semantic SHA-256 reference")
    elif kind == "datetime":
        if not isinstance(raw, str) or not raw.endswith("Z"):
            raise EventEvidenceError(f"{label} datetime must be canonical UTC")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventEvidenceError(f"{label} datetime is invalid") from exc
        if (
            parsed.utcoffset() != UTC.utcoffset(parsed)
            or parsed.isoformat().replace("+00:00", "Z") != raw
        ):
            raise EventEvidenceError(f"{label} datetime is not canonical UTC")
    else:
        _text(raw, f"{label} string")
    return {"type": kind, "value": raw}


def validate_event_evidence(
    definition: PatternDefinition,
    trigger_id: str,
    rationale: Mapping[str, object],
) -> dict[str, object]:
    """Validate and canonically detach one strict detector-evidence-v1 document."""
    root = _object(
        rationale,
        "event rationale",
        frozenset({"schema", "condition", "items", "source_market_event_refs"}),
        frozenset({"schema", "condition", "items", "source_market_event_refs"}),
    )
    if root["schema"] != SCHEMA:
        raise EventEvidenceError("event rationale schema must be detector-evidence-v1")
    if root["condition"] != trigger_id or trigger_id not in definition.rationale_condition_ids:
        raise EventEvidenceError("event rationale condition must equal a declared trigger")
    raw_items = root["items"]
    if not isinstance(raw_items, list | tuple) or not raw_items:
        raise EventEvidenceError("event rationale requires evidence items")
    source_refs = _refs(root["source_market_event_refs"], "source_market_event_refs")
    items: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_items):
        label = f"evidence item {index}"
        item = _object(
            raw_item,
            label,
            frozenset(
                {
                    "condition_id",
                    "status",
                    "value",
                    "operator",
                    "threshold",
                    "units",
                    "source_refs",
                    "features",
                }
            ),
            frozenset(
                {
                    "condition_id",
                    "status",
                    "value",
                    "operator",
                    "threshold",
                    "units",
                    "source_refs",
                    "features",
                }
            ),
        )
        condition_id = _text(item["condition_id"], f"{label} condition_id")
        if condition_id not in definition.rationale_condition_ids:
            raise EventEvidenceError(f"{label} condition_id is not declared")
        status = item["status"]
        if not isinstance(status, str) or status not in _STATUSES:
            raise EventEvidenceError(f"{label} status is invalid")
        operator = item["operator"]
        if operator is not None and (not isinstance(operator, str) or operator not in _OPERATORS):
            raise EventEvidenceError(f"{label} operator is invalid")
        units = item["units"]
        if units is not None:
            _text(units, f"{label} units")
        refs = _refs(item["source_refs"], f"{label} source_refs")
        if set(refs) - set(source_refs):
            raise EventEvidenceError(f"{label} source_refs are absent from the event")
        features = item["features"]
        if not isinstance(features, Mapping) or any(
            not isinstance(key, str) or not key.strip() for key in features
        ):
            raise EventEvidenceError(f"{label} features must have named keys")
        items.append(
            {
                "condition_id": condition_id,
                "status": status,
                "value": None if item["value"] is None else _typed(item["value"], f"{label} value"),
                "operator": operator,
                "threshold": None
                if item["threshold"] is None
                else _typed(item["threshold"], f"{label} threshold"),
                "units": units,
                "source_refs": refs,
                "features": {
                    key: _typed(value, f"{label} feature {key}")
                    for key, value in sorted(features.items())
                },
            }
        )
    if not any(item["condition_id"] == trigger_id for item in items):
        raise EventEvidenceError("event rationale lacks evidence for its trigger")
    return {
        "schema": SCHEMA,
        "condition": trigger_id,
        "items": items,
        "source_market_event_refs": source_refs,
    }
