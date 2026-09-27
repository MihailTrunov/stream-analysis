from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    DetectionAnalysisConfig,
    EvaluationPlan,
    OutcomeSelection,
    PatternSelection,
    SegmentSelection,
    detection_config_hash,
    evaluation_plan_hash,
    resolve_detection_config,
)
from market_analysis.patterns import ParameterSpec, ParameterType

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "config_hashes"


def test_detection_hash_is_stable_and_evaluation_changes_only_plan_hash() -> None:
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    detection_hash = detection_config_hash(config)
    assert detection_hash == "c6660cf41f67b16e65acd796660721fe0af41e7d99d14d29d9dcd0d694c4db21"
    base = EvaluationPlan(
        detection_config_hash=detection_hash,
        context_schema_version="1",
    )
    changed = EvaluationPlan(
        detection_config_hash=detection_hash,
        context_schema_version="1",
        outcomes=(
            OutcomeSelection(
                outcome_id="forward-return",
                outcome_version="1",
                parameters=(ConfigParameter(name="horizon", value=5),),
            ),
        ),
    )
    assert len(detection_hash) == 64
    assert detection_config_hash(config) == detection_hash
    assert evaluation_plan_hash(base) != evaluation_plan_hash(
        changed,
        outcome_parameters={
            ("forward-return", "1"): (ParameterSpec("horizon", ParameterType.INTEGER, 5),)
        },
    )
    assert detection_config_hash(config) == detection_hash


def test_detection_hash_changes_with_instrument_and_is_order_independent() -> None:
    first = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    second = DetectionAnalysisConfig(instrument_id="DAX", calendar_id="demo-v1")
    assert detection_config_hash(first) != detection_config_hash(second)


def test_canonical_utf8_bytes_and_digests_match_golden_fixtures() -> None:
    specs = {("atr", "2"): (
        ParameterSpec("threshold", ParameterType.DECIMAL, Decimal("1.20")),
        ParameterSpec("enabled", ParameterType.BOOLEAN, True),
    )}
    config = DetectionAnalysisConfig(
        instrument_id="DÄX",
        calendar_id="cal-v1",
        components=(ComponentSelection(component_id="atr", component_version="2"),),
    )
    canonical = resolve_detection_config(config, component_parameters=specs).canonical_json()
    detection_bytes = (FIXTURES / "detection-config-v1.json").read_bytes().removesuffix(b"\n")
    assert canonical.encode("utf-8") == detection_bytes
    detection_digest = (FIXTURES / "detection-config-v1.sha256").read_text().strip()
    assert sha256(b"detection-config-v1\n" + detection_bytes).hexdigest() == detection_digest
    assert detection_config_hash(config, component_parameters=specs) == detection_digest

    plan = EvaluationPlan(
        detection_config_hash=detection_digest,
        context_schema_version="1",
        export_settings=(ConfigParameter(
            name="at", value=datetime(2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=2)))
        ),),
    )
    plan_bytes = (FIXTURES / "evaluation-plan-v1.json").read_bytes().removesuffix(b"\n")
    assert plan.canonical_json().encode("utf-8") == plan_bytes
    plan_digest = (FIXTURES / "evaluation-plan-v1.sha256").read_text().strip()
    assert sha256(b"evaluation-plan-v1\n" + plan_bytes).hexdigest() == plan_digest
    assert evaluation_plan_hash(plan) == plan_digest


def test_unresolved_selections_cannot_be_hashed() -> None:
    config = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal",
        components=(ComponentSelection(component_id="atr", component_version="1"),),
    )
    with pytest.raises(ConfigurationError, match="unregistered component"):
        detection_config_hash(config)
    plan = EvaluationPlan(
        detection_config_hash="abc", context_schema_version="1",
        outcomes=(OutcomeSelection(outcome_id="return", outcome_version="1"),),
    )
    with pytest.raises(ConfigurationError, match="unregistered outcome"):
        evaluation_plan_hash(plan)


def test_pattern_version_and_plan_changes_have_separate_hash_boundaries() -> None:
    from market_analysis.patterns import ConditionGroup, PatternDefinition, TransitionSpec

    def definition(version: str) -> PatternDefinition:
        return PatternDefinition(
            "compression", version, "Compression", "Demo", (), (),
            (ParameterSpec("bars", ParameterType.INTEGER, 3),),
            ("idle", "active"), (TransitionSpec("idle", "active", "entry"),),
            (ConditionGroup("formation", ("entry",)),), ("entry",), (), ("entry",),
        )

    hashes = []
    for version in ("1", "2"):
        selected = DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="cal",
            patterns=(PatternSelection(pattern_id="compression", pattern_version=version),),
        )
        current_definition = definition(version)
        hashes.append(detection_config_hash(
            selected, pattern_definitions={current_definition.identity: current_definition}
        ))
    assert hashes[0] != hashes[1]

    base = EvaluationPlan(detection_config_hash=hashes[0], context_schema_version="1")
    changed = base.model_copy(
        update={"export_settings": (ConfigParameter(name="format", value="csv"),)}
    )
    assert evaluation_plan_hash(base) != evaluation_plan_hash(changed)
    assert hashes[0] == detection_config_hash(
        DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="cal",
            patterns=(PatternSelection(pattern_id="compression", pattern_version="1"),),
        ),
        pattern_definitions={definition("1").identity: definition("1")},
    )


