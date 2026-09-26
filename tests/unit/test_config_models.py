from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from market_analysis.config import (
    ComponentSelection, ConfigParameter, ConfigurationPresetRevision,
    DetectionAnalysisConfig, EvaluationPlan, OutcomeSelection, PatternSelection,
    SegmentSelection, StalePresetRevisionError,
)
from market_analysis.patterns import (
    ConditionGroup, ParameterSpec, ParameterType, PatternDefinition, TransitionSpec,
)


def test_detection_config_is_immutable_forbids_unknowns_and_serializes_canonically() -> None:
    component = ComponentSelection(
        component_id="ema", component_version="1",
        parameters=(ConfigParameter(name="period", value=45),),
    )
    config = DetectionAnalysisConfig(
        instrument_id="US30", calendar_id="oanda-us30-v1", components=(component,),
    )
    assert '"timeframe":"1m"' in config.canonical_json()
    with pytest.raises(ValidationError):
        config.instrument_id = "DAX"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DetectionAnalysisConfig(instrument_id="US30", calendar_id="cal", made_up=True)  # type: ignore[call-arg]


def test_pattern_defaults_are_fully_resolved_before_config_snapshot() -> None:
    definition = PatternDefinition(
        "compression", "1", "Compression", "Compression", ("range_state",), (),
        (ParameterSpec("bars", ParameterType.INTEGER, 3),), ("idle", "active"),
        (TransitionSpec("idle", "active", "entry"),),
        (ConditionGroup("formation", ("entry",)),), ("entry",), (), ("entry",),
    )
    assert PatternSelection.from_definition(definition) == PatternSelection.from_definition(definition, {"bars": 3})


def test_evaluation_plan_changes_do_not_mutate_detection_config() -> None:
    detection = DetectionAnalysisConfig(instrument_id="US30", calendar_id="cal")
    before = detection.canonical_json()
    plan_a = EvaluationPlan(
        detection_config_hash="abc", context_schema_version="1",
        outcomes=(OutcomeSelection(outcome_id="return", outcome_version="1", parameters=(ConfigParameter(name="bars", value=5),)),),
    )
    plan_b = EvaluationPlan(
        detection_config_hash="abc", context_schema_version="1",
        outcomes=(OutcomeSelection(outcome_id="return", outcome_version="1", parameters=(ConfigParameter(name="bars", value=10),)),),
        segments=(SegmentSelection(segment_id="session", segment_version="1"),),
    )
    assert detection.canonical_json() == before
    assert plan_a.canonical_json() != plan_b.canonical_json()


def test_decimal_timestamp_and_preset_revision_semantics() -> None:
    local = timezone(timedelta(hours=2))
    plan = EvaluationPlan(
        detection_config_hash="abc", context_schema_version="1",
        export_settings=(
            ConfigParameter(name="when", value=datetime(2026, 1, 1, 2, tzinfo=local)),
            ConfigParameter(name="x", value=Decimal("1.20")),
        ),
    )
    assert '"1.20"' in plan.canonical_json()
    assert "2026-01-01T00:00:00Z" in plan.canonical_json()

    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="cal")
    preset = ConfigurationPresetRevision(preset_id="default", revision=2, instrument_id="US30", detection_config=config)
    with pytest.raises(StalePresetRevisionError, match="stale"):
        preset.next_revision(edited_revision=1, detection_config=config)
    assert preset.next_revision(edited_revision=2, detection_config=config).revision == 3


def test_blank_identifiers_and_duplicate_segment_parameters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DetectionAnalysisConfig(instrument_id="   ", calendar_id="cal")
    with pytest.raises(ValidationError):
        EvaluationPlan(detection_config_hash="abc", context_schema_version="1", context_fields=("",))
    with pytest.raises(ValidationError, match="segment parameter"):
        SegmentSelection(
            segment_id="session", segment_version="1",
            parameters=(ConfigParameter(name="x", value=1), ConfigParameter(name="x", value=2)),
        )
