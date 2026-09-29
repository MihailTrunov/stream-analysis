"""Account-scoped OANDA v20 historical candles behind the canonical page contract."""

from __future__ import annotations

import base64
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import quote

import httpx

from market_analysis.domain.historical_data import (
    HistoricalDataPage,
    HistoricalDataRequest,
    HistoricalSource,
    MappingUnavailableError,
    ProviderError,
)
from market_analysis.domain.market_data import Bar, DomainValidationError, Timeframe

_GRANULARITY: Mapping[Timeframe, tuple[str, timedelta]] = {
    Timeframe.M1: ("M1", timedelta(minutes=1)),
    Timeframe.M5: ("M5", timedelta(minutes=5)),
    Timeframe.M15: ("M15", timedelta(minutes=15)),
    Timeframe.H1: ("H1", timedelta(hours=1)),
    Timeframe.D1: ("D", timedelta(days=1)),
}
_WINDOW_CANDLES = 4997  # Leave room for inclusive/unaligned boundary candles.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_BASE_URLS = {
    "live": "https://api-fxtrade.oanda.com",
    "practice": "https://api-fxpractice.oanda.com",
}
SOURCE_ID = "oanda-midpoint"


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProviderError("OANDA candle time must be RFC3339 UTC")
    fraction = value.partition(".")[2].removesuffix("Z")
    if fraction and (
        not fraction.isdigit()
        or (len(fraction) > 6 and any(digit != "0" for digit in fraction[6:]))
    ):
        raise ProviderError("OANDA candle time exceeds canonical precision")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderError("invalid OANDA candle time") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ProviderError("OANDA candle time must be UTC")
    return parsed.astimezone(UTC)


def _scope(request: HistoricalDataRequest, symbol: str, environment: str) -> str:
    identity = (
        request.instrument.instrument_id,
        request.timeframe.value,
        _utc_text(request.start),
        _utc_text(request.end),
        symbol,
        environment,
        SOURCE_ID,
    )
    return sha256(repr(identity).encode()).hexdigest()[:24]


