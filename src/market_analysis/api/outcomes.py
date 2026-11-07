"""Read-only registered outcome contracts for explicit plan selection."""

from typing import Any

from fastapi import APIRouter

from market_analysis.application.forward_return import DEFAULT_HORIZONS
from market_analysis.application.outcome_registry import OUTCOME_DEFINITIONS

router = APIRouter(prefix="/outcome-definitions", tags=["outcomes"])


@router.get("")
def definitions() -> dict[str, Any]:
    return {
        "default_horizons": list(DEFAULT_HORIZONS),
        "definitions": [value.model_dump(mode="json") for value in OUTCOME_DEFINITIONS.values()],
    }
