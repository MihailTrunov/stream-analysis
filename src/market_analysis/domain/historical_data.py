"""Provider-neutral, paginated historical bar acquisition contract.

A terminal page (``next_page_token is None``) completes the requested range.
Callers must follow continuation tokens before treating a response as complete.
No bars in a terminal page is a successful empty result, including for an empty
``[start, end)`` range. Provider failures raise, so they cannot look like empty data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .market_data import Bar, DomainValidationError, Instrument, Timeframe


class HistoricalDataError(Exception):
    """Base error for acquisition failures; no page is returned on failure."""


class MappingUnavailableError(HistoricalDataError):
    """The source has no symbol mapping for the requested instrument."""


class ProviderError(HistoricalDataError):
    """The source failed while retrieving data; callers may apply their own policy."""


@dataclass(frozen=True, slots=True)
class HistoricalDataRequest:
    instrument: Instrument
    timeframe: Timeframe
    start: datetime
    end: datetime
    page_token: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, Instrument):
            raise DomainValidationError("instrument must be an Instrument")
        if not isinstance(self.timeframe, Timeframe):
            raise DomainValidationError("timeframe must be a canonical Timeframe")
        for name in ("start", "end"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise DomainValidationError(f"{name} must be a UTC datetime")
            if value.utcoffset() != UTC.utcoffset(value):
                raise DomainValidationError(f"{name} must be a UTC datetime")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.end < self.start:
            raise DomainValidationError("end must be >= start")
        if self.page_token is not None and (
            not isinstance(self.page_token, str) or not self.page_token
        ):
            raise DomainValidationError("page_token must be a non-empty string")


@dataclass(frozen=True, slots=True)
class HistoricalSource:
    provider: str
    symbol: str
    source_id: str
    environment: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "symbol", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{name} must be non-empty")
        if self.environment is not None and not self.environment.strip():
            raise DomainValidationError("environment must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class HistoricalDataPage:
    request: HistoricalDataRequest
    bars: tuple[Bar, ...]
    source: HistoricalSource
    next_page_token: str | None = None

    def __post_init__(self) -> None:
        bars = tuple(self.bars)
        previous: datetime | None = None
        for bar in bars:
            if not isinstance(bar, Bar):
                raise DomainValidationError("bars must contain canonical Bars")
            if (
                bar.instrument_id != self.request.instrument.instrument_id
                or bar.timeframe != self.request.timeframe
            ):
                raise DomainValidationError("bar identity does not match request")
            if not self.request.start <= bar.timestamp < self.request.end:
                raise DomainValidationError("bar timestamp is outside [start, end)")
            if previous is not None and bar.timestamp <= previous:
                raise DomainValidationError("bars must have strictly increasing timestamps")
            if bar.source_id != self.source.source_id:
                raise DomainValidationError("bar source_id does not match source metadata")
            previous = bar.timestamp
        object.__setattr__(self, "bars", bars)
        if self.next_page_token is not None:
            if not isinstance(self.next_page_token, str) or not self.next_page_token:
                raise DomainValidationError("next_page_token must be a non-empty string")
            if not bars:
                raise DomainValidationError("a partial page must contain bars")
            if self.next_page_token == self.request.page_token:
                raise DomainValidationError("next_page_token must advance")

    @property
    def is_complete(self) -> bool:
        return self.next_page_token is None


@runtime_checkable
class HistoricalDataSource(Protocol):
    """Return one deterministic page for a canonical request.

    Tokens are opaque to callers and valid only for the same range, instrument,
    timeframe, and source snapshot. Adapters own provider retries and limits.
    """

    def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage: ...
