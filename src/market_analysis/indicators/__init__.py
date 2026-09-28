"""Incremental, provider-independent market-state contracts."""

from .atr import AtrState, VolatilityState
from .ema import EmaState
from .incremental import IncrementalMarketState, MarketState, MarketStateError
from .range_state import (
    RANGE_STATE_DEFINITION_ID,
    BandwidthObservation,
    BandwidthState,
    ChopCategory,
    ChopInput,
    ChopState,
    CloseObservation,
    CompressionCategory,
    CompressionState,
    RangeAvailability,
    RangeLineage,
    RangeObservation,
    RangeParameters,
    RangeState,
)
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
from .swing_structure import (
    SWING_STRUCTURE_DEFINITION_ID,
    OverallStructure,
    StructureBreak,
    StructureBreakType,
    StructureLineage,
    SwingClassification,
    SwingLabel,
    SwingStructureState,
)
from .trend_leg import (
    TREND_LEG_DEFINITION_ID,
    EmaCrossSegment,
    EmaCrossTransition,
    TrendDirection,
    TrendLeg,
    TrendLegState,
    TrendLegTransition,
    TrendLegTransitionType,
)

__all__ = [
    "TREND_LEG_DEFINITION_ID", "EmaCrossSegment", "EmaCrossTransition", "TrendDirection",
    "TrendLeg", "TrendLegState", "TrendLegTransition", "TrendLegTransitionType",
    "AtrState", "EmaState", "IncrementalMarketState", "MarketState", "MarketStateError",
    "VolatilityState",
    "SessionComponent",
    "SWING_POINT_DEFINITION_ID", "ConfirmationSource", "EqualExtremePolicy", "ExtremeSource",
    "SwingPoint", "SwingPointReference", "SwingPointState", "SwingType",
    "SWING_STRUCTURE_DEFINITION_ID", "OverallStructure", "StructureBreak", "StructureBreakType",
    "StructureLineage", "SwingClassification", "SwingLabel", "SwingStructureState",
    "RANGE_STATE_DEFINITION_ID", "BandwidthObservation", "BandwidthState", "ChopCategory",
    "ChopInput", "ChopState", "CloseObservation", "CompressionCategory", "CompressionState",
    "RangeAvailability", "RangeLineage", "RangeObservation", "RangeParameters", "RangeState",
]
