from __future__ import annotations

from collections.abc import Mapping

from market_analysis.config.models import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    PatternSelection,
)
from market_analysis.patterns import ParameterSpec, PatternDefinition

DefinitionKey = tuple[str, str]


def resolve_detection_config(
    config: DetectionAnalysisConfig,
    *,
    component_parameters: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[DefinitionKey, PatternDefinition] | None = None,
) -> DetectionAnalysisConfig:
    """Expand and type-check all registered defaults before a run is persisted."""
    component_specs = component_parameters or {}
    definitions = pattern_definitions or {}
    components = []
    for component_selection in config.components:
        key = (component_selection.component_id, component_selection.component_version)
        if key not in component_specs:
            raise ConfigurationError(f"unregistered component definition: {key}")
        component_values = _resolve_parameters(component_specs[key], component_selection.parameters)
        components.append(
            ComponentSelection(
                component_id=component_selection.component_id,
                component_version=component_selection.component_version,
                enabled=component_selection.enabled,
                parameters=component_values,
            )
        )
    patterns = []
    for pattern_selection in config.patterns:
        key = (pattern_selection.pattern_id, pattern_selection.pattern_version)
        definition = definitions.get(key)
        if definition is None or definition.identity != key:
            raise ConfigurationError(f"unregistered pattern definition: {key}")
        pattern_values = definition.resolve_parameters(
            {parameter.name: parameter.value for parameter in pattern_selection.parameters}
        )
        patterns.append(
            PatternSelection(
                pattern_id=pattern_selection.pattern_id,
                pattern_version=pattern_selection.pattern_version,
                enabled=pattern_selection.enabled,
                parameters=tuple(
                    ConfigParameter.model_validate({"name": name, "value": value})
                    for name, value in sorted(pattern_values.items())
                ),
            )
        )
    return DetectionAnalysisConfig(
        schema_version=config.schema_version,
        instrument_id=config.instrument_id,
        timeframe=config.timeframe,
        calendar_id=config.calendar_id,
        components=tuple(components),
        patterns=tuple(patterns),
    )


def _resolve_parameters(
    specs: tuple[ParameterSpec, ...], parameters: tuple[ConfigParameter, ...]
) -> tuple[ConfigParameter, ...]:
    spec_by_name = {spec.parameter_id: spec for spec in specs}
    if len(spec_by_name) != len(specs):
        raise ConfigurationError("component parameter definitions must be unique")
    supplied = {parameter.name: parameter.value for parameter in parameters}
    unknown = set(supplied) - set(spec_by_name)
    if unknown:
        raise ConfigurationError(f"unknown component parameters: {sorted(unknown)}")
    return tuple(
        ConfigParameter.model_validate(
            {"name": name, "value": spec.normalize(supplied.get(name, spec.default))}
        )
        for name, spec in sorted(spec_by_name.items())
    )
