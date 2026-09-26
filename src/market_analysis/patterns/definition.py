from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType


class PatternDefinitionError(ValueError):
    pass


class ParameterType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    STRING = "string"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    parameter_id: str
    value_type: ParameterType
    default: object
    required: bool = True
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.parameter_id.strip():
            raise PatternDefinitionError("parameter_id must be non-empty")
        lo = _decimal_or_none(self.minimum)
        hi = _decimal_or_none(self.maximum)
        if lo is not None and hi is not None and lo > hi:
            raise PatternDefinitionError(
                "parameter minimum cannot exceed maximum"
            )
        object.__setattr__(self, "minimum", lo)
        object.__setattr__(self, "maximum", hi)
        object.__setattr__(
            self,
            "default",
            self.normalize(self.default),
        )

    def normalize(self, value: object) -> object:
        if self.value_type is ParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be an integer"
                )
            normalized: object = value
        elif self.value_type is ParameterType.DECIMAL:
            if isinstance(value, bool):
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be a decimal"
                )
            try:
                normalized = (
                    value
                    if isinstance(value, Decimal)
                    else Decimal(str(value))
                )
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be a decimal"
                ) from exc
            if not normalized.is_finite():
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be finite"
                )
        elif self.value_type is ParameterType.BOOLEAN:
            if not isinstance(value, bool):
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be a boolean"
                )
            normalized = value
        else:
            if not isinstance(value, str) or not value.strip():
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be a non-empty string"
                )
            normalized = value

        if isinstance(normalized, int | Decimal) and not isinstance(
            normalized,
            bool,
        ):
            number = Decimal(normalized)
            if self.minimum is not None and number < self.minimum:
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be >= {self.minimum}"
                )
            if self.maximum is not None and number > self.maximum:
                raise PatternDefinitionError(
                    f"{self.parameter_id} must be <= {self.maximum}"
                )
        return normalized

    def canonical(
        self,
        *,
        include_default: bool,
        include_description: bool,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "parameter_id": self.parameter_id,
            "value_type": self.value_type.value,
            "required": self.required,
            "minimum": _json_value(self.minimum),
            "maximum": _json_value(self.maximum),
        }
        if include_default:
            result["default"] = _json_value(self.default)
        if include_description:
            result["description"] = self.description
        return result


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    from_state: str
    to_state: str
    trigger_id: str


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    group_id: str
    condition_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        values = tuple(self.condition_ids)
        invalid = (
            not self.group_id.strip()
            or not values
            or any(not value.strip() for value in values)
        )
        if invalid:
            raise PatternDefinitionError(
                "condition groups require non-empty ids"
            )
        _unique("condition ids", values)
        object.__setattr__(self, "condition_ids", values)


@dataclass(frozen=True, slots=True)
class ContextFieldSpec:
    field_id: str
    type_name: str

    def __post_init__(self) -> None:
        if not self.field_id.strip() or not self.type_name.strip():
            raise PatternDefinitionError(
                "context field id/type must be non-empty"
            )


