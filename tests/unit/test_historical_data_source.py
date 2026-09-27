from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from market_analysis.application.historical_bars import iter_historical_bars
from market_analysis.domain import (
    Bar,
    DomainValidationError,
    HistoricalDataError,
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalDataSource,
    HistoricalSource,
    Instrument,
    MappingUnavailableError,
    ProviderError,
    ProviderSymbolMapping,
    Timeframe,
)
from market_analysis.providers.memory import InMemoryHistoricalDataSource

START = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def instrument(*, mapped: bool = True) -> Instrument:
    mappings = (ProviderSymbolMapping("memory", "US30_TEST"),) if mapped else ()
    return Instrument("US30", "US 30", "test-calendar", 1, Decimal("1"), mappings)


def bar(minute: int, *, instrument_id: str = "US30", timeframe: Timeframe = Timeframe.M1) -> Bar:
    return Bar(
        instrument_id, timeframe, START + timedelta(minutes=minute),
        Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), source_id="memory",
    )


def request(
    *, start: datetime = START, end: datetime = START + timedelta(minutes=3),
    page_token: str | None = None, mapped: bool = True,
) -> HistoricalDataRequest:
    return HistoricalDataRequest(instrument(mapped=mapped), Timeframe.M1, start, end, page_token)


def test_pages_are_ordered_and_respect_half_open_range() -> None:
    source = InMemoryHistoricalDataSource(
        (bar(3), bar(2), bar(-1), bar(0), bar(1), bar(0, instrument_id="DAX")),
        page_size=2,
    )
    assert isinstance(source, HistoricalDataSource)
    first = source.get_bars(request())
    assert [item.timestamp for item in first.bars] == [START, START + timedelta(minutes=1)]
    assert first.source == HistoricalSource("memory", "US30_TEST", "memory")
    assert not first.is_complete
    assert first.next_page_token is not None

    second = source.get_bars(request(page_token=first.next_page_token))
    assert [item.timestamp for item in second.bars] == [START + timedelta(minutes=2)]
    assert second.is_complete
    assert second.next_page_token is None
    assert source.get_bars(request()) == first
    with pytest.raises(ProviderError, match="invalid page token"):
        source.get_bars(request(end=START + timedelta(minutes=2), page_token=first.next_page_token))


def test_empty_data_and_zero_width_range_are_complete() -> None:
    source = InMemoryHistoricalDataSource((bar(0),))
    no_data = source.get_bars(request(start=START + timedelta(minutes=1)))
    empty_range = source.get_bars(request(start=START, end=START))
    assert no_data.bars == empty_range.bars == ()
    assert no_data.is_complete and empty_range.is_complete
    assert no_data.source.symbol == "US30_TEST"


def test_unavailable_mapping_and_provider_error_are_not_empty_pages() -> None:
    source = InMemoryHistoricalDataSource()
    with pytest.raises(MappingUnavailableError):
        source.get_bars(request(mapped=False))

    failure = ProviderError("upstream unavailable")
    failing_source = InMemoryHistoricalDataSource(error=failure)
    with pytest.raises(ProviderError) as raised:
        failing_source.get_bars(request())
    assert raised.value is failure


def test_invalid_requests_and_pages_are_rejected() -> None:
    with pytest.raises(DomainValidationError, match="end must be >= start"):
        request(start=START + timedelta(minutes=1), end=START)
    with pytest.raises(DomainValidationError, match="UTC datetime"):
        request(start=START.astimezone(timezone(timedelta(hours=1))))
    with pytest.raises(DomainValidationError, match="UTC datetime"):
        request(start=START.replace(tzinfo=None))
    with pytest.raises(DomainValidationError, match="page_token"):
        request(page_token="")

    selected = request()
    source = HistoricalSource("memory", "US30_TEST", "memory")
    with pytest.raises(DomainValidationError, match="strictly increasing"):
        HistoricalDataPage(selected, (bar(1), bar(0)), source)
    with pytest.raises(DomainValidationError, match="outside"):
        HistoricalDataPage(selected, (bar(3),), source)
    with pytest.raises(DomainValidationError, match="partial page must contain bars"):
        HistoricalDataPage(selected, (), source, "next")
    with pytest.raises(DomainValidationError, match="source_id"):
        HistoricalDataPage(selected, (bar(0),), HistoricalSource("memory", "US30_TEST", "other"))


def test_core_contract_has_no_provider_imports() -> None:
    paths = (*Path("src/market_analysis/domain").glob("*.py"),
             Path("src/market_analysis/application/historical_bars.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("market_analysis.providers")
                assert not (node.level > 0 and (node.module or "").startswith("providers"))
            elif isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("market_analysis.providers")
                    for alias in node.names
                )


def test_acquisition_consumer_reads_all_pages_without_provider_dependency() -> None:
    source: HistoricalDataSource = InMemoryHistoricalDataSource(
        (bar(2), bar(0), bar(1)), page_size=1,
    )
    assert tuple(iter_historical_bars(source, request())) == (bar(0), bar(1), bar(2))
    assert tuple(iter_historical_bars(source, request(start=START, end=START))) == ()


def test_acquisition_consumer_propagates_provider_failure() -> None:
    source: HistoricalDataSource = InMemoryHistoricalDataSource(
        (bar(0),), error=ProviderError("unavailable"),
    )
    with pytest.raises(ProviderError, match="unavailable"):
        tuple(iter_historical_bars(source, request()))


def test_acquisition_consumer_rejects_cross_page_reordering() -> None:
    class ReorderedSource:
        def get_bars(self, selected: HistoricalDataRequest) -> HistoricalDataPage:
            if selected.page_token is None:
                return HistoricalDataPage(selected, (bar(1),),
                                          HistoricalSource("memory", "US30_TEST", "memory"), "more")
            return HistoricalDataPage(selected, (bar(0),),
                                      HistoricalSource("memory", "US30_TEST", "memory"))

    with pytest.raises(HistoricalDataError, match="ordered across pages"):
        tuple(iter_historical_bars(ReorderedSource(), request()))
