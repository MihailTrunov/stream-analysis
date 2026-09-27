"""Provider-independent domain contracts."""

from .dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    BarSequence,
    DatasetLineage,
    canonical_bar_checksum,
    canonical_bar_checksum_ordered,
)
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
    "BarSequence",
    "BAR_CHECKSUM_VERSION",
    "Check",
    "DomainValidationError",
    "DatasetLineage",
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
    "canonical_bar_checksum",
    "canonical_bar_checksum_ordered",
]
