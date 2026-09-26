from dataclasses import replace
from decimal import Decimal

import pytest

from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    PatternDefinitionError,
    PatternDefinitionRegistry,
    TransitionSpec,
    assert_semantic_change_is_versioned,
)


def definition(
    version: str = "1",
    confirm: str = "protected_swing_break",
    threshold: str = "35",
) -> PatternDefinition:
    return PatternDefinition(
        "trend-reversal",
        version,
        "Trend reversal",
        "Reversal hypothesis",
        ("trend_leg", "ema"),
        (confirm,),
        (
            ParameterSpec(
                "retracement_points",
                ParameterType.DECIMAL,
                Decimal(threshold),
                minimum=Decimal("0"),
            ),
            ParameterSpec(
                "expiry_bars",
                ParameterType.INTEGER,
                60,
                minimum=Decimal("1"),
            ),
            ParameterSpec(
                "enabled",
                ParameterType.BOOLEAN,
                True,
            ),
        ),
        (
            "idle",
            "candidate",
            "confirmed",
            "invalidated",
            "expired",
        ),
        (
            TransitionSpec(
                "idle",
                "candidate",
                "opposing_ema_cross",
            ),
            TransitionSpec(
                "candidate",
                "confirmed",
                confirm,
            ),
            TransitionSpec(
                "candidate",
                "invalidated",
                "new_directional_extreme",
            ),
            TransitionSpec(
                "candidate",
                "expired",
                "expiry",
            ),
        ),
        (
            ConditionGroup(
                "candidate",
                ("opposing_ema_cross",),
            ),
            ConditionGroup(
                "confirmation",
                (confirm,),
            ),
            ConditionGroup(
                "invalidation",
                ("new_directional_extreme",),
            ),
            ConditionGroup(
                "expiry",
                ("expiry",),
            ),
        ),
        (
            confirm,
            "new_directional_extreme",
            "expiry",
        ),
        (
            ContextFieldSpec(
                "protected_swing",
                "event_ref",
            ),
            ContextFieldSpec(
                "candidate_started_at",
                "datetime",
            ),
        ),
        (
            "opposing_ema_cross",
            confirm,
        ),
    )


def test_deterministic_serialization_and_parameter_validation() -> None:
    item = definition()
    assert item.canonical_json() == definition().canonical_json()
    resolved = item.resolve_parameters({"expiry_bars": 30})
    assert resolved["retracement_points"] == Decimal("35")
    with pytest.raises(PatternDefinitionError, match="unknown parameters"):
        item.resolve_parameters({"unknown": 1})
    with pytest.raises(PatternDefinitionError, match="integer"):
        item.resolve_parameters({"expiry_bars": True})


def test_versions_coexist_and_semantic_changes_require_version_bump() -> None:
    registry = PatternDefinitionRegistry()
    registry.register(definition("1"))
    registry.register(
        definition(
            "2",
            confirm="close_break_confirmed",
        )
    )
    assert len(registry.definitions()) == 2
    assert_semantic_change_is_versioned(
        definition(
            "1",
            threshold="35",
        ),
        definition(
            "1",
            threshold="40",
        ),
    )
    with pytest.raises(
        PatternDefinitionError,
        match="without a pattern_version change",
    ):
        assert_semantic_change_is_versioned(
            definition("1"),
            definition(
                "1",
                confirm="wick_break",
            ),
        )


def test_registry_rejects_same_identity_definition_drift() -> None:
    registry = PatternDefinitionRegistry()
    registry.register(
        definition(
            threshold="35",
        )
    )
    with pytest.raises(
        PatternDefinitionError,
        match="two different definitions",
    ):
        registry.register(
            definition(
                threshold="40",
            )
        )


def test_transition_and_rationale_ids_must_be_declared() -> None:
    with pytest.raises(
        PatternDefinitionError,
        match="transition triggers",
    ):
        PatternDefinition(
            "bad",
            "1",
            "Bad",
            "Bad",
            (),
            (),
            (),
            ("idle", "done"),
            (
                TransitionSpec(
                    "idle",
                    "done",
                    "undeclared",
                ),
            ),
            (
                ConditionGroup(
                    "confirmation",
                    ("declared",),
                ),
            ),
            (),
            (),
            (),
        )


def test_same_state_and_trigger_cannot_have_two_targets() -> None:
    base = definition()
    with pytest.raises(PatternDefinitionError, match="ambiguous transition"):
        replace(
            base,
            transitions=base.transitions
            + (TransitionSpec("candidate", "expired", "protected_swing_break"),),
        )


def test_competing_transitions_require_declared_precedence() -> None:
    with pytest.raises(PatternDefinitionError, match="precedence must cover"):
        replace(
            definition(),
            simultaneous_precedence=("protected_swing_break",),
        )


def test_contract_supports_compression_and_continuation_context_without_framework_changes() -> None:
    compression = PatternDefinition(
        "range-compression",
        "1",
        "Compression",
        "Compression hypothesis",
        ("range_state",),
        (),
        (
            ParameterSpec(
                "persistence_bars",
                ParameterType.INTEGER,
                3,
            ),
        ),
        ("idle", "active"),
        (
            TransitionSpec(
                "idle",
                "active",
                "compression_entry_pass",
            ),
        ),
        (
            ConditionGroup(
                "formation",
                ("compression_entry_pass",),
            ),
        ),
        ("compression_entry_pass",),
        (
            ContextFieldSpec(
                "qualifying_count",
                "int",
            ),
        ),
        ("compression_entry_pass",),
    )
    continuation = PatternDefinition(
        "trend-continuation",
        "1",
        "Continuation",
        "Continuation hypothesis",
        ("trend_leg", "ema"),
        (),
        (
            ParameterSpec(
                "expiry_bars",
                ParameterType.INTEGER,
                60,
            ),
        ),
        (
            "idle",
            "candidate",
            "confirmed",
        ),
        (
            TransitionSpec(
                "idle",
                "candidate",
                "opposing_ema_cross",
            ),
            TransitionSpec(
                "candidate",
                "confirmed",
                "ema_reclaim",
            ),
        ),
        (
            ConditionGroup(
                "candidate",
                ("opposing_ema_cross",),
            ),
            ConditionGroup(
                "confirmation",
                ("ema_reclaim",),
            ),
        ),
        ("ema_reclaim",),
        (
            ContextFieldSpec(
                "frozen_directional_swing",
                "event_ref",
            ),
            ContextFieldSpec(
                "frozen_protected_swing",
                "event_ref",
            ),
        ),
        (
            "opposing_ema_cross",
            "ema_reclaim",
        ),
    )
    assert compression.context_schema[0].field_id == "qualifying_count"
    assert len(continuation.context_schema) == 2


def test_documentation_and_default_only_changes_do_not_change_semantics() -> None:
    previous = definition()
    candidate = definition(
        threshold="40",
    )
    object.__setattr__(
        candidate,
        "name",
        "Updated display name",
    )
    object.__setattr__(
        candidate,
        "description",
        "Updated docs",
    )
    assert_semantic_change_is_versioned(
        previous,
        candidate,
    )
