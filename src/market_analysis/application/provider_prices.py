"""Calendar-independent chart prices, retained separately from validated research inputs."""

from __future__ import annotations

import fcntl
import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from market_analysis.config.oanda_uk_calendars import build_oanda_uk_instrument
from market_analysis.domain import Bar, SessionCalendarError, Timeframe
from market_analysis.domain.aggregation import MINUTES
from market_analysis.providers.oanda import OandaHistoricalDataSource

from .browser_replay import _store
from .history_ranges import HistoryFeed, stamp
from .live_ingress import _source

MINUTE = timedelta(minutes=1)


def price_key(feed: HistoryFeed) -> str:
    identity = feed.model_dump(exclude={"calendar_version"})
    return sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class ProviderPriceStore:
    """Atomic per-UTC-day Parquet cache. No DatasetRevision or calendar validity is implied."""

    def __init__(self, root: Path, feed: HistoryFeed):
        self.root = root / "provider-prices" / price_key(feed)
        self.feed = feed

    def _read(self, path: Path) -> dict[datetime, Bar]:
        if not path.exists():
            return {}
        result = {}
        for row in pq.read_table(path).to_pylist():
            encoded = row["bar_json"]
            if sha256(encoded.encode()).hexdigest() != row["sha256"]:
                raise ValueError("provider price cache checksum differs")
            bar = Bar.from_canonical_dict(json.loads(encoded))
            if (bar.instrument_id != self.feed.instrument_id or bar.source_id != self.feed.source_id
                or bar.timeframe is not Timeframe.M1 or not bar.is_complete
                or bar.timestamp.second or bar.timestamp.microsecond
                or path.stem != bar.timestamp.date().isoformat() or bar.timestamp in result):
                raise ValueError("provider price cache identity differs")
            result[bar.timestamp] = bar
        return result

    def retain(self, bars: tuple[Bar, ...]) -> None:
        if not bars:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        days: dict[str, list[Bar]] = defaultdict(list)
        for bar in bars:
            if (bar.instrument_id != self.feed.instrument_id or bar.source_id != self.feed.source_id
                or bar.timeframe is not Timeframe.M1 or not bar.is_complete
                or bar.timestamp.second or bar.timestamp.microsecond):
                raise ValueError("provider cache accepts completed canonical M1 only")
            days[bar.timestamp.date().isoformat()].append(bar)
        # Processes sharing the data root serialize read/merge/replace too.
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for day, incoming in days.items():
                path = self.root / f"{day}.parquet"
                retained = self._read(path)
                changed = False
                for bar in incoming:
                    previous = retained.get(bar.timestamp)
                    if previous is not None and previous != bar:
                        raise ValueError("completed provider price conflicts with retained data")
                    changed |= previous is None
                    retained[bar.timestamp] = bar
                if not changed:
                    continue
                rows = []
                for bar in sorted(retained.values(), key=lambda b: b.timestamp):
                    encoded = json.dumps(dict(bar.to_canonical_dict()), sort_keys=True)
                    rows.append({"bar_json": encoded,
                                 "sha256": sha256(encoded.encode()).hexdigest()})
                temporary = path.with_suffix(f".{uuid4().hex}.tmp")
                try:
                    pq.write_table(pa.Table.from_pylist(rows), temporary)
                    with temporary.open("rb") as handle:
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                    descriptor = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                finally:
                    temporary.unlink(missing_ok=True)

    def read(self, end: datetime, count: int = 1440) -> tuple[Bar, ...]:
        bars: list[Bar] = []
        for path in sorted(self.root.glob("*.parquet"), reverse=True):
            if path.stem > end.date().isoformat():
                continue
            bars[:0] = sorted((bar for bar in self._read(path).values() if bar.timestamp < end),
                              key=lambda b: b.timestamp)
            if len(bars) >= count:
                break
        return tuple(bars[-count:])


