"""Authoritative versioned component parameter schemas for run and UI validation."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from market_analysis.patterns import ParameterSpec, ParameterType

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

BUILTIN_COMPONENT_PARAMETERS: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] = (
    MappingProxyType({
        ("ema", "1"): EMA_V1_PARAMETERS,
        ("atr", "1"): ATR_V1_PARAMETERS,
        ("swing_point", "1"): SWING_POINT_V1_PARAMETERS,
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
