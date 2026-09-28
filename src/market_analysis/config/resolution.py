from __future__ import annotations

from collections.abc import Mapping

from market_analysis.config.component_registry import (
    BUILTIN_COMPONENT_PARAMETERS,
    validate_component_parameters,
)
from market_analysis.config.models import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    EvaluationPlan,
    OutcomeSelection,
    PatternSelection,
    SegmentSelection,
)
from market_analysis.domain import Timeframe
from market_analysis.patterns import ParameterSpec, PatternDefinition

DefinitionKey = tuple[str, str]


def resolve_detection_config(
    config: DetectionAnalysisConfig,
    *,
    component_parameters: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[DefinitionKey, PatternDefinition] | None = None,
) -> DetectionAnalysisConfig:
    """Expand and type-check all registered defaults before a run is persisted."""
    component_specs = dict(BUILTIN_COMPONENT_PARAMETERS)
    for key, specs in (component_parameters or {}).items():
        if key in component_specs and specs != component_specs[key]:
            raise ConfigurationError(f"cannot override registered component definition: {key}")
        component_specs[key] = specs
    definitions = pattern_definitions or {}
    components = []
    for component_selection in config.components:
        key = (component_selection.component_id, component_selection.component_version)
        if key not in component_specs:
            raise ConfigurationError(f"unregistered component definition: {key}")
        component_values = _resolve_parameters(component_specs[key], component_selection.parameters)
        validate_component_parameters(key, {item.name: item.value for item in component_values})
        components.append(
            ComponentSelection(
                component_id=component_selection.component_id,
                component_version=component_selection.component_version,
                instance_id=component_selection.instance_id,
                enabled=component_selection.enabled,
                parameters=component_values,
            )
        )
    for selection in components:
        if (selection.component_id == "trend_leg_qualification"
                and selection.component_version == "1" and selection.enabled):
            if config.timeframe != Timeframe.M1:
                raise ConfigurationError("TrendLeg qualification v1 requires canonical M1 bars")
            values = {item.name: item.value for item in selection.parameters}
            dependency = next(
                (item for item in components
                 if item.effective_instance_id == values["trend_leg_instance_id"]),
                None,
            )
            if (dependency is None or not dependency.enabled
                    or dependency.component_id != "trend_leg"
                    or dependency.component_version != "1"):
                raise ConfigurationError(
                    "TrendLeg qualification trend_leg_instance_id must bind an enabled trend_leg v1"
                )
        if (selection.component_id != "trend_leg" or selection.component_version != "1"
                or not selection.enabled):
            continue
        values = {item.name: item.value for item in selection.parameters}
        for parameter, component_id in (
            ("ema_instance_id", "ema"),
            ("structure_instance_id", "swing_structure"),
        ):
            dependency = next(
                (item for item in components if item.effective_instance_id == values[parameter]),
                None,
            )
            if (
                dependency is None
                or not dependency.enabled
                or dependency.component_id != component_id
                or dependency.component_version != "1"
            ):
                raise ConfigurationError(
                    f"TrendLeg {parameter} must bind an enabled {component_id} v1"
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


def resolve_evaluation_plan(
    plan: EvaluationPlan,
    *,
    outcome_parameters: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] | None = None,
    segment_parameters: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] | None = None,
) -> EvaluationPlan:
    """Expand registered outcome and segment defaults before hashing a plan."""
    outcome_specs = outcome_parameters or {}
    segment_specs = segment_parameters or {}
    outcomes = []
    for outcome in plan.outcomes:
        key = (outcome.outcome_id, outcome.outcome_version)
        if key not in outcome_specs:
            raise ConfigurationError(f"unregistered outcome definition: {key}")
        outcomes.append(
            OutcomeSelection(
                outcome_id=outcome.outcome_id,
                outcome_version=outcome.outcome_version,
                parameters=_resolve_parameters(outcome_specs[key], outcome.parameters),
            )
        )
    segments = []
    for segment in plan.segments:
        key = (segment.segment_id, segment.segment_version)
        if key not in segment_specs:
            raise ConfigurationError(f"unregistered segment definition: {key}")
        segments.append(
            SegmentSelection(
                segment_id=segment.segment_id,
                segment_version=segment.segment_version,
                parameters=_resolve_parameters(segment_specs[key], segment.parameters),
            )
        )
    return EvaluationPlan(
        schema_version=plan.schema_version,
        detection_config_hash=plan.detection_config_hash,
        context_schema_version=plan.context_schema_version,
        context_fields=plan.context_fields,
        outcomes=tuple(outcomes),
        segments=tuple(segments),
        export_settings=plan.export_settings,
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
