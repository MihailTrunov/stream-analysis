from decimal import Decimal

import pytest

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    PatternSelection,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.patterns import (
    ConditionGroup,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    TransitionSpec,
)


def _definition() -> PatternDefinition:
    return PatternDefinition(
        "compression", "1", "Compression", "Demo", (), (),
        (ParameterSpec("bars", ParameterType.INTEGER, 3),),
        ("idle", "active"),
        (TransitionSpec("idle", "active", "entry"),),
        (ConditionGroup("formation", ("entry",)),),
        ("entry",), (), ("entry",),
    )


def test_omitted_and_explicit_defaults_have_identical_resolved_hash() -> None:
    definition = _definition()
    specs = {("atr", "1"): (ParameterSpec("period", ParameterType.INTEGER, 14),)}
    patterns = {definition.identity: definition}

    def config(explicit: bool) -> DetectionAnalysisConfig:
        return DetectionAnalysisConfig(
            instrument_id="US30",
            calendar_id="demo-v1",
            components=(
                ComponentSelection(
                    component_id="atr",
                    component_version="1",
                    parameters=(ConfigParameter(name="period", value=14),) if explicit else (),
                ),
            ),
            patterns=(
                PatternSelection(
                    pattern_id="compression",
                    pattern_version="1",
                    parameters=(ConfigParameter(name="bars", value=3),) if explicit else (),
                ),
            ),
        )

    resolved_implicit = resolve_detection_config(
        config(False), component_parameters=specs, pattern_definitions=patterns
    )
    resolved_explicit = resolve_detection_config(
        config(True), component_parameters=specs, pattern_definitions=patterns
    )
    assert resolved_implicit == resolved_explicit
    assert detection_config_hash(
        config(False), component_parameters=specs, pattern_definitions=patterns
    ) == detection_config_hash(
        config(True), component_parameters=specs, pattern_definitions=patterns
    )
    assert resolved_implicit.patterns[0].parameters[0].value == 3


def test_unregistered_or_invalid_component_is_rejected() -> None:
    config = DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="demo-v1",
        components=(ComponentSelection(component_id="atr", component_version="1"),),
    )
    with pytest.raises(ConfigurationError, match="unregistered component"):
        resolve_detection_config(config)
    invalid = config.model_copy(
        update={"components": (
            ComponentSelection(
                component_id="atr",
                component_version="1",
                parameters=(ConfigParameter(name="period", value=Decimal("1.5")),),
            ),
        )}
    )
    with pytest.raises(ValueError, match="integer"):
        resolve_detection_config(
            invalid,
            component_parameters={
                ("atr", "1"): (ParameterSpec("period", ParameterType.INTEGER, 14),)
            },
        )
