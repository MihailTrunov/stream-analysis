"""Independent live detector sessions over immutable bootstrap and journal receipts."""

from __future__ import annotations

import json
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from threading import RLock
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from market_analysis.config import resolve_detection_config
from market_analysis.domain import Bar, DatasetLineage, Timeframe, ValidationStatus
from market_analysis.domain.aggregation import MINUTE, bucket_bounds
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION
from market_analysis.persistence.history_ranges import utc
from market_analysis.persistence.live_analysis import live_analysis_advances, live_analysis_closes
from market_analysis.persistence.live_candles import live_candles
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    load_dataset_lineage,
    load_dataset_revision,
)

from .analysis_clock import verify_closed_interval
from .analysis_sessions import (
    AnalysisRequest,
    AnalysisSessionManager,
    StreamSettings,
    preview_configuration,
)
from .browser_replay import _store
from .history_inputs import read_history_input
from .history_navigation import plan_history_navigation, verified_history_bounds
from .history_prices import completed_source_window
from .history_ranges import HistoryFeed, stamp
from .live_ingress import LiveIngress, live_ingress
from .live_retention import _decode, feed_key
from .replay_catalog import PATTERN_DEFINITIONS, required_warmup_bars


class LiveAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subscription_id: UUID
    bootstrap_revision_id: str | None = None
    streams: tuple[StreamSettings, ...] = Field(min_length=1, max_length=7)
    chart_timeframe: Timeframe = Timeframe.M1
    calendar_mode: Literal['verified', 'provider_observed'] = 'verified'


@dataclass
class AttachedSession:
    request: LiveAnalysisRequest
    session_id: str
    input_id: str
    feed: HistoryFeed
    failure: str | None = None
    snapshots: OrderedDict[tuple[datetime, datetime], dict[str, Any]] = field(
        default_factory=OrderedDict,
    )