def test_decimal_scale_and_null_are_canonical() -> None:
    def plan(value: Decimal) -> EvaluationPlan:
        return EvaluationPlan(
            detection_config_hash="abc", context_schema_version="1",
            export_settings=(
                ConfigParameter(name="threshold", value=value),
                ConfigParameter(name="unset", value=None),
            ),
        )

    assert plan(Decimal("1.20")).canonical_json() == plan(Decimal("1.2")).canonical_json()
    assert plan(Decimal("-0.00")).canonical_json() == plan(Decimal("0")).canonical_json()
    assert '"value":null,"value_type":"null"' in plan(Decimal("1.20")).canonical_json()


def test_evaluation_defaults_match_explicit_values() -> None:
    outcome_specs = {("return", "1"): (ParameterSpec("horizon", ParameterType.INTEGER, 5),)}
    segment_specs = {("session", "1"): (ParameterSpec("enabled", ParameterType.BOOLEAN, True),)}

    def plan(explicit: bool) -> EvaluationPlan:
        return EvaluationPlan(
            detection_config_hash="abc",
            context_schema_version="1",
            outcomes=(OutcomeSelection(
                outcome_id="return",
                outcome_version="1",
                parameters=(ConfigParameter(name="horizon", value=5),) if explicit else (),
            ),),
            segments=(SegmentSelection(
                segment_id="session",
                segment_version="1",
                parameters=(ConfigParameter(name="enabled", value=True),) if explicit else (),
            ),),
        )

    assert evaluation_plan_hash(
        plan(False), outcome_parameters=outcome_specs, segment_parameters=segment_specs
    ) == evaluation_plan_hash(
        plan(True), outcome_parameters=outcome_specs, segment_parameters=segment_specs
    )


def test_detection_parameter_mutation_changes_both_hashes_when_plan_is_rebound() -> None:
    specs = {("atr", "1"): (ParameterSpec("length", ParameterType.INTEGER, 14),)}

    def selected(length: int) -> DetectionAnalysisConfig:
        return DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="cal",
            components=(ComponentSelection(
                component_id="atr", component_version="1",
                parameters=(ConfigParameter(name="length", value=length),),
            ),),
        )

    first = detection_config_hash(selected(14), component_parameters=specs)
    second = detection_config_hash(selected(21), component_parameters=specs)
    assert first != second
    assert evaluation_plan_hash(EvaluationPlan(
        detection_config_hash=first, context_schema_version="1",
    )) != evaluation_plan_hash(EvaluationPlan(
        detection_config_hash=second, context_schema_version="1",
    ))


def test_outcome_version_configuration_and_context_mutate_only_plan_hash() -> None:
    detection = DetectionAnalysisConfig(instrument_id="US30", calendar_id="cal")
    fingerprint = detection_config_hash(detection)
    specs = {
        ("return", "1"): (ParameterSpec("horizon", ParameterType.INTEGER, 5),),
        ("return", "2"): (ParameterSpec("horizon", ParameterType.INTEGER, 5),),
    }

    def plan(version: str, horizon: int, context: tuple[str, ...] = ()) -> EvaluationPlan:
        return EvaluationPlan(
            detection_config_hash=fingerprint, context_schema_version="1",
            context_fields=context,
            outcomes=(OutcomeSelection(
                outcome_id="return", outcome_version=version,
                parameters=(ConfigParameter(name="horizon", value=horizon),),
            ),),
        )

    baseline = evaluation_plan_hash(plan("1", 5), outcome_parameters=specs)
    assert baseline != evaluation_plan_hash(plan("1", 10), outcome_parameters=specs)
    assert baseline != evaluation_plan_hash(plan("2", 5), outcome_parameters=specs)
    assert baseline != evaluation_plan_hash(
        plan("1", 5, ("session",)), outcome_parameters=specs
    )
    assert detection_config_hash(detection) == fingerprint


def test_selection_and_parameter_order_do_not_change_resolved_hashes() -> None:
    specs = {
        ("atr", "1"): (
            ParameterSpec("period", ParameterType.INTEGER, 14),
            ParameterSpec("enabled", ParameterType.BOOLEAN, True),
        ),
    }
    ema = ComponentSelection(component_id="ema", component_version="1")
    atr = ComponentSelection(component_id="atr", component_version="1", parameters=(
        ConfigParameter(name="period", value=14),
        ConfigParameter(name="enabled", value=True),
    ))
    reordered_atr = atr.model_copy(update={"parameters": tuple(reversed(atr.parameters))})
    first = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal", components=(ema, atr),
    )
    reordered = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="cal", components=(reordered_atr, ema),
    )
    assert detection_config_hash(first, component_parameters=specs) == detection_config_hash(
        reordered, component_parameters=specs
    )


def test_presentation_settings_cannot_enter_either_hash_payload() -> None:
    with pytest.raises(ValidationError):
        DetectionAnalysisConfig.model_validate({
            "instrument_id": "US30", "calendar_id": "cal", "chart_color": "blue",
        })
    with pytest.raises(ValidationError):
        EvaluationPlan.model_validate({
            "detection_config_hash": "abc", "context_schema_version": "1",
            "viewport_width": 1200,
        })
