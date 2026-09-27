from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from market_analysis.domain import Timeframe
from market_analysis.patterns import PatternDefinition


class ConfigurationError(ValueError):
    pass


class StalePresetRevisionError(ConfigurationError):
    pass


Identifier = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
InstanceIdentifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
ConfigValue = str | bool | int | Decimal | datetime | None


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConfigParameter(ImmutableModel):
    name: Identifier
    value: ConfigValue

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    "configuration timestamps must be timezone-aware"
                )
            return value.astimezone(UTC)
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("configuration decimal must be finite")
            return value
        if isinstance(value, str | bool | int):
            return value
        if isinstance(value, float):
            try:
                result = Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError(
                    "configuration decimal must be finite"
                ) from exc
            if not result.is_finite():
                raise ValueError("configuration decimal must be finite")
            return result
        raise ValueError(
            f"unsupported configuration value type: {type(value).__name__}"
        )


class ComponentSelection(ImmutableModel):
    component_id: Identifier
    component_version: Identifier
    instance_id: InstanceIdentifier | None = None
    enabled: bool = True
    parameters: tuple[ConfigParameter, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_instance_id(cls, value: Any) -> Any:
        if (
            isinstance(value, Mapping)
            and value.get("instance_id") == value.get("component_id")
        ):
            return {**value, "instance_id": None}
        return value

    @property
    def effective_instance_id(self) -> str:
        """Legacy selections use their component type as the instance ID."""
        return self.component_id if self.instance_id is None else self.instance_id

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        _ensure_unique(
            "component parameter",
            (item.name for item in self.parameters),
        )
        return self


class PatternSelection(ImmutableModel):
    pattern_id: Identifier
    pattern_version: Identifier
    enabled: bool = True
    parameters: tuple[ConfigParameter, ...] = ()

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        _ensure_unique(
            "pattern parameter",
            (item.name for item in self.parameters),
        )
        return self

    @classmethod
    def from_definition(
        cls,
        definition: PatternDefinition,
        overrides: Mapping[str, object] | None = None,
    ) -> PatternSelection:
        resolved = definition.resolve_parameters(overrides)
        return cls(
            pattern_id=definition.pattern_id,
            pattern_version=definition.pattern_version,
            parameters=tuple(
                ConfigParameter(name=name, value=_config_value(value))
                for name, value in sorted(resolved.items())
            ),
        )


class DetectionAnalysisConfig(ImmutableModel):
    schema_version: Identifier = "detection-config-v1"
    instrument_id: Identifier
    timeframe: Timeframe = Timeframe.M1
    calendar_id: Identifier
    components: tuple[ComponentSelection, ...] = ()
    patterns: tuple[PatternSelection, ...] = ()

    @model_validator(mode="after")
    def unique_selections(self) -> Self:
        _ensure_unique(
            "component instance",
            (item.effective_instance_id for item in self.components),
        )
        _ensure_unique(
            "pattern",
            (item.pattern_id for item in self.patterns),
        )
        return self

    def canonical_dict(self) -> dict[str, object]:
        components = sorted(
            self.components,
            key=lambda value: value.effective_instance_id,
        )
        patterns = sorted(
            self.patterns,
            key=lambda value: value.pattern_id,
        )
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe.value,
            "calendar_id": self.calendar_id,
            "components": [_selection_payload(item) for item in components],
            "patterns": [_selection_payload(item) for item in patterns],
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_dict())

    @classmethod
    def from_canonical_json(cls, source: str) -> DetectionAnalysisConfig:
        """Reconstruct typed resolved values without losing canonical identity."""
        try:
            payload = json.loads(source)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("invalid canonical detection JSON") from exc
        if not isinstance(payload, dict):
            raise ConfigurationError("canonical detection JSON must be an object")
        for group in ("components", "patterns"):
            selections = payload.get(group)
            if not isinstance(selections, list):
                raise ConfigurationError(f"canonical {group} must be a list")
            for selection in selections:
                if not isinstance(selection, dict) or not isinstance(
                    selection.get("parameters"), list
                ):
                    raise ConfigurationError(f"canonical {group} selection is invalid")
                selection["parameters"] = [
                    _parse_canonical_parameter(parameter)
                    for parameter in selection["parameters"]
                ]
        result = cls.model_validate(payload)
        if result.canonical_json() != source:
            raise ConfigurationError("canonical detection JSON changed on reload")
        return result


class OutcomeSelection(ImmutableModel):
    outcome_id: Identifier
    outcome_version: Identifier
    parameters: tuple[ConfigParameter, ...] = ()

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        _ensure_unique(
            "outcome parameter",
            (item.name for item in self.parameters),
        )
        return self


class SegmentSelection(ImmutableModel):
    segment_id: Identifier
    segment_version: Identifier
    parameters: tuple[ConfigParameter, ...] = ()

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        _ensure_unique(
            "segment parameter",
            (item.name for item in self.parameters),
        )
        return self