def _encode_token(scope: str, cursor: datetime, overlap: Bar | None) -> str:
    payload = {
        "scope": scope,
        "cursor": _utc_text(cursor),
        "overlap": dict(overlap.to_canonical_dict()) if overlap is not None else None,
    }
    return (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode_token(
    token: str,
    scope: str,
    request: HistoricalDataRequest,
    span: timedelta,
    interval: timedelta,
) -> tuple[datetime, Bar | None]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("scope") != scope:
            raise ValueError("scope mismatch")
        cursor = _timestamp(payload["cursor"])
        if not request.start < cursor < request.end or (cursor - request.start) % span:
            raise ValueError("invalid cursor")
        overlap_payload = payload.get("overlap")
        if overlap_payload is not None and not isinstance(overlap_payload, dict):
            raise ValueError("invalid overlap")
        overlap = Bar.from_canonical_dict(overlap_payload) if overlap_payload else None
        if overlap is not None and (
            overlap.instrument_id != request.instrument.instrument_id
            or overlap.timeframe != request.timeframe
            or overlap.source_id != SOURCE_ID
            or not cursor - interval <= overlap.timestamp < cursor
        ):
            raise ValueError("invalid overlap")
        return cursor, overlap
    except (ValueError, KeyError, TypeError, DomainValidationError, ProviderError) as exc:
        raise ProviderError("invalid OANDA page token") from exc


@dataclass(slots=True)
class OandaHistoricalDataSource:
    """Fetch one canonical page; callers own iteration and dataset publication."""

    account_id: str
    token: str = field(repr=False)
    environment: str = "live"
    client: httpx.Client | None = field(default=None, repr=False)
    timeout_seconds: float = 20.0
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)
    _owns_client: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError("account_id must be non-empty")
        if not isinstance(self.token, str) or not self.token.strip():
            raise ValueError("token must be non-empty")
        if self.environment not in _BASE_URLS:
            raise ValueError("environment must be live or practice")
        if self.timeout_seconds <= 0 or self.max_attempts < 1 or self.backoff_seconds < 0:
            raise ValueError("invalid timeout or retry settings")
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout_seconds)

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def __enter__(self) -> OandaHistoricalDataSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_bars(self, request: HistoricalDataRequest) -> HistoricalDataPage:
        try:
            symbol = request.instrument.provider_symbol("oanda", environment=self.environment)
        except KeyError as exc:
            raise MappingUnavailableError(str(exc)) from exc
        source = HistoricalSource("oanda", symbol, SOURCE_ID, self.environment)
        if request.start == request.end:
            if request.page_token is not None:
                raise ProviderError("invalid OANDA page token")
            return HistoricalDataPage(request, (), source)

        granularity, interval = _GRANULARITY[request.timeframe]
        span = interval * _WINDOW_CANDLES
        scope = _scope(request, symbol, self.environment)
        cursor, overlap = (
            (request.start, None)
            if request.page_token is None
            else _decode_token(request.page_token, scope, request, span, interval)
        )
        while cursor < request.end:
            window_end = min(cursor + span, request.end)
            query_from = overlap.timestamp if overlap is not None else cursor
            payload = self._fetch(symbol, granularity, query_from, window_end)
            bars = self._normalize(
                payload, request, symbol, granularity, query_from, cursor, window_end, overlap
            )
            cursor = window_end
            overlap = (
                bars[-1]
                if bars
                and cursor < request.end
                and (cursor - interval <= bars[-1].timestamp < cursor)
                else None
            )
            if bars:
                next_token = _encode_token(scope, cursor, overlap) if cursor < request.end else None
                return HistoricalDataPage(request, bars, source, next_token)
        return HistoricalDataPage(request, (), source)

    def _fetch(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> Mapping[str, Any]:
        url = (
            f"{_BASE_URLS[self.environment]}/v3/accounts/{quote(self.account_id, safe='')}"
            f"/instruments/{quote(symbol, safe='')}/candles"
        )
        params = {
            "price": "M",
            "granularity": granularity,
            "from": _utc_text(start),
            "to": _utc_text(end),
            "smooth": "false",
            "includeFirst": "true",
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }
        assert self.client is not None
        for attempt in range(self.max_attempts):
            try:
                response = self.client.get(
                    url, params=params, headers=headers, timeout=self.timeout_seconds
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt + 1 == self.max_attempts:
                    raise ProviderError("OANDA transport failure after retries") from exc
                self.sleeper(self.backoff_seconds * 2**attempt)
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    parsed_delay = (
                        float(retry_after) if retry_after else self.backoff_seconds * 2**attempt
                    )
                    delay = (
                        min(max(parsed_delay, 0), 30)
                        if math.isfinite(parsed_delay)
                        else self.backoff_seconds * 2**attempt
                    )
                except ValueError:
                    delay = self.backoff_seconds * 2**attempt
                self.sleeper(delay)
                continue
            if response.status_code != 200:
                raise ProviderError(f"OANDA candle request failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError("invalid OANDA candle response JSON") from exc
            if not isinstance(payload, dict):
                raise ProviderError("invalid OANDA candle response")
            return payload
        raise AssertionError("unreachable retry state")

    @staticmethod
    def _normalize(
        payload: Mapping[str, Any],
        request: HistoricalDataRequest,
        symbol: str,
        granularity: str,
        query_from: datetime,
        cursor: datetime,
        window_end: datetime,
        overlap: Bar | None,
    ) -> tuple[Bar, ...]:
        if payload.get("instrument") != symbol or payload.get("granularity") != granularity:
            raise ProviderError("OANDA candle response identity mismatch")
        candles = payload.get("candles")
        if not isinstance(candles, list) or len(candles) > 5000:
            raise ProviderError("invalid OANDA candle list")
        seen: dict[datetime, Bar] = {}
        previous: datetime | None = None
        for candle in candles:
            if not isinstance(candle, dict) or not isinstance(candle.get("complete"), bool):
                raise ProviderError("malformed OANDA candle")
            timestamp = _timestamp(candle.get("time"))
            if previous is not None and timestamp < previous:
                raise ProviderError("OANDA candles are out of order")
            previous = timestamp
            if not query_from <= timestamp <= window_end:
                raise ProviderError("OANDA candle outside requested window")
            if not candle["complete"] or timestamp >= window_end:
                continue
            mid = candle.get("mid")
            volume = candle.get("volume")
            if (
                not isinstance(mid, dict)
                or not isinstance(volume, int)
                or isinstance(volume, bool)
                or volume < 0
            ):
                raise ProviderError("malformed OANDA midpoint candle")
            try:
                bar = Bar(
                    request.instrument.instrument_id,
                    request.timeframe,
                    timestamp,
                    mid["o"],
                    mid["h"],
                    mid["l"],
                    mid["c"],
                    Decimal(volume),
                    source_id=SOURCE_ID,
                )
            except (KeyError, DomainValidationError) as exc:
                raise ProviderError("malformed OANDA midpoint candle") from exc
            prior = seen.get(timestamp)
            if prior is not None and prior != bar:
                raise ProviderError("conflicting OANDA candles at one timestamp")
            seen[timestamp] = bar
        if overlap is not None and overlap.timestamp in seen and seen[overlap.timestamp] != overlap:
            raise ProviderError("conflicting OANDA candles across pages")
        return tuple(bar for timestamp, bar in seen.items() if cursor <= timestamp < request.end)
