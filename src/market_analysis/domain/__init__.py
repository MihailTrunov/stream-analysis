"""Provider-independent domain contracts."""

from .dataset_validation import (
    Check,
    ExpectedSlotCalendar,
    Finding,
    Severity,
    ValidationReport,
    ValidationStatus,
    validate_dataset,
)
from .historical_data import (
    HistoricalDataError,
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalDataSource,
    HistoricalSource,
    MappingUnavailableError,
    ProviderError,
)
from .market_data import (
    Bar,
    DomainValidationError,
    Instrument,
    ProviderSymbolMapping,
    Timeframe,
)

__all__ = [
    "Bar",
    "Check",
    "DomainValidationError",
    "ExpectedSlotCalendar",
    "Finding",
    "HistoricalDataError",
    "HistoricalDataPage",
    "HistoricalDataRequest",
    "HistoricalDataSource",
    "HistoricalSource",
    "MappingUnavailableError",
    "Instrument",
    "ProviderError",
    "ProviderSymbolMapping",
    "Severity",
    "Timeframe",
    "ValidationReport",
    "ValidationStatus",
    "validate_dataset",
]