class EvaluationPlan(ImmutableModel):
    schema_version: Identifier = "evaluation-plan-v1"
    detection_config_hash: Identifier
    context_schema_version: Identifier
    context_fields: tuple[Identifier, ...] = ()
    outcomes: tuple[OutcomeSelection, ...] = ()
    segments: tuple[SegmentSelection, ...] = ()
    export_settings: tuple[ConfigParameter, ...] = ()

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        _ensure_unique("context field", self.context_fields)
        _ensure_unique(
            "outcome",
            (
                (item.outcome_id, item.outcome_version)
                for item in self.outcomes
            ),
        )
        _ensure_unique(
            "segment",
            (
                (item.segment_id, item.segment_version)
                for item in self.segments
            ),
        )
        _ensure_unique(
            "export setting",
            (item.name for item in self.export_settings),
        )
        return self

    def canonical_dict(self) -> dict[str, object]:
        outcomes = sorted(
            self.outcomes,
            key=lambda value: (
                value.outcome_id,
                value.outcome_version,
            ),
        )
        segments = sorted(
            self.segments,
            key=lambda value: (
                value.segment_id,
                value.segment_version,
            ),
        )
        export_settings = sorted(
            self.export_settings,
            key=lambda value: value.name,
        )
        return {
            "schema_version": self.schema_version,
            "detection_config_hash": self.detection_config_hash,
            "context_schema_version": self.context_schema_version,
            "context_fields": sorted(self.context_fields),
            "outcomes": [
                _named_selection_payload(item, "outcome")
                for item in outcomes
            ],
            "segments": [
                _named_selection_payload(item, "segment")
                for item in segments
            ],
            "export_settings": [
                _parameter_payload(item)
                for item in export_settings
            ],
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_dict())


class ConfigurationPresetRevision(ImmutableModel):
    preset_id: Identifier
    revision: int = Field(ge=1)
    instrument_id: Identifier
    detection_config: DetectionAnalysisConfig

    @model_validator(mode="after")
    def instrument_matches(self) -> Self:
        if self.instrument_id != self.detection_config.instrument_id:
            raise ValueError("preset instrument must match detection config")
        return self

    def next_revision(
        self,
        *,
        edited_revision: int,
        detection_config: DetectionAnalysisConfig,
    ) -> ConfigurationPresetRevision:
        if edited_revision != self.revision:
            raise StalePresetRevisionError(
                "stale preset revision: "
                f"edited {edited_revision}, current {self.revision}"
            )
        if detection_config.instrument_id != self.instrument_id:
            raise ConfigurationError(
                "cannot change preset instrument across revisions"
            )
        return ConfigurationPresetRevision(
            preset_id=self.preset_id,
            revision=self.revision + 1,
            instrument_id=self.instrument_id,
            detection_config=detection_config,
        )


def _config_value(value: object) -> ConfigValue:
    if value is None or isinstance(value, str | bool | int | Decimal | datetime):
        return value
    raise ConfigurationError(
        f"unsupported resolved configuration value: {type(value).__name__}"
    )


def _parse_canonical_parameter(parameter: object) -> dict[str, object]:
    if not isinstance(parameter, dict) or set(parameter) != {"name", "value", "value_type"}:
        raise ConfigurationError("canonical parameter must have name, value and value_type")
    value = parameter["value"]
    value_type = parameter["value_type"]
    if value_type == "boolean" and isinstance(value, bool):
        parsed: object = value
    elif value_type == "null" and value is None:
        parsed = None
    elif value_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif value_type == "decimal" and isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ConfigurationError("invalid canonical decimal") from exc
    elif value_type == "timestamp" and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigurationError("invalid canonical timestamp") from exc
    elif value_type == "string" and isinstance(value, str):
        parsed = value
    else:
        raise ConfigurationError(f"canonical parameter type mismatch: {value_type!r}")
    return {"name": parameter["name"], "value": parsed}


def _selection_payload(
    value: ComponentSelection | PatternSelection,
) -> dict[str, object]:
    if isinstance(value, ComponentSelection):
        identity_key = "component_version"
        id_key = "component_id"
    else:
        identity_key = "pattern_version"
        id_key = "pattern_id"
    parameters = sorted(
        value.parameters,
        key=lambda item: item.name,
    )
    payload = {
        id_key: getattr(value, id_key),
        identity_key: getattr(value, identity_key),
        "enabled": value.enabled,
        "parameters": [_parameter_payload(item) for item in parameters],
    }
    if isinstance(value, ComponentSelection) and value.instance_id is not None:
        payload["instance_id"] = value.instance_id
    return payload


def _named_selection_payload(
    value: OutcomeSelection | SegmentSelection,
    prefix: str,
) -> dict[str, object]:
    parameters = sorted(
        value.parameters,
        key=lambda item: item.name,
    )
    return {
        f"{prefix}_id": getattr(value, f"{prefix}_id"),
        f"{prefix}_version": getattr(value, f"{prefix}_version"),
        "parameters": [_parameter_payload(item) for item in parameters],
    }


def _parameter_payload(value: ConfigParameter) -> dict[str, object]:
    parameter_value = value.value
    if isinstance(parameter_value, bool):
        value_type = "boolean"
    elif parameter_value is None:
        value_type = "null"
    elif isinstance(parameter_value, int):
        value_type = "integer"
    elif isinstance(parameter_value, Decimal):
        value_type = "decimal"
    elif isinstance(parameter_value, datetime):
        value_type = "timestamp"
    else:
        value_type = "string"
    return {
        "name": value.name,
        "value_type": value_type,
        "value": _canonical_value(parameter_value),
    }


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(Decimal(0) if value == 0 else value.normalize(), "f")
    return value


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _ensure_unique(label: str, values: Any) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{label} values must be unique")
