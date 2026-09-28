"""Incremental, provider-independent market-state contracts."""

from .atr import AtrState, VolatilityState
from .ema import EmaState
from .incremental import IncrementalMarketState, MarketState, MarketStateError
from .session import SessionComponent
from .swing_point import (
    SWING_POINT_DEFINITION_ID,
    ConfirmationSource,
    EqualExtremePolicy,
    ExtremeSource,
    SwingPoint,
    SwingPointReference,
    SwingPointState,
    SwingType,
)

__all__ = [
    "AtrState", "EmaState", "IncrementalMarketState", "MarketState", "MarketStateError",
    "VolatilityState",
    "SessionComponent",
    "SWING_POINT_DEFINITION_ID", "ConfirmationSource", "EqualExtremePolicy", "ExtremeSource",
    "SwingPoint", "SwingPointReference", "SwingPointState", "SwingType",
]
