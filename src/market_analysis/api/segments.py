"""Registered detection-time partition contracts, available without a database."""

from typing import Any

from fastapi import APIRouter

from market_analysis.application.segment_registry import SEGMENT_DEFINITIONS

router = APIRouter(prefix="/segment-definitions", tags=["segments"])


@router.get("")
def definitions() -> dict[str, Any]:
    return {"definitions": [item.model_dump(mode="json") for item in SEGMENT_DEFINITIONS.values()]}