@dataclass(frozen=True, slots=True)
class PatternDefinition:
    pattern_id: str
    pattern_version: str
    name: str
    description: str
    required_components: tuple[str, ...]
    required_market_events: tuple[str, ...]
    parameters: tuple[ParameterSpec, ...]
    lifecycle_states: tuple[str, ...]
    transitions: tuple[TransitionSpec, ...]
    condition_groups: tuple[ConditionGroup, ...]
    simultaneous_precedence: tuple[str, ...]
    context_schema: tuple[ContextFieldSpec, ...]
    rationale_condition_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        identity_fields = (
            "pattern_id",
            "pattern_version",
            "name",
            "description",
        )
        if any(
            not getattr(self, field_name).strip()
            for field_name in identity_fields
        ):
            raise PatternDefinitionError(
                "pattern identity/name/description must be non-empty"
            )

        tuple_fields = (
            "required_components",
            "required_market_events",
            "parameters",
            "lifecycle_states",
            "transitions",
            "condition_groups",
            "simultaneous_precedence",
            "context_schema",
            "rationale_condition_ids",
        )
        for field_name in tuple_fields:
            object.__setattr__(
                self,
                field_name,
                tuple(getattr(self, field_name)),
            )

        _unique("required components", self.required_components)
        _unique("required market events", self.required_market_events)
        _unique(
            "parameter ids",
            (value.parameter_id for value in self.parameters),
        )
        _unique("lifecycle states", self.lifecycle_states)
        _unique(
            "condition group ids",
            (value.group_id for value in self.condition_groups),
        )
        _unique(
            "context field ids",
            (value.field_id for value in self.context_schema),
        )
        _unique("rationale ids", self.rationale_condition_ids)
        _unique("precedence ids", self.simultaneous_precedence)

        invalid_states = (
            not self.lifecycle_states
            or any(not state.strip() for state in self.lifecycle_states)
        )
        if invalid_states:
            raise PatternDefinitionError(
                "lifecycle states must be non-empty"
            )

        states = set(self.lifecycle_states)
        conditions = {
            condition_id
            for group in self.condition_groups
            for condition_id in group.condition_ids
        }
        for transition in self.transitions:
            if (
                transition.from_state not in states
                or transition.to_state not in states
            ):
                raise PatternDefinitionError(
                    "transition references unknown lifecycle state"
                )
            if transition.trigger_id not in conditions:
                raise PatternDefinitionError(
                    "transition triggers must be declared conditions"
                )
        if set(self.rationale_condition_ids) - conditions:
            raise PatternDefinitionError(
                "rationale ids must be declared conditions"
            )
        if set(self.simultaneous_precedence) - conditions:
            raise PatternDefinitionError(
                "precedence ids must be declared conditions"
            )

    @property
    def identity(self) -> tuple[str, str]:
        return self.pattern_id, self.pattern_version

    def resolve_parameters(
        self,
        overrides: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        supplied = {} if overrides is None else dict(overrides)
        specs = {
            value.parameter_id: value
            for value in self.parameters
        }
        unknown = set(supplied) - set(specs)
        if unknown:
            raise PatternDefinitionError(
                f"unknown parameters: {sorted(unknown)}"
            )
        resolved = {
            key: spec.normalize(supplied.get(key, spec.default))
            for key, spec in specs.items()
        }
        return MappingProxyType(resolved)

    def canonical_dict(
        self,
        *,
        semantic_only: bool = False,
    ) -> dict[str, object]:
        parameters = sorted(
            self.parameters,
            key=lambda item: item.parameter_id,
        )
        result: dict[str, object] = {
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "required_components": sorted(self.required_components),
            "required_market_events": sorted(self.required_market_events),
            "parameters": [
                value.canonical(
                    include_default=not semantic_only,
                    include_description=not semantic_only,
                )
                for value in parameters
            ],
            "lifecycle_states": list(self.lifecycle_states),
            "transitions": [
                vars_like(value)
                for value in self.transitions
            ],
            "condition_groups": [
                {
                    "group_id": value.group_id,
                    "condition_ids": list(value.condition_ids),
                }
                for value in self.condition_groups
            ],
            "simultaneous_precedence": list(
                self.simultaneous_precedence
            ),
            "context_schema": [
                vars_like(value)
                for value in self.context_schema
            ],
            "rationale_condition_ids": list(
                self.rationale_condition_ids
            ),
        }
        if not semantic_only:
            result.update(
                name=self.name,
                description=self.description,
            )
        return result

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def semantic_fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical_dict(semantic_only=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class PatternDefinitionRegistry:
    def __init__(self) -> None:
        self._items: dict[
            tuple[str, str],
            PatternDefinition,
        ] = {}

    def register(self, definition: PatternDefinition) -> None:
        existing = self._items.get(definition.identity)
        if (
            existing is not None
            and existing.canonical_json() != definition.canonical_json()
        ):
            raise PatternDefinitionError(
                "pattern id/version cannot map to two different definitions"
            )
        self._items[definition.identity] = definition

    def definitions(self) -> tuple[PatternDefinition, ...]:
        return tuple(
            self._items[key]
            for key in sorted(self._items)
        )

    def get(
        self,
        pattern_id: str,
        pattern_version: str,
    ) -> PatternDefinition:
        try:
            return self._items[(pattern_id, pattern_version)]
        except KeyError as exc:
            raise PatternDefinitionError(
                f"unknown pattern: {pattern_id}@{pattern_version}"
            ) from exc


def assert_semantic_change_is_versioned(
    previous: PatternDefinition,
    candidate: PatternDefinition,
) -> None:
    if previous.pattern_id != candidate.pattern_id:
        raise PatternDefinitionError(
            "cannot compare different pattern ids"
        )
    same_version = previous.pattern_version == candidate.pattern_version
    semantics_changed = (
        previous.semantic_fingerprint()
        != candidate.semantic_fingerprint()
    )
    if same_version and semantics_changed:
        raise PatternDefinitionError(
            "material pattern semantics changed "
            "without a pattern_version change"
        )


def _unique(
    label: str,
    values: Iterable[object],
) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise PatternDefinitionError(
            f"{label} must be unique"
        )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PatternDefinitionError(
            "numeric bound cannot be boolean"
        )
    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PatternDefinitionError(
            "numeric bound must be decimal"
        ) from exc
    if not result.is_finite():
        raise PatternDefinitionError(
            "numeric bound must be finite"
        )
    return result


def _json_value(value: object) -> object:
    return format(value, "f") if isinstance(value, Decimal) else value


def vars_like(value: object) -> dict[str, object]:
    return {
        name: getattr(value, name)
        for name in value.__slots__  # type: ignore[attr-defined]
    }