def display_prices(bars: tuple[Bar, ...], feed: HistoryFeed, timeframe: Timeframe,
                   end: datetime) -> list[dict[str, object]]:
    """Aggregate M1 for display only; unknown calendar gaps never become validated candles."""
    if timeframe is Timeframe.M1:
        return [{**dict(bar.to_canonical_dict()), "available_at": stamp(bar.timestamp + MINUTE)}
                for bar in bars]
    calendar = feed.calendar()  # Read alignment metadata, never derive open/closed sessions here.
    groups: dict[tuple[datetime, datetime], list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.timestamp.astimezone(ZoneInfo(calendar.timezone_name))
        date = local.date() - timedelta(days=local.time().replace(tzinfo=None)
                                       < calendar.trading_day_boundary)
        left = datetime.combine(date, calendar.trading_day_boundary, local.tzinfo).astimezone(UTC)
        right = datetime.combine(date + timedelta(days=1), calendar.trading_day_boundary,
                                 local.tzinfo).astimezone(UTC)
        if timeframe is not Timeframe.D1:
            seconds = MINUTES[timeframe] * 60
            start = datetime.fromtimestamp(int(bar.timestamp.timestamp()) // seconds * seconds, UTC)
            left, right = max(left, start), min(right, start + timedelta(seconds=seconds))
        groups[left, right].append(bar)
    result = []
    for (left, right), values in sorted(groups.items()):
        observed = min(right, end, values[-1].timestamp + MINUTE)
        result.append({"timestamp": stamp(left), "available_at": stamp(observed),
                       "open": str(values[0].open), "high": str(max(b.high for b in values)),
                       "low": str(min(b.low for b in values)), "close": str(values[-1].close),
                       "is_complete": False, "quality_flags": ["display_only_aggregate"]})
    return result


@dataclass
class PriceChannel:
    feed: HistoryFeed
    store: ProviderPriceStore
    lock: RLock = field(default_factory=RLock)
    source: OandaHistoricalDataSource | None = None
    polled: datetime | None = None
    detail: str | None = None
    stopped: Event = field(default_factory=Event)
    thread: Thread | None = None
    clients: set[str] = field(default_factory=set)
    history_reads: dict[datetime, datetime] = field(default_factory=dict)


class ProviderPrices:
    def __init__(self, *, root: Callable[[], Path] = lambda: _store().root,
                 source: Callable[[HistoryFeed], OandaHistoricalDataSource] =
                 lambda feed: cast(OandaHistoricalDataSource, _source(feed)),
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 threaded: bool = True):
        self.root, self.source, self.clock, self.threaded = root, source, clock, threaded
        self.lock = RLock()
        self.channels: dict[str, PriceChannel] = {}

    def channel(self, feed: HistoryFeed) -> PriceChannel:
        key = price_key(feed)
        with self.lock:
            if key not in self.channels:
                if len(self.channels) >= 2:
                    idle = next((k for k, c in self.channels.items() if not c.clients), None)
                    if idle is None:
                        raise ValueError("provider price capacity reached")
                    old = self.channels.pop(idle)
                    old.stopped.set()
                    with old.lock:
                        if old.source:
                            old.source.close()
                self.channels[key] = PriceChannel(feed, ProviderPriceStore(self.root(), feed))
            return self.channels[key]

    def fetch(self, channel: PriceChannel, end: datetime, count: int) -> None:
        if channel.source is None:
            channel.source = self.source(channel.feed)
        instrument = build_oanda_uk_instrument(channel.feed.provider_symbol)
        page = channel.source.price_window(instrument, end=end, count=count)
        if (page.source.provider != channel.feed.provider
            or page.source.symbol != channel.feed.provider_symbol
            or page.source.environment != channel.feed.environment
            or page.source.source_id != channel.feed.source_id):
            raise ValueError("provider price response identity differs")
        channel.store.retain(page.bars)

    def poll(self, channel: PriceChannel) -> None:
        with channel.lock:
            if channel.stopped.is_set():
                return
            now = self.clock().astimezone(UTC)
            if channel.polled is not None and now - channel.polled < timedelta(seconds=2):
                return
            try:
                # Refresh a full window after outages; frequent polls need only recent closes.
                count = 3 if channel.polled and now - channel.polled < MINUTE else 1440
                self.fetch(channel, now.replace(second=0, microsecond=0), count)
                channel.polled, channel.detail = now, None
            except Exception:
                channel.detail = "Provider price refresh failed; retained prices remain available."
                if channel.source:
                    channel.source.close()
                    channel.source = None

    def window(self, feed: HistoryFeed, timeframe: Timeframe, end: datetime | None = None
               ) -> dict[str, Any]:
        now = self.clock().astimezone(UTC)
        latest = end is None
        end = end or now.replace(second=0, microsecond=0)
        if (end.tzinfo is None or end.second or end.microsecond
            or end > now.replace(second=0, microsecond=0)):
            raise ValueError("price window needs an elapsed, aligned, aware end")
        end = end.astimezone(UTC)
        channel = self.channel(feed)
        with channel.lock:
            detail = channel.detail
            if latest:
                self.poll(channel)
                detail = channel.detail
            else:
                previous = channel.history_reads.get(end)
                if previous is None or end > now - timedelta(minutes=5) and (
                    now - previous >= timedelta(seconds=2)
                ):
                    try:
                        self.fetch(channel, end, 1440)
                        channel.history_reads.pop(end, None)
                        channel.history_reads[end] = now
                        if len(channel.history_reads) > 16:
                            del channel.history_reads[next(iter(channel.history_reads))]
                        detail = None
                    except Exception:
                        detail = ("Provider history refresh failed; "
                                  "retained prices remain available.")
                        if channel.source:
                            channel.source.close()
                            channel.source = None
            bars = channel.store.read(end)
            if not bars and detail:
                raise ValueError(detail)
            covered = True
            try:
                for instant in (bars[0].timestamp if bars else end, end - MINUTE, now if latest
                                else end - MINUTE):
                    feed.calendar().derive(instant)
            except SessionCalendarError:
                covered = False
            return {"instrument_id": feed.instrument_id, "timeframe": timeframe.value,
                    "end": stamp(end), "calendar_status": "covered" if covered else "unverified",
                    "analysis_ready": False, "aggregation": "display_only",
                    "detail": detail, "bars": display_prices(bars, feed, timeframe, end)}

    def subscribe(self, client: str, feed: HistoryFeed) -> None:
        with self.lock:
            if any(client in c.clients and c.feed != feed for c in self.channels.values()):
                raise ValueError("disconnect before replacing a price subscription")
            if any(client in c.clients for c in self.channels.values()):
                return
            if sum(len(c.clients) for c in self.channels.values()) >= 20:
                raise ValueError("provider price subscription capacity reached")
            channel = self.channel(feed)
            channel.clients.add(client)
            if self.threaded and channel.thread is None:
                channel.stopped = Event()
                channel.thread = Thread(target=self._run, args=(channel,), daemon=True)
                channel.thread.start()

    def _run(self, channel: PriceChannel) -> None:
        while not channel.stopped.is_set():
            if channel.clients:
                self.poll(channel)
            channel.stopped.wait(2)

    def unsubscribe(self, client: str) -> None:
        with self.lock:
            for channel in self.channels.values():
                channel.clients.discard(client)

    def close(self) -> None:
        with self.lock:
            for channel in self.channels.values():
                channel.stopped.set()
            for channel in self.channels.values():
                if channel.thread:
                    channel.thread.join(timeout=25)
                with channel.lock:
                    if channel.source:
                        channel.source.close()
            self.channels.clear()


provider_prices = ProviderPrices()
