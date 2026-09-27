"""Deterministic in-memory historical source for tests and offline use."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from market_analysis.domain.historical_data import (
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalSource,
    MappingUnavailableError,
    ProviderError,
)
from market_analysis.domain.market_data import Bar


@dataclass(frozen=True, slots=True)
class InMemoryHistoricalDataSource:
    bars: tuple[Bar, ...] = ()
    provider: str = "memory"
    environment: str | None = None
    page_size: int = 1000
    error: ProviderError | None = None
    _ordered_bars: tuple[Bar, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise ValueError("page_size must be positive")
        object.__setattr__(self, "bars", tuple(self.bars))
        object.__setattr__(
            self, "_ordered_bars", tuple(sorted(self.bars, key=lambda bar: bar.identity))
        )

    def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage:
        if self.error is not None:
            raise self.error
        try:
            symbol = request.instrument.provider_symbol(
                self.provider, environment=self.environment
            )
        except KeyError as exc:
            raise MappingUnavailableError(str(exc)) from exc
        source = HistoricalSource(self.provider, symbol, self.provider, self.environment)
        matching = tuple(
            bar for bar in self._ordered_bars
            if bar.instrument_id == request.instrument.instrument_id
            and bar.timeframe == request.timeframe
            and request.start <= bar.timestamp < request.end
        )
        scope = sha256(
            repr((
                self.provider, self.environment, symbol,
                request.instrument.instrument_id, request.timeframe.value,
                request.start.isoformat(), request.end.isoformat(),
            )).encode("utf-8")
        ).hexdigest()[:16]
        if request.page_token is None:
            offset = 0
        else:
            try:
                token_scope, token_offset = request.page_token.split(":", 1)
                offset = int(token_offset)
            except ValueError as exc:
                raise ProviderError("invalid page token") from exc
            if (
                token_scope != scope or str(offset) != token_offset
                or offset < 1 or offset >= len(matching)
            ):
                raise ProviderError("invalid page token")
        page_bars = matching[offset:offset + self.page_size]
        next_offset = offset + len(page_bars)
        return HistoricalDataPage(
            request=request,
            bars=page_bars,
            source=source,
            next_page_token=f"{scope}:{next_offset}" if next_offset < len(matching) else None,
        )
