from decimal import Decimal

import pytest
from pydantic import ValidationError

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


def test_ema_instances_share_one_registered_definition_and_resolve_independently() -> None:
    selected = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(
            ComponentSelection(component_id="ema", component_version="1",
                               instance_id="trend_ema"),
            ComponentSelection(component_id="ema", component_version="1",
                               instance_id="fast_ema",
                               parameters=(ConfigParameter(name="period", value=9),)),
        ),
    )
    resolved = resolve_detection_config(selected)
    by_instance = {item.effective_instance_id: item for item in resolved.components}
    assert by_instance["trend_ema"].parameters[0].value == 45
    assert by_instance["fast_ema"].parameters[0].value == 9
    assert all(item.component_id == "ema" for item in resolved.components)
    assert (
        DetectionAnalysisConfig.from_canonical_json(resolved.canonical_json()).canonical_json()
        == resolved.canonical_json()
    )
    assert detection_config_hash(selected) == detection_config_hash(
        selected.model_copy(update={"components": tuple(reversed(selected.components))})
    )
    changed = selected.model_copy(update={
        "components": (selected.components[0], selected.components[1].model_copy(update={
            "parameters": (ConfigParameter(name="period", value=12),)
        }))
    })
    assert detection_config_hash(selected) != detection_config_hash(changed)


def test_instance_id_cannot_collide_with_legacy_or_another_explicit_instance() -> None:
    with pytest.raises(ValueError, match="component instance"):
        DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="cal",
            components=(
                ComponentSelection(component_id="ema", component_version="1"),
                ComponentSelection(component_id="ema", component_version="1",
                                   instance_id="ema"),
            ),
        )
    with pytest.raises(ValueError, match="component instance"):
        DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="cal",
            components=(
                ComponentSelection(component_id="ema", component_version="1",
                                   instance_id="same"),
                ComponentSelection(component_id="atr", component_version="1",
                                   instance_id="same"),
            ),
        )


def test_custom_component_registry_adds_to_built_in_ema_schema() -> None:
    selected = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(
            ComponentSelection(component_id="ema", component_version="1",
                               instance_id="trend"),
            ComponentSelection(component_id="atr", component_version="1"),
        ),
    )
    resolved = resolve_detection_config(
        selected,
        component_parameters={
            ("atr", "1"): (ParameterSpec("period", ParameterType.INTEGER, 14),)
        },
    )
    assert [item.parameters[0].value for item in resolved.components] == [45, 14]


def test_registered_ema_definition_cannot_be_overridden() -> None:
    selected = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(ComponentSelection(component_id="ema", component_version="1"),),
    )
    with pytest.raises(ConfigurationError, match="cannot override registered"):
        resolve_detection_config(
            selected,
            component_parameters={
                ("ema", "1"): (ParameterSpec("period", ParameterType.INTEGER, 9),)
            },
        )


def test_explicit_type_name_as_instance_normalizes_to_legacy_identity() -> None:
    normalized = ComponentSelection(component_id="ema", component_version="1",
                                    instance_id="ema")
    assert normalized.instance_id is None
    legacy = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(ComponentSelection(component_id="ema", component_version="1"),),
    )
    explicit = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(normalized,),
    )
    assert explicit.components[0].instance_id is None
    assert explicit.canonical_json() == legacy.canonical_json()
    assert detection_config_hash(explicit) == detection_config_hash(legacy)


@pytest.mark.parametrize("instance_id", ["", "fast ema", "../ema", "FastEma"])
def test_explicit_instance_ids_are_machine_safe(instance_id: str) -> None:
    with pytest.raises(ValidationError):
        ComponentSelection(component_id="ema", component_version="1", instance_id=instance_id)
