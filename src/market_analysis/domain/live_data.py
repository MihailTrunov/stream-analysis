"""Provider-neutral completed-minute polling, with separate forming display data."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .historical_data import HistoricalDataPage, HistoricalDataRequest
from .market_data import Bar, DomainValidationError, Timeframe


@dataclass(frozen=True)
class LiveCandleUpdate:
    completed: HistoricalDataPage
    observed_at: datetime
    forming: Bar | None = None

    def __post_init__(self) -> None:
        request = self.completed.request
        if (request.timeframe is not Timeframe.M1 or self.observed_at.tzinfo is None
            or request.end > self.observed_at or not self.completed.is_complete
            or any(not bar.is_complete or bar.timestamp + timedelta(minutes=1) > request.end
                   for bar in self.completed.bars)):
            raise DomainValidationError("live updates require a terminal completed M1 prefix")
        if self.forming is not None and (
            self.forming.is_complete or self.forming.timeframe is not Timeframe.M1
            or self.forming.instrument_id != request.instrument.instrument_id
            or self.forming.source_id != self.completed.source.source_id
            or self.forming.timestamp.second or self.forming.timestamp.microsecond
            or not self.forming.timestamp <= self.observed_at
            < self.forming.timestamp + timedelta(minutes=1)
        ):
            raise DomainValidationError("forming display data must belong to the current M1 candle")


class LiveCandleSource(Protocol):
    def poll_live(self, request: HistoricalDataRequest, *, at: datetime) -> LiveCandleUpdate: ...

    def close(self) -> None: ...
