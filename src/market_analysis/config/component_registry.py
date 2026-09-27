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

BUILTIN_COMPONENT_PARAMETERS: Mapping[DefinitionKey, tuple[ParameterSpec, ...]] = (
    MappingProxyType({("ema", "1"): EMA_V1_PARAMETERS})
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
