from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from market_analysis.application.historical_bars import iter_historical_bars
from market_analysis.domain import (
    HistoricalDataRequest,
    HistoricalDataSource,
    Instrument,
    MappingUnavailableError,
    ProviderError,
    ProviderSymbolMapping,
    Timeframe,
)
from market_analysis.providers.oanda import SOURCE_ID, OandaHistoricalDataSource

START = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def instrument(*, mapped: bool = True) -> Instrument:
    mappings = (ProviderSymbolMapping("oanda", "US30_USD", "live"),) if mapped else ()
    return Instrument("US30", "US 30", "calendar", 1, Decimal("1"), mappings)


def selected(
    *,
    minutes: int = 3,
    mapped: bool = True,
    token: str | None = None,
    timeframe: Timeframe = Timeframe.M1,
) -> HistoricalDataRequest:
    return HistoricalDataRequest(
        instrument(mapped=mapped), timeframe, START, START + timedelta(minutes=minutes), token
    )


def candle(
    minute: int, *, complete: bool = True, close: str = "101", time: str | None = None
) -> dict[str, object]:
    return {
        "time": time
        or (START + timedelta(minutes=minute)).isoformat().replace("+00:00", ".000000000Z"),
        "complete": complete,
        "volume": 12,
        "mid": {"o": "100", "h": "102", "l": "99", "c": close},
    }


def body(
    *candles: dict[str, object], symbol: str = "US30_USD", granularity: str = "M1"
) -> dict[str, object]:
    return {"instrument": symbol, "granularity": granularity, "candles": list(candles)}


def source(
    handler: httpx.MockTransport, *, attempts: int = 3, sleeps: list[float] | None = None
) -> OandaHistoricalDataSource:
    return OandaHistoricalDataSource(
        account_id="test-account",
        token="private-test-token",
        environment="live",
        client=httpx.Client(transport=handler),
        max_attempts=attempts,
        sleeper=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_maps_midpoint_complete_bars_and_half_open_interval() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=body(candle(0), candle(1, complete=False), candle(2), candle(3))
        )

    adapter = source(httpx.MockTransport(handler))
    assert isinstance(adapter, HistoricalDataSource)
    page = adapter.get_bars(selected())
    assert [bar.timestamp for bar in page.bars] == [START, START + timedelta(minutes=2)]
    assert all(bar.source_id == SOURCE_ID and bar.is_complete for bar in page.bars)
    assert page.bars[0].open == 100 and page.bars[0].close == 101
    assert page.bars[0].volume == 12
    assert page.source.provider == "oanda"
    assert page.source.symbol == "US30_USD"
    assert page.source.environment == "live"
    assert page.is_complete
    assert "private-test-token" not in repr(adapter)
    assert calls[0].url.path == "/v3/accounts/test-account/instruments/US30_USD/candles"
    query = parse_qs(calls[0].url.query.decode())
    assert query["price"] == ["M"]
    assert query["granularity"] == ["M1"]
    assert query["smooth"] == ["false"]
    assert query["from"] == ["2026-01-02T14:30:00Z"]
    assert query["to"] == ["2026-01-02T14:33:00Z"]
    assert calls[0].headers["Accept-Datetime-Format"] == "RFC3339"


def test_empty_range_and_missing_mapping_do_not_contact_provider() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body())

    adapter = source(httpx.MockTransport(handler))
    empty = HistoricalDataRequest(instrument(), Timeframe.M1, START, START)
    assert adapter.get_bars(empty).is_complete
    assert adapter.get_bars(empty).bars == ()
    with pytest.raises(MappingUnavailableError):
        adapter.get_bars(selected(mapped=False))
    assert calls == 0


def test_unaligned_start_filters_provider_candle_covering_from() -> None:
    adapter = source(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json=body(candle(0), candle(1)))
        )
    )
    request = HistoricalDataRequest(
        instrument(), Timeframe.M1, START + timedelta(seconds=30),
        START + timedelta(minutes=2),
    )
    page = adapter.get_bars(request)
    assert [bar.timestamp for bar in page.bars] == [START + timedelta(minutes=1)]