class LiveAnalysisManager:
    def __init__(self, ingress: LiveIngress) -> None:
        self.ingress = ingress
        self._analysis = AnalysisSessionManager()
        self._lock = RLock()
        self._sessions: dict[str, AttachedSession] = {}
        self._cancelled: set[str] = set()

    def prepare(self, request: LiveAnalysisRequest, feed: HistoryFeed) -> dict[str, Any]:
        with self._lock:
            client = str(request.subscription_id)
            if client in self._cancelled:
                raise ValueError("this connection was disconnected; reconnect with a new identity")
            previous = self._sessions.get(client)
            if previous is not None:
                if previous.feed != feed or previous.request.streams != request.streams:
                    raise ValueError("disconnect before replacing live analytical settings")
                return {"state": self.state(client)}
            now = self.ingress.now()
            calendar = feed.calendar()
            if calendar.observed_only:
                from .provider_analysis import publish_provider_source
                from .provider_prices import provider_prices

                engine = self._analysis.engine()
                try:
                    return {'source': publish_provider_source(
                        engine, provider_prices, feed, live=True)}
                finally:
                    engine.dispose()
            verified_start, verified_end = verified_history_bounds(feed)
            end = min(now.replace(second=0, microsecond=0), verified_end)
            if end <= verified_start:
                raise ValueError("no completed verified history is available")
            last = completed_source_window(calendar, Timeframe.M1, verified_start, end, 1)
            if last is None:
                raise ValueError("no completed verified history is available")
            if now >= verified_end:
                status = "calendar_unverified"
                detail = (
                    "Detector monitoring is unavailable because the trading calendar does not "
                    "cover the current date. Provider prices load independently."
                )
            else:
                status = "ready" if calendar.derive(now).is_open else "market_closed"
                detail = ("Market closed; showing the latest completed candles."
                          if status == "market_closed" else "")
            engine = self._analysis.engine()
            try:
                with engine.begin() as connection:
                    plan = plan_history_navigation(
                        connection,
                        feed,
                        request.bootstrap_revision_id,
                        last[0],
                        Timeframe.M1,
                        request.streams,
                        "replay",
                        at=now,
                        end=end,
                        # The completed target candle is included in addition to this prefix.
                        price_bars=1440 - 1,
                    )
                    plan["live_status"] = status
                    plan["live_detail"] = detail
                    return {"plan": plan}
            finally:
                engine.dispose()

    def connect(self, request: LiveAnalysisRequest, feed: HistoryFeed) -> dict[str, Any]:
        client = str(request.subscription_id)
        with self._lock:
            if client in self._cancelled:
                raise ValueError("this connection was disconnected; reconnect with a new identity")
            existing = self._sessions.get(client)
            if existing:
                if (
                    existing.request.model_dump(exclude={"chart_timeframe"})
                    != request.model_dump(exclude={"chart_timeframe"})
                    or existing.feed != feed
                ):
                    raise ValueError("disconnect or apply settings before replacing a live session")
                return self.state(client)
            if len(self._sessions) >= 20:
                raise ValueError("stop a live analysis session before opening another")
            if request.bootstrap_revision_id is None:
                raise ValueError("prepare recent provider history before connecting live")
            engine = self._analysis.engine()
            try:
                with engine.connect() as connection:
                    store = _store()
                    sequence = store.load_sequence(
                        connection, request.bootstrap_revision_id, feed.instrument_id, Timeframe.M1
                    )
                    revision = load_dataset_revision(connection, request.bootstrap_revision_id)
                    if (
                        revision is None
                        or revision.calendar_version != feed.calendar_version
                        or revision.normalization_version != feed.normalization_version
                    ):
                        raise ValueError("live bootstrap has no matching calendar or normalization")
                    provenance = json.loads(sequence.lineage.provider_request_json)
                    if revision.provider == "history-composite":
                        verified = read_history_input(
                            connection, store, request.bootstrap_revision_id
                        )
                        same_feed = verified["source_manifest"]["feed"] == feed.model_dump()
                    else:
                        same_feed = revision.provider == feed.provider and all(
                            provenance.get(name) == value
                            for name, value in {
                                "provider": feed.provider,
                                "environment": feed.environment,
                                "symbol": feed.provider_symbol,
                                "source_id": feed.source_id,
                                "account_fingerprint": feed.account_fingerprint,
                                "price_component": feed.price_component,
                                "smooth": feed.smooth,
                            }.items()
                        )
                    if not same_feed or not sequence.bars:
                        raise ValueError("live bootstrap must belong to this exact provider feed")
                    start = sequence.lineage.requested_end
                    now = self.ingress.now()
                    # Validate current coverage before starting any runtime or connection.
                    feed.calendar().derive(now)
                    if not timedelta(0) <= now - start <= timedelta(days=1):
                        raise ValueError("prepare recent provider history before connecting live")
                    manifest = {
                        "version": "live-analysis-input-v1",
                        "feed": feed.model_dump(),
                        "bootstrap_revision_id": request.bootstrap_revision_id,
                        "bootstrap_checksum": sequence.lineage.canonical_checksum,
                        "live_start": stamp(start),
                        "initialization_origin": stamp(sequence.bars[0].timestamp),
                    }
                # Manual detectors are requested through historical calculation, not live dispatch.
                streams_list = []
                for settings in request.streams:
                    if settings.config.calendar_id != feed.calendar().calendar_id or set(
                        settings.policies
                    ) - {pattern.pattern_id for pattern in settings.config.patterns}:
                        raise ValueError("live settings differ from the feed or detector selection")
                    preview_configuration(settings.config)
                    patterns = tuple(
                        pattern
                        for pattern in settings.config.patterns
                        if settings.policies.get(pattern.pattern_id, "live") != "manual"
                    )
                    streams_list.append(
                        settings.model_copy(
                            update={
                                "config": settings.config.model_copy(update={"patterns": patterns}),
                                "policies": {
                                    key: value
                                    for key, value in settings.policies.items()
                                    if key in {pattern.pattern_id for pattern in patterns}
                                },
                            }
                        )
                    )
                streams = tuple(streams_list)
                bootstrap = AnalysisRequest(
                    dataset_revision_id=request.bootstrap_revision_id,
                    instrument_id=feed.instrument_id,
                    selected_start=sequence.bars[-1].timestamp,
                    selected_end=sequence.lineage.requested_end,
                    streams=streams,
                    chart_timeframe=request.chart_timeframe,
                )
                input_id = f"live-input-{uuid4()}"
                initialized = self._analysis.launch(
                    bootstrap, source_input_id=input_id, source_input_manifest=manifest
                )
                attached = AttachedSession(request, initialized["run_id"], input_id, feed)
                initialized_session = self._analysis.current(attached.session_id)
                initialized_session.bars = list(initialized_session.bars)
                try:
                    self.ingress.subscribe(client, feed, start)
                    self._sessions[client] = attached
                    self.ingress.listen(client, lambda: self.advance(client))
                    self.advance(client)
                except Exception:
                    self.ingress.unsubscribe(client)
                    self._sessions.pop(client, None)
                    self._analysis.command(attached.session_id, "stop")
                    raise
                return self.state(client)
            finally:
                engine.dispose()

    def advance(self, client: str) -> None:
        """Called by ingestion, including while every browser tab is hidden."""
        with self._lock:
            attached = self._sessions.get(client)
            if attached is None or attached.failure:
                return
            state = self.ingress.state(client)
            if state["status"] in {"failed", "conflict"}:
                return
            session = self._analysis.current(attached.session_id)
            target = datetime.fromisoformat(state["available_at"])
            engine = self._analysis.engine()
            previous = (
                session.index,
                session.watermark,
                len(session.bars),
                {tf: len(candles) for tf, candles in session.candles.items()},
                [
                    (len(stream.frames), len(stream.events), stream.processed)
                    for stream in session.streams
                ],
            )
            try:
                with engine.begin() as connection:
                    # A shared acquisition can still have been behind the new seed at
                    # attachment. Reconcile its newly captured overlap before advancing.
                    bootstrap_end = session.request.selected_end
                    for row in connection.execute(
                        select(live_candles).where(
                            live_candles.c.feed_key == feed_key(attached.feed),
                            live_candles.c.timestamp >= session.bars[0].timestamp,
                            live_candles.c.timestamp < bootstrap_end,
                        )
                    ).mappings():
                        retained = _decode(row)
                        index = bisect_left(
                            session.bars, retained.timestamp, key=lambda bar: bar.timestamp
                        )
                        if index == len(session.bars) or session.bars[index] != retained:
                            raise ValueError("live bootstrap conflicts with retained feed")
                    if target <= session.watermark:
                        return
                    rows = (
                        connection.execute(
                            select(live_candles)
                            .where(
                                live_candles.c.feed_key == feed_key(attached.feed),
                                live_candles.c.timestamp >= session.watermark,
                                live_candles.c.timestamp < target,
                            )
                            .order_by(live_candles.c.timestamp)
                        )
                        .mappings()
                        .all()
                    )
                    for row in rows:
                        bar = _decode(row)
                        session.advance_closed(connection, bar.timestamp, mode="live")
                        candles = [
                            candle
                            for aggregator in session.aggregators.values()
                            for candle in aggregator.push(bar)
                        ]
                        session.dispatch(connection, candles, mode="live")
                        cast(list[Bar], session.bars).append(bar)
                        session.index += 1
                        session.revealed_at = bar.timestamp + MINUTE
                        connection.execute(
                            live_analysis_closes.insert().values(
                                input_id=attached.input_id,
                                timestamp=bar.timestamp,
                                feed_key=row["feed_key"],
                                sha256=row["sha256"],
                            )
                        )
                    session.advance_closed(connection, target, mode="live")
                    connection.execute(
                        live_analysis_advances.insert().values(
                            input_id=attached.input_id,
                            available_at=target,
                        )
                    )
            except Exception:
                # A mutated runtime cannot be retried after its database transaction rolls back.
                index, watermark, length, candle_lengths, stream_lengths = previous
                session.index = index
                session.revealed_at = watermark
                del cast(list[Bar], session.bars)[length:]
                for tf, count in candle_lengths.items():
                    del session.candles[tf][count:]
                for stream, (frame_count, event_count, processed) in zip(
                    session.streams,
                    stream_lengths,
                    strict=True,
                ):
                    del stream.frames[frame_count:]
                    del stream.events[event_count:]
                    stream.processed = processed
                attached.failure = "Live analysis stopped; reinitialize from retained history."
                session.status = "failed"
            finally:
                engine.dispose()

    def state(self, client: str) -> dict[str, Any]:
        with self._lock:
            attached = self._sessions.get(client)
            if attached is None:
                raise ValueError("live analysis session is disconnected")
            session = self._analysis.current(attached.session_id)
            feed = self.ingress.state(client)
            return {
                "subscription_id": client,
                "run_id": session.session_id,
                "input_id": attached.input_id,
                "bootstrap_revision_id": attached.request.bootstrap_revision_id,
                "instrument_id": attached.feed.instrument_id,
                "streams": [item.model_dump(mode="json") for item in attached.request.streams],
                "available_at": stamp(session.watermark),
                "history_start": stamp(session.bars[0].timestamp),
                "status": "failed" if attached.failure else feed["status"],
                "detail": attached.failure or feed["detail"],
                "forming": feed["forming"],
                "cursor_index": session.index,
                "initialization": [
                    {
                        "run_id": stream.runtime.run_id,
                        "timeframe": stream.runtime.config.timeframe.value,
                        "processed": stream.processed,
                        "required": required_warmup_bars(stream.runtime.config),
                        "pattern_id": stream.runtime.config.patterns[0].pattern_id,
                    }
                    for stream in session.streams
                ],
            }

    def view(
        self,
        client: str,
        timeframe: Timeframe,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        if end is not None and end.tzinfo is None:
            raise ValueError("live view requires an aware endpoint")
        with self._lock:
            state = self.state(client)
            attached = self._sessions[client]
            session = self._analysis.current(attached.session_id)
            finish = min(end, session.watermark) if end is not None else session.watermark
            if finish <= session.bars[0].timestamp:
                raise ValueError("view precedes initialized live history")
            view = self._analysis.view(
                session.session_id, timeframe, session.bars[0].timestamp, finish, limit=1440
            )
            # Forming display aggregation never enters the analytical aggregator/runtime.
            state["forming"] = None
            current = self.ingress.state(client)["forming"]
            if current is not None and end is None and state["status"] == "connected":
                forming = Bar.from_canonical_dict(current)
                if forming.timestamp == session.watermark:
                    left, _ = bucket_bounds(forming.timestamp, timeframe, session.calendar)
                    prefix = [
                        bar
                        for bar in session.bars[: session.index + 1]
                        if left <= bar.timestamp < forming.timestamp
                    ]
                    try:
                        verify_closed_interval(
                            session.calendar,
                            left,
                            prefix[0].timestamp if prefix else forming.timestamp,
                        )
                        complete_context = True
                    except ValueError:
                        complete_context = False
                    if complete_context:
                        combined = [*prefix, forming]
                        state["forming"] = {
                            "timestamp": stamp(left),
                            "open": str(combined[0].open),
                            "high": str(max(bar.high for bar in combined)),
                            "low": str(min(bar.low for bar in combined)),
                            "close": str(forming.close),
                            "is_complete": False,
                        }
            return {**view, "live": state}

    def disconnect(self, client: str) -> None:
        with self._lock:
            self._cancelled.add(client)
            attached = self._sessions.pop(client, None)
            if attached:
                self.ingress.unsubscribe(client)
                self._analysis.command(attached.session_id, "stop")

    def snapshot(self, client: str, start: datetime, end: datetime) -> dict[str, Any]:
        """Freeze bounded viewed history plus initialization for requested historical detectors."""
        with self._lock:
            attached = self._sessions.get(client)
            if attached is None:
                raise ValueError("live analysis session is disconnected")
            session = self._analysis.current(attached.session_id)
            if (
                start.tzinfo is None
                or end.tzinfo is None
                or start.second
                or start.microsecond
                or end.second
                or end.microsecond
                or not session.bars[0].timestamp <= start < end <= session.watermark
            ):
                raise ValueError("historical calculation must use completed live input bounds")
            first = start
            for settings in attached.request.streams:
                required = required_warmup_bars(
                    resolve_detection_config(
                        settings.config,
                        pattern_definitions=PATTERN_DEFINITIONS,
                    )
                )
                if required:
                    window = completed_source_window(
                        session.calendar,
                        settings.config.timeframe,
                        session.bars[0].timestamp,
                        start,
                        required,
                    )
                    first = min(first, window[0] if window else session.bars[0].timestamp)
            key = first, end
            if key in attached.snapshots:
                attached.snapshots.move_to_end(key)
                return attached.snapshots[key]
            bars = tuple(
                bar
                for bar in session.bars
                if first <= bar.timestamp and bar.timestamp + MINUTE <= end
            )
            if not bars:
                raise ValueError("no completed source candles in this historical view")
            checksum = canonical_bar_checksum_ordered(bars)
            recipe = {
                "version": "live-capture-v1",
                "input_id": attached.input_id,
                "feed": attached.feed.model_dump(),
                "start": stamp(first),
                "end": stamp(end),
                "canonical_checksum": checksum,
            }
            encoded = json.dumps(recipe, sort_keys=True, separators=(",", ":"))
            source_checksum = sha256(encoded.encode()).hexdigest()
            revision_id = f"live-capture-{source_checksum}"
            engine = self._analysis.engine()
            try:
                with engine.begin() as connection:
                    bootstrap_id = attached.request.bootstrap_revision_id
                    if bootstrap_id is None:
                        raise ValueError("live bootstrap identity is unavailable")
                    bootstrap = load_dataset_lineage(
                        connection,
                        bootstrap_id,
                        attached.feed.instrument_id,
                        Timeframe.M1,
                    )
                    if bootstrap is None:
                        raise ValueError("live bootstrap lineage is unavailable")
                    last_received = connection.scalar(
                        select(
                            func.max(
                                live_candles.c.recorded_at,
                            )
                        )
                        .join(
                            live_analysis_closes,
                            (live_analysis_closes.c.feed_key == live_candles.c.feed_key)
                            & (live_analysis_closes.c.timestamp == live_candles.c.timestamp),
                        )
                        .where(
                            live_analysis_closes.c.input_id == attached.input_id,
                            live_analysis_closes.c.timestamp >= first,
                            live_analysis_closes.c.timestamp < end,
                        )
                    )
                    acquired = bootstrap.acquired_at
                    if last_received is not None:
                        acquired = max(acquired, utc(last_received))
                    revision = DatasetRevision(
                        revision_id,
                        attached.input_id,
                        attached.input_id,
                        "live-capture",
                        acquired,
                        acquired,
                        attached.feed.normalization_version,
                        attached.feed.calendar_version,
                        DATASET_FORMAT_VERSION,
                        (
                            DatasetMembership(
                                attached.feed.instrument_id, Timeframe.M1, first, end, len(bars)
                            ),
                        ),
                        f"datasets/{revision_id}/manifest.json",
                    )
                    lineage = DatasetLineage(
                        revision_id,
                        attached.input_id,
                        attached.feed.instrument_id,
                        Timeframe.M1,
                        first,
                        end,
                        bars[0].timestamp,
                        bars[-1].timestamp + MINUTE,
                        len(bars),
                        acquired,
                        ValidationStatus.WARNING if attached.feed.calendar().observed_only
                        else ValidationStatus.PASS,
                        encoded,
                        source_checksum,
                        checksum,
                        BAR_CHECKSUM_VERSION,
                        DATASET_FORMAT_VERSION,
                    )
                    _store().publish(connection, revision, lineage, bars)
            finally:
                engine.dispose()
            result = {
                "dataset_revision_id": revision_id,
                "source_dataset_id": attached.input_id,
                "instrument_id": attached.feed.instrument_id,
                "timeframe": "1m",
                "range_start": stamp(first),
                "range_end": stamp(end),
                "bar_count": len(bars),
                "canonical_checksum": checksum,
                "calendar_version": attached.feed.calendar_version,
                "non_research_grade": False,
                "suggested_start": stamp(start),
                "suggested_end": stamp(end),
                "config_id": "",
                "config_version": "",
            }
            attached.snapshots[key] = result
            if len(attached.snapshots) > 16:
                attached.snapshots.popitem(last=False)
            return result

    def close(self) -> None:
        with self._lock:
            for client in list(self._sessions):
                self.disconnect(client)


live_analysis = LiveAnalysisManager(live_ingress)
