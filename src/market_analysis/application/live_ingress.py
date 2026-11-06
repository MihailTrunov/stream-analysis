"""Shared, close-confirmed feed acquisition independent of browser polling/rendering.

This layer owns acquisition only. Analytical sessions consume its durable confirmed
prefix; it must not create detector runs with an incomplete/pinned-history lineage.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event, RLock, Thread
from typing import Any

from sqlalchemy import Engine, create_engine

from market_analysis.config.oanda_uk_calendars import build_oanda_uk_instrument
from market_analysis.domain import HistoricalDataRequest, Timeframe
from market_analysis.domain.live_data import LiveCandleSource
from market_analysis.persistence.market_data import register_instrument
from market_analysis.providers.oanda import OandaHistoricalDataSource

from .history_ranges import HistoryFeed, stamp
from .live_retention import (
    LiveRetentionConflict,
    feed_key,
    read_live_history,
    retain_completed_minute,
)

MINUTE = timedelta(minutes=1)


def _engine() -> Engine:
    url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if not url:
        raise ValueError("database is not configured")
    return create_engine(url)


def _source(feed: HistoryFeed) -> LiveCandleSource:
    token = os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN") or ""
    account = os.getenv("OANDA_ACCOUNT") or ""
    if (not token or sha256(account.encode()).hexdigest() != feed.account_fingerprint
        or (os.getenv("OANDA_ENV") or "live").lower() != feed.environment
        or (os.getenv("OANDA_REGION") or "UK").upper() != "UK"):
        raise ValueError("live provider configuration is unavailable or changed")
    source = OandaHistoricalDataSource(account, token, feed.environment)
    try:
        source.verify_account_instrument(feed.provider_symbol)
    except Exception:
        source.close()
        raise
    return source


@dataclass
class FeedChannel:
    feed: HistoryFeed
    start: datetime
    watermark: datetime
    subscribers: set[str] = field(default_factory=set)
    status: str = "connecting"
    detail: str | None = None
    polled_at: datetime | None = None
    forming: dict[str, object] | None = None
    source: LiveCandleSource | None = None
    stopped: Event = field(default_factory=Event)
    lock: RLock = field(default_factory=RLock)
    poll_lock: RLock = field(default_factory=RLock)
    thread: Thread | None = None


class LiveIngress:
    """One connection per feed, max 20 clients, explicit lazy reconnection after restart."""

    def __init__(self, *, engine: Callable[[], Engine] = _engine,
                 source: Callable[[HistoryFeed], LiveCandleSource] = _source,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 threaded: bool = True) -> None:
        self._engine, self._source, self._clock = engine, source, clock
        self._threaded = threaded
        self._lock = RLock()
        self._feeds: dict[str, FeedChannel] = {}
        self._clients: dict[str, tuple[str, datetime]] = {}
        self._listeners: dict[str, Callable[[], None]] = {}

    def listen(self, client_id: str, callback: Callable[[], None]) -> None:
        with self._lock:
            if client_id not in self._clients:
                raise ValueError("live subscription is disconnected")
            self._listeners[client_id] = callback

    def now(self) -> datetime:
        return self._clock().astimezone(UTC)

    @staticmethod
    def _publish(channel: FeedChannel, **values: Any) -> None:
        with channel.lock:
            for key, value in values.items():
                setattr(channel, key, value)

    def subscribe(self, client_id: str, feed: HistoryFeed, start: datetime) -> dict[str, Any]:
        now = self._clock()
        if (start.tzinfo is None or start.second or start.microsecond
            or not timedelta(0) <= now - start <= timedelta(days=1)):
            raise ValueError("live catch-up requires an aligned start within the previous day")
        start = start.astimezone(UTC)
        calendar = feed.calendar()
        cursor = start
        while cursor <= now:
            calendar.derive(cursor)  # Fail before connecting outside verified coverage.
            cursor += MINUTE
        key = feed_key(feed)
        with self._lock:
            previous = self._clients.get(client_id)
            if previous is not None:
                if previous != (key, start):
                    raise ValueError("disconnect the previous subscription before replacing it")
                return self.state(client_id)
            if len(self._clients) >= 20 or key not in self._feeds and len(self._feeds) >= 2:
                raise ValueError("live acquisition capacity reached")
            channel = self._feeds.get(key)
            if channel is not None and start < channel.start:
                raise ValueError("load earlier history separately before joining this shared feed")
            if channel is None:
                engine = self._engine()
                try:
                    with engine.begin() as connection:
                        register_instrument(
                            connection, build_oanda_uk_instrument(feed.provider_symbol),
                        )
                finally:
                    engine.dispose()
                channel = FeedChannel(feed, start, start)
                self._feeds[key] = channel
            channel.subscribers.add(client_id)
            self._clients[client_id] = (key, start)
            if self._threaded and channel.thread is None:
                channel.thread = Thread(target=self._run, args=(key, channel), daemon=True,
                                        name=f"live-{feed.instrument_id}")
                channel.thread.start()
        return self.state(client_id)

    def _run(self, key: str, channel: FeedChannel) -> None:
        try:
            while not channel.stopped.is_set():
                self.poll(key)
                channel.stopped.wait(2)
        finally:
            if channel.source is not None:
                channel.source.close()

    def poll(self, key: str) -> None:
        """Bound one acquisition iteration to one hour; all prices commit before publication."""
        with self._lock:
            channel = self._feeds.get(key)
        if channel is None:
            return
        with channel.poll_lock:
            if channel.stopped.is_set() or channel.status == "conflict":
                return
            now = self._clock().astimezone(UTC)
            start = channel.watermark
            request_start = max(channel.start, start - MINUTE)
            end = min(now.replace(second=0, microsecond=0), request_start + timedelta(hours=1))
            try:
                calendar = channel.feed.calendar()
                # The wall clock/current forming candle also needs verified coverage.
                calendar.derive(now)
                if end < start:
                    raise ValueError("live clock moved behind its confirmed watermark")
                if channel.source is None:
                    channel.source = self._source(channel.feed)
                instrument = build_oanda_uk_instrument(channel.feed.provider_symbol)
                request = HistoricalDataRequest(instrument, Timeframe.M1, request_start, end)
                update = channel.source.poll_live(request, at=now)
                if (update.completed.request != request or update.observed_at != now
                    or update.completed.source.provider != channel.feed.provider
                    or update.completed.source.symbol != channel.feed.provider_symbol
                    or update.completed.source.environment != channel.feed.environment
                    or update.completed.source.source_id != channel.feed.source_id):
                    raise ValueError("live provider response has inconsistent feed/window identity")
                forming = None
                if update.forming is not None:
                    if not calendar.derive(update.forming.timestamp).is_open:
                        raise ValueError("forming candle lies outside the verified open calendar")
                    forming = dict(update.forming.to_canonical_dict())
                engine = self._engine()
                try:
                    present = {bar.timestamp for bar in update.completed.bars}
                    for bar in update.completed.bars:
                        if channel.stopped.is_set():
                            return
                        retain_completed_minute(engine, channel.feed, bar, at=now)
                    cursor = start
                    while cursor < end:
                        if calendar.derive(cursor).is_open and cursor not in present:
                            status = "gap"
                            detail = f"Waiting for completed minute {stamp(cursor)}."
                            break
                        cursor += MINUTE
                    else:
                        status = "catching_up" if end < now.replace(
                            second=0, microsecond=0,
                        ) else "connected"
                        detail = None
                    self._publish(channel, watermark=cursor, polled_at=now, forming=forming,
                                  status=status, detail=detail)
                finally:
                    engine.dispose()
            except LiveRetentionConflict:
                self._publish(channel, status="conflict", forming=None,
                              detail="Completed provider data conflicts with retained history.")
            except Exception:
                # Never serialize raw transport exceptions, URLs, account IDs or tokens.
                self._publish(channel, status="failed", forming=None,
                              detail="Live acquisition failed; confirmed data is retained.")
                if channel.source is not None:
                    channel.source.close()
                    channel.source = None
        with self._lock:
            callbacks = [self._listeners[client] for client in channel.subscribers
                         if client in self._listeners]
        for callback in callbacks:
            callback()

    def state(self, client_id: str) -> dict[str, Any]:
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                raise ValueError("live subscription is disconnected; reconnect explicitly")
            key, start = client
            channel = self._feeds[key]
            subscribers = len(channel.subscribers)
        with channel.lock:
            stale = channel.polled_at is not None and self._clock() - channel.polled_at > timedelta(
                seconds=15,
            )
            return {
                "subscription_id": client_id, "feed_key": key,
                "instrument_id": channel.feed.instrument_id, "start": stamp(start),
                "available_at": stamp(channel.watermark),
                "status": "stale" if stale and channel.status == "connected" else channel.status,
                "detail": channel.detail, "subscribers": subscribers,
                "polled_at": None if channel.polled_at is None else stamp(channel.polled_at),
                "forming": channel.forming,
            }

    def bars(self, client_id: str, start: datetime, end: datetime, limit: int) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("live view limit must be 1..500")
        state = self.state(client_id)
        earliest = datetime.fromisoformat(state["start"])
        watermark = datetime.fromisoformat(state["available_at"])
        if start.tzinfo is None or end.tzinfo is None or start < earliest or end > watermark:
            raise ValueError("live view must stay inside this subscription's confirmed prefix")
        if start == end == watermark:
            return {**state, "bars": [], "missing_minutes": []}
        engine = self._engine()
        try:
            with engine.connect() as connection:
                result = read_live_history(connection, state["feed_key"], start, end,
                                           at=self._clock(), limit=limit)
            return {**result, "available_at": state["available_at"]}
        finally:
            engine.dispose()

    def unsubscribe(self, client_id: str) -> None:
        with self._lock:
            client = self._clients.pop(client_id, None)
            self._listeners.pop(client_id, None)
            if client is None:
                return
            key, _ = client
            channel = self._feeds[key]
            channel.subscribers.discard(client_id)
            if not channel.subscribers:
                channel.stopped.set()
                self._feeds.pop(key)
                if not self._threaded and channel.source is not None:
                    channel.source.close()

    def close(self) -> None:
        with self._lock:
            channels = list(self._feeds.values())
            self._clients.clear()
            self._listeners.clear()
            self._feeds.clear()
            for channel in channels:
                channel.stopped.set()
        for channel in channels:
            if channel.thread is not None:
                channel.thread.join(timeout=2)
            elif channel.source is not None:
                channel.source.close()


live_ingress = LiveIngress()