def test_sparse_pagination_skips_empty_windows_and_verifies_overlap() -> None:
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        calls.append(query)
        if len(calls) == 1:
            return httpx.Response(200, json=body())
        if len(calls) == 2:
            return httpx.Response(200, json=body(candle(9993)))
        return httpx.Response(200, json=body(candle(9993), candle(9994)))

    adapter = source(httpx.MockTransport(handler))
    first = adapter.get_bars(selected(minutes=10000))
    assert [bar.timestamp for bar in first.bars] == [START + timedelta(minutes=9993)]
    assert first.next_page_token
    second = adapter.get_bars(selected(minutes=10000, token=first.next_page_token))
    assert [bar.timestamp for bar in second.bars] == [START + timedelta(minutes=9994)]
    assert second.is_complete
    assert calls[0]["from"] == ["2026-01-02T14:30:00Z"]
    assert calls[1]["from"] == [
        (START + timedelta(minutes=4997)).isoformat().replace("+00:00", "Z")
    ]
    assert calls[2]["from"] == [
        (START + timedelta(minutes=9993)).isoformat().replace("+00:00", "Z")
    ]


def test_identical_duplicates_collapse_but_conflicting_duplicates_fail() -> None:
    identical = source(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=body(candle(0), candle(0), candle(1)),
            )
        )
    )
    assert len(identical.get_bars(selected()).bars) == 2
    conflicting = source(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=body(candle(0), candle(0, close="100")),
            )
        )
    )
    with pytest.raises(ProviderError, match="conflicting"):
        conflicting.get_bars(selected())


def test_conflicting_overlap_across_pages_fails() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=body(candle(4996)))
        return httpx.Response(200, json=body(candle(4996, close="100"), candle(4997)))

    adapter = source(httpx.MockTransport(handler))
    first = adapter.get_bars(selected(minutes=5000))
    with pytest.raises(ProviderError, match="across pages"):
        adapter.get_bars(selected(minutes=5000, token=first.next_page_token))


def test_rejects_reused_token_for_other_request_and_malformed_token() -> None:
    adapter = source(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=body(candle(4996)),
            )
        )
    )
    first = adapter.get_bars(selected(minutes=5000))
    with pytest.raises(ProviderError, match="page token"):
        adapter.get_bars(selected(minutes=5001, token=first.next_page_token))
    with pytest.raises(ProviderError, match="page token"):
        adapter.get_bars(selected(minutes=5000, token="not-a-token"))


@pytest.mark.parametrize(
    "bad_response",
    [
        body(candle(1), candle(0)),
        body(candle(0), symbol="DE30_EUR"),
        body(candle(0), granularity="M5"),
        body({"time": "2026-01-02T14:30:00Z", "complete": True, "mid": {}}),
        body(candle(0, time="2026-01-02T14:30:00.000000001Z")),
        body({**candle(0), "mid": {"o": "100", "h": "99", "l": "98", "c": "101"}}),
        {"instrument": "US30_USD", "granularity": "M1", "candles": "bad"},
    ],
)
def test_malformed_provider_response_fails(bad_response: dict[str, object]) -> None:
    adapter = source(httpx.MockTransport(lambda _: httpx.Response(200, json=bad_response)))
    with pytest.raises(ProviderError):
        adapter.get_bars(selected())


def test_retry_429_with_retry_after_then_succeed() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=body(candle(0)))

    adapter = source(httpx.MockTransport(handler), sleeps=sleeps)
    assert len(adapter.get_bars(selected()).bars) == 1
    assert calls == 2 and sleeps == [2.0]


def test_retry_timeout_is_bounded_and_non_transient_http_does_not_retry() -> None:
    calls = 0
    sleeps: list[float] = []

    def timeout(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    adapter = source(httpx.MockTransport(timeout), attempts=3, sleeps=sleeps)
    with pytest.raises(ProviderError, match="transport failure"):
        adapter.get_bars(selected())
    assert calls == 3 and sleeps == [0.5, 1.0]

    calls = 0

    def unauthorized(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"errorMessage": "secret"})

    adapter = source(httpx.MockTransport(unauthorized), sleeps=sleeps)
    with pytest.raises(ProviderError, match="HTTP 401") as error:
        adapter.get_bars(selected())
    assert "secret" not in str(error.value)
    assert calls == 1


def test_empty_provider_history_terminates_and_consumer_sees_no_bars() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body())

    adapter = source(httpx.MockTransport(handler))
    assert tuple(iter_historical_bars(adapter, selected(minutes=10000))) == ()
    assert calls == 3


@pytest.mark.parametrize(
    "timeframe,expected",
    [
        (Timeframe.M1, "M1"),
        (Timeframe.M5, "M5"),
        (Timeframe.M15, "M15"),
        (Timeframe.H1, "H1"),
        (Timeframe.D1, "D"),
    ],
)
def test_canonical_timeframes_map_to_oanda_granularity(
    timeframe: Timeframe,
    expected: str,
) -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(parse_qs(request.url.query.decode())["granularity"][0])
        return httpx.Response(200, json=body(granularity=expected))

    adapter = source(httpx.MockTransport(handler))
    adapter.get_bars(selected(timeframe=timeframe))
    assert captured == [expected]
