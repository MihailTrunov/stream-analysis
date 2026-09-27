"""Provider-independent acquisition of paginated historical canonical bars."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime

from market_analysis.domain import (
    Bar,
    HistoricalDataError,
    HistoricalDataRequest,
    HistoricalDataSource,
    HistoricalSource,
)


def iter_historical_bars(
    source: HistoricalDataSource, request: HistoricalDataRequest
) -> Iterator[Bar]:
    """Yield an ordered range and propagate any page failure to the caller.

    The consumer knows only the canonical domain contract. Callers must exhaust
    the iterator before treating the requested range as complete.
    """
    current = request
    seen_tokens: set[str] = set()
    last_timestamp: datetime | None = None
    first_source: HistoricalSource | None = None
    while True:
        page = source.get_bars(current)
        if page.request != current:
            raise HistoricalDataError("source returned a page for a different request")
        if first_source is None:
            first_source = page.source
        elif page.source != first_source:
            raise HistoricalDataError("source metadata changed across pages")
        for bar in page.bars:
            if last_timestamp is not None and bar.timestamp <= last_timestamp:
                raise HistoricalDataError("bars are not ordered across pages")
            last_timestamp = bar.timestamp
            yield bar
        if page.next_page_token is None:
            return
        if page.next_page_token in seen_tokens:
            raise HistoricalDataError("source repeated a continuation token")
        seen_tokens.add(page.next_page_token)
        current = replace(current, page_token=page.next_page_token)
