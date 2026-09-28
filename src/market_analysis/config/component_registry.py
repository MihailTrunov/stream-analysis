"""Authoritative versioned component parameter schemas for run and UI validation."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from market_analysis.patterns import ParameterSpec, ParameterType, PatternDefinitionError

DefinitionKey = tuple[str, str]

EMA_V1_PARAMETERS = (
    ParameterSpec(
        "period", ParameterType.INTEGER, 45,
        minimum=Decimal(1),
        description="Number of completed closes used to seed and smooth the EMA.",
    ),
)

ATR_V1_PARAMETERS = (
    ParameterSpec(
        "period", ParameterType.INTEGER, 14,
        minimum=Decimal(1),
        description="Wilder ATR period over completed canonical bars.",
    ),
)

SWING_POINT_V1_PARAMETERS = (
    ParameterSpec(
        "atr_period", ParameterType.INTEGER, 14,
        minimum=Decimal(1),
        description="ATR period whose observable value is frozen at each candidate extreme.",
    ),
    ParameterSpec(
        "reversal_atr_multiplier", ParameterType.DECIMAL, Decimal("1.5"),
        minimum=Decimal("0.0001"),
        description="Reversal threshold in ATR units, frozen when the candidate extreme forms.",
    ),
    ParameterSpec(
        "extreme_source", ParameterType.STRING, "HIGH_LOW",
        supported_values=("HIGH_LOW",),
        description="Candidate extreme source; v1 supports OHLC high/low only.",
    ),
    ParameterSpec(
        "confirmation_source", ParameterType.STRING, "CLOSE",
        supported_values=("CLOSE",),
        description="Confirmation price source; v1 supports completed closes only.",
    ),
    ParameterSpec(
        "freeze_atr_at_extreme", ParameterType.BOOLEAN, True,
        supported_values=(True,),
        description="v1 freezes ATR at the extreme; rolling ATR requires a new version.",
    ),
    ParameterSpec(
        "allow_same_bar_confirmation", ParameterType.BOOLEAN, False,
        supported_values=(False,),
        description="v1 requires a later completed bar; same-bar confirmation is a new version.",
    ),
    ParameterSpec(
        "equal_extreme_policy", ParameterType.STRING, "KEEP_EARLIEST",
        supported_values=("KEEP_EARLIEST",),
        description="Equal extremes keep the earliest event_time; v1 has no other policy.",
    ),
    ParameterSpec(
        "require_alternation", ParameterType.BOOLEAN, True,
        supported_values=(True,),
        description="v1 requires confirmed swings to alternate; removal is a new version.",
    ),
)

SWING_STRUCTURE_V1_PARAMETERS = (
    ParameterSpec(
        "equal_level_atr_multiplier", ParameterType.DECIMAL, Decimal("0.10"),
        minimum=Decimal(0), description="Inclusive equality tolerance in new-swing ATR units.",
    ),
    ParameterSpec(
        "equality_atr_anchor", ParameterType.STRING, "NEW_SWING_ATR_AT_EXTREME",
        supported_values=("NEW_SWING_ATR_AT_EXTREME",),
    ),
    ParameterSpec(
        "break_buffer_atr_multiplier", ParameterType.DECIMAL, Decimal("0.10"),
        minimum=Decimal(0), description="Strict close-break buffer in reference-swing ATR units.",
    ),
    ParameterSpec(
        "break_atr_anchor", ParameterType.STRING, "REFERENCE_SWING_ATR_AT_EXTREME",
        supported_values=("REFERENCE_SWING_ATR_AT_EXTREME",),
    ),
    ParameterSpec(
        "break_confirmation_source", ParameterType.STRING, "CLOSE", supported_values=("CLOSE",),
    ),
    ParameterSpec(
        "allow_break_on_reference_detection_bar", ParameterType.BOOLEAN, False,
        supported_values=(False,),
    ),
)

TREND_LEG_V1_PARAMETERS = (
    ParameterSpec("ema_instance_id", ParameterType.STRING, "ema"),
    ParameterSpec("structure_instance_id", ParameterType.STRING, "swing_structure"),
    ParameterSpec(
        "establishment_mode",
        ParameterType.STRING,
        "CONFIRMED_CORRECTIVE_DIRECTIONAL_V1",
        supported_values=("CONFIRMED_CORRECTIVE_DIRECTIONAL_V1",),
    ),
    ParameterSpec(
        "termination_mode",
        ParameterType.STRING,
        "PROTECTED_SWING_CLOSE_BREAK_V1",
        supported_values=("PROTECTED_SWING_CLOSE_BREAK_V1",),
    ),
    ParameterSpec(
        "ema_segment_boundary_mode",
        ParameterType.STRING,
        "INCLUSIVE_SHARED_BAR_V1",
        supported_values=("INCLUSIVE_SHARED_BAR_V1",),
    ),
    ParameterSpec(
        "favorable_extreme_mode",
        ParameterType.STRING,
        "DIRECTIONAL_SWING_AND_OBSERVED_BARS_V1",
        supported_values=("DIRECTIONAL_SWING_AND_OBSERVED_BARS_V1",),
    ),
)

TREND_LEG_QUALIFICATION_V1_PARAMETERS = (
    ParameterSpec("trend_leg_instance_id", ParameterType.STRING, "trend_leg"),
    ParameterSpec(
        "min_duration_bars", ParameterType.INTEGER, 30, minimum=Decimal(1),
        description="Minimum completed one-minute bars from the initial protected swing.",
    ),
    ParameterSpec(
        "min_directional_move_points", ParameterType.DECIMAL, Decimal(70), minimum=Decimal(0),
        description="Minimum directional move from initial protection to the completed close.",
    ),
    ParameterSpec(
        "qualification_mode", ParameterType.STRING, "LIVE_STICKY_V1",
        supported_values=("LIVE_STICKY_V1",),
    ),
)

RANGE_STATE_V1_PARAMETERS = (
    ParameterSpec("chop_period", ParameterType.INTEGER, 14, minimum=Decimal(2)),
    ParameterSpec("chop_directional_threshold", ParameterType.DECIMAL, Decimal("38.2"),
                  minimum=Decimal(0), maximum=Decimal(100)),
    ParameterSpec("chop_range_threshold", ParameterType.DECIMAL, Decimal("61.8"),
                  minimum=Decimal(0), maximum=Decimal(100)),
    ParameterSpec("bandwidth_period", ParameterType.INTEGER, 20, minimum=Decimal(1)),
    ParameterSpec("bandwidth_stddev_multiplier", ParameterType.DECIMAL, Decimal("2.0"),
                  minimum=Decimal(0)),
    ParameterSpec("compression_reference_bars", ParameterType.INTEGER, 120, minimum=Decimal(2)),
    ParameterSpec("expanded_score_threshold", ParameterType.DECIMAL, Decimal(20),
                  minimum=Decimal(0), maximum=Decimal(100)),
    ParameterSpec("compressed_score_threshold", ParameterType.DECIMAL, Decimal(80),
                  minimum=Decimal(0), maximum=Decimal(100)),
    ParameterSpec("bandwidth_stddev_mode", ParameterType.STRING, "POPULATION_V1",
                  supported_values=("POPULATION_V1",)),
    ParameterSpec("compression_percentile_mode", ParameterType.STRING, "STRICT_EMPIRICAL_V1",
                  supported_values=("STRICT_EMPIRICAL_V1",)),
)


def validate_component_parameters(key: DefinitionKey, values: Mapping[str, object]) -> None:
    """Cross-field checks scoped to the new version; legacy definitions stay unchanged."""
    if key != ("range_state", "1"):
        return
    for lower, upper in (
        ("chop_directional_threshold", "chop_range_threshold"),
        ("expanded_score_threshold", "compressed_score_threshold"),
    ):
        lo, hi = values[lower], values[upper]
        if not isinstance(lo, Decimal | int) or not isinstance(hi, Decimal | int) or lo >= hi:
            raise PatternDefinitionError(f"{lower} must be strictly below {upper}")


BUILTIN_COMPONENT_PARAMETERS: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] = (
    MappingProxyType({
        ("ema", "1"): EMA_V1_PARAMETERS,
        ("atr", "1"): ATR_V1_PARAMETERS,
        ("swing_point", "1"): SWING_POINT_V1_PARAMETERS,
        ("swing_structure", "1"): SWING_STRUCTURE_V1_PARAMETERS,
        ("trend_leg", "1"): TREND_LEG_V1_PARAMETERS,
        ("trend_leg_qualification", "1"): TREND_LEG_QUALIFICATION_V1_PARAMETERS,
        ("range_state", "1"): RANGE_STATE_V1_PARAMETERS,
    })
)


def component_definition_schema() -> tuple[dict[str, object], ...]:
    """Stable UI-facing metadata; submitted values are validated again server-side."""
    return tuple(
        {
            "component_id": component_id,
            "component_version": version,
            "parameters": [spec.canonical(include_default=True, include_description=True)
                           for spec in specs],
        }
        for (component_id, version), specs in sorted(BUILTIN_COMPONENT_PARAMETERS.items())
    )
