"""Independent tab sessions over pinned M1 input and the existing detector runtime."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, create_engine, select

from market_analysis.config import (
    DetectionAnalysisConfig,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.detection import DetectorRuntime
from market_analysis.domain import Bar, Timeframe, TradingCalendar
from market_analysis.domain.aggregation import (
    AGGREGATION_VERSION,
    MINUTE,
    MINUTES,
    AnalyticalCandle,
    CandleAggregator,
    bucket_bounds,
)
from market_analysis.persistence.analysis_lineage import load_analysis_lineage
from market_analysis.persistence.analysis_sessions import analysis_session_revisions
from market_analysis.persistence.live_analysis import live_analysis_inputs
from market_analysis.persistence.live_candles import live_candles
from market_analysis.persistence.market_data import load_dataset_lineage, load_dataset_revision
from market_analysis.persistence.pattern_instances import (
    pattern_instance_transitions,
    pattern_instances,
)
from market_analysis.persistence.runs import RunSnapshotRecord, create_run_snapshot

from .analysis_clock import verify_closed_interval
from .analysis_coverage import coverage_status
from .analytical_step import process_detector_candle
from .browser_replay import ReplayReviewScope, _store
from .history_prices import load_completed_candles
from .history_ranges import HistoryFeed
from .live_retention import _decode, _lock_feed
from .replay_catalog import (
    PATTERN_DEFINITIONS,
    detector_bindings,
    required_warmup_bars,
    resolve_replay_calendar,
)
from .replay_event_store import ReplayEventStore

Policy = Literal["live", "manual", "always"]


class StreamSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    config: DetectionAnalysisConfig
    policies: dict[str, Policy] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_revision_id: str
    instrument_id: Literal["US30", "DAX"]
    selected_start: datetime
    selected_end: datetime
    chart_timeframe: Timeframe = Timeframe.M1
    streams: tuple[StreamSettings, ...] = Field(min_length=1, max_length=7)
    mode: Literal["replay", "history"] = "replay"
    automatic: bool = False

    @model_validator(mode="after")
    def validate_inputs(self) -> AnalysisRequest:
        if (
            self.selected_start.tzinfo is None
            or self.selected_end.tzinfo is None
            or self.selected_end <= self.selected_start
        ):
            raise ValueError("analysis interval requires ordered aware bounds")
        if len({item.config.timeframe for item in self.streams}) != len(self.streams):
            raise ValueError("select each analytical timeframe once")
        for stream in self.streams:
            if stream.config.instrument_id != self.instrument_id:
                raise ValueError("analytical instrument differs from session")
            identifiers = {item.pattern_id for item in stream.config.patterns}
            if set(stream.policies) - identifiers:
                raise ValueError("policy references an unselected detector")
        return self


def preview_configuration(config: DetectionAnalysisConfig) -> dict[str, object]:
    resolved = resolve_detection_config(config, pattern_definitions=PATTERN_DEFINITIONS)
    executable = resolved.model_copy(
        update={"patterns": tuple(item for item in resolved.patterns if item.enabled)}
    )
    DetectorRuntime(
        executable,
        detector_bindings(executable),
        run_id="configuration-preview",
        dataset_revision_id="configuration-preview",
    )
    return {
        "detection_config_hash": detection_config_hash(
            resolved, pattern_definitions=PATTERN_DEFINITIONS
        )
    }


def automatic_policy(policy: Policy, mode: Literal["live", "history"]) -> bool:
    return policy == "always" or mode == "live" and policy == "live"


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def bar_payload(bar: Bar) -> dict[str, Any]:
    return {
        key: value
        for key, value in bar.to_canonical_dict().items()
        if key in {"timestamp", "open", "high", "low", "close"}
    }


@dataclass
class DetectorStream:
    runtime: DetectorRuntime
    store: ReplayEventStore
    snapshot: RunSnapshotRecord
    policy: Policy
    frames: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    processed: int = 0
    valid_from: datetime | None = None
    available_from: datetime | None = None

    def process(self, connection: Connection, candle: AnalyticalCandle) -> None:
        if self.valid_from is None and self.processed + 1 >= required_warmup_bars(
            self.runtime.config
        ):
            self.valid_from = candle.available_at
            self.available_from = candle.available_at
        result = process_detector_candle(connection, candle, self.runtime, self.store)
        self.processed += 1
        frame = json.loads(result.frame.debug_json())
        self.frames.append(
            {
                "timestamp": stamp(candle.bar.timestamp),
                "available_at": stamp(candle.available_at),
                "availability": frame["availability"],
                "components": frame["components"],
                "market_events": frame["market_events_this_bar"],
            }
        )
        for event in result.events:
            event_id, instance_id = self.store.identity(event)
            definition = PATTERN_DEFINITIONS[event.pattern_id, event.pattern_version]
            self.events.append(
                {
                    **event.to_canonical_dict(),
                    "event_id": event_id,
                    "runtime_instance_id": event.instance_id,
                    "instance_id": instance_id,
                    "available_at": stamp(candle.available_at),
                    "interval_start": stamp(candle.bar.timestamp),
                    "pattern_name": definition.name,
                    "definition_fingerprint": definition.semantic_fingerprint(),
                    "build_id": self.snapshot.build_id,
                    "code_revision": self.snapshot.code_revision,
                    "code_dirty": self.snapshot.code_dirty,
                    "code_capture_status": self.snapshot.code_capture_status,
                    "stream_emission_order": len(self.events),
                }
            )


def dispatch_candle(
    connection: Connection,
    candle: AnalyticalCandle,
    streams: list[DetectorStream],
    mode: Literal["replay", "live", "history"],
    automatic: bool = False,
) -> None:
    """One policy boundary for replay, requested history and finalized live ingress."""
    if not candle.bar.is_complete:
        raise ValueError("detectors require completed analytical candles")
    for stream in streams:
        if stream.runtime.config.timeframe is not candle.bar.timeframe:
            continue
        if (
            mode == "replay"
            or mode == "history"
            and not automatic
            or automatic_policy(stream.policy, mode)
        ):
            stream.process(connection, candle)


@dataclass
class AnalysisSession:
    session_id: str
    request: AnalysisRequest
    bars: Sequence[Bar]
    calendar: TradingCalendar
    checksum: str
    source_id: str
    streams: list[DetectorStream]
    aggregators: dict[Timeframe, CandleAggregator]
    candles: dict[Timeframe, list[AnalyticalCandle]]
    manifest_hash: str
    index: int = -1
    status: str = "paused"
    failure: str | None = None
    navigation: dict[str, Any] | None = None
    revealed_at: datetime | None = None

    @property
    def watermark(self) -> datetime:
        return self.revealed_at or self.bars[0].timestamp

    def dispatch(
        self,
        connection: Connection,
        candles: list[AnalyticalCandle],
        *,
        mode: Literal["replay", "live", "history"] | None = None,
    ) -> None:
        for candle in sorted(
            candles, key=lambda item: (item.available_at, MINUTES[item.bar.timeframe])
        ):
            self.candles[candle.bar.timeframe].append(candle)
            dispatch_candle(
                connection, candle, self.streams, mode or self.request.mode, self.request.automatic
            )

    def advance_closed(
        self,
        connection: Connection,
        target: datetime,
        *,
        mode: Literal["replay", "live", "history"] | None = None,
    ) -> None:
        verify_closed_interval(self.calendar, self.watermark, target)
        closed = [c for aggregator in self.aggregators.values() for c in aggregator.advance(target)]
        self.dispatch(connection, closed, mode=mode)
        self.revealed_at = target

    def consume(self, connection: Connection) -> None:
        source = self.bars[self.index + 1]
        self.advance_closed(connection, source.timestamp)
        closed = [c for aggregator in self.aggregators.values() for c in aggregator.push(source)]
        self.dispatch(connection, closed)
        self.index += 1
        self.revealed_at = source.timestamp + MINUTE

    def coverage(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [
            {
                "run_id": stream.runtime.run_id,
                "pattern_id": stream.runtime.config.patterns[0].pattern_id,
                "timeframe": stream.runtime.config.timeframe.value,
                "status": coverage_status(
                    start, end, stream.valid_from, stream.available_from, self.watermark
                ),
                "valid_from": None if stream.valid_from is None else stamp(stream.valid_from),
                "available_from": None
                if stream.available_from is None
                else stamp(stream.available_from),
                "valid_to": stamp(self.watermark),
                "processed": stream.processed,
                "required": required_warmup_bars(stream.runtime.config),
            }
            for stream in self.streams
        ]

    def state(self) -> dict[str, Any]:
        first = self.request.streams[0].config
        visible = [
            bar
            for bar in self.bars[: self.index + 1]
            if bar.timestamp >= self.request.selected_start
        ]
        cursor = visible[-1].timestamp if visible else None
        return {
            "run_id": self.session_id,
            "analysis_session": True,
            "status": self.status,
            "dataset_revision_id": self.request.dataset_revision_id,
            "source_dataset_id": self.source_id,
            "canonical_checksum": self.checksum,
            "instrument_id": self.request.instrument_id,
            "timeframe": self.request.chart_timeframe.value,
            "selected_start": self.request.selected_start,
            "selected_end": self.request.selected_end,
            "detection_config_hash": self.manifest_hash,
            "detection_config": first.model_dump(mode="json"),
            "calendar_version": self.calendar.version,
            "warmup_required": 0,
            "warmup_processed": self.index + 1 - len(visible),
            "visible_bars": len(visible),
            "cursor_index": self.index,
            "cursor_time": cursor,
            "available_at": self.watermark,
            "has_next": self.index + 1 < len(self.bars),
            "events": [],
            "streams": [item.model_dump(mode="json") for item in self.request.streams],
            "initialization": [
                {
                    "run_id": stream.runtime.run_id,
                    "timeframe": stream.runtime.config.timeframe.value,
                    "processed": stream.processed,
                    "pattern_id": stream.runtime.config.patterns[0].pattern_id,
                    "pattern_version": stream.runtime.config.patterns[0].pattern_version,
                    "review_interval": (
                        {
                            "start": stamp(
                                self.candles[stream.runtime.config.timeframe][-1].bar.timestamp
                            ),
                            "end": stamp(
                                self.candles[stream.runtime.config.timeframe][-1].available_at
                            ),
                        }
                        if self.candles[stream.runtime.config.timeframe]
                        and self.candles[stream.runtime.config.timeframe][-1].available_at
                        > self.request.selected_start
                        else None
                    ),
                    "required": required_warmup_bars(stream.runtime.config),
                }
                for stream in self.streams
            ],
            "failure": self.failure,
            "navigation": self.navigation,
        }


class AnalysisSessionManager:
    def __init__(self, *, history_capacity: int = 8, replay_capacity: int = 20) -> None:
        if min(history_capacity, replay_capacity) < 1:
            raise ValueError("analysis cache capacity must be positive")
        self._lock = RLock()
        self._sessions: dict[str, AnalysisSession] = {}
        self._history: OrderedDict[str, AnalysisSession] = OrderedDict()
        self._history_capacity = history_capacity
        self._replay_capacity = replay_capacity

    @staticmethod
    def engine() -> Any:
        url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
        if not url:
            raise ValueError("database is not configured")
        return create_engine(url)

    def current(self, session_id: str) -> AnalysisSession:
        session = self._sessions.get(session_id)
        if session is None and session_id in self._history:
            self._history.move_to_end(session_id)
            session = self._history[session_id]
        if session is None:
            raise ValueError("analysis session is unavailable; start a new session")
        return session

    def launch(
        self,
        request: AnalysisRequest,
        *,
        replacing: str | None = None,
        source_input_id: str | None = None,
        source_input_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if (source_input_id is None) != (source_input_manifest is None):
            raise ValueError("live input identity requires its complete manifest")
        with self._lock:
            occupied = len(self._sessions) - int(replacing in self._sessions)
            if request.mode == "replay" and occupied >= self._replay_capacity:
                raise ValueError("stop an analysis session before opening another")
            engine = self.engine()
            try:
                with engine.begin() as connection:
                    sequence = _store().load_sequence(
                        connection, request.dataset_revision_id, request.instrument_id, Timeframe.M1
                    )
                    if (
                        request.selected_start < sequence.lineage.requested_start
                        or request.selected_end > sequence.lineage.requested_end
                    ):
                        raise ValueError("analysis interval is outside the pinned source revision")
                    bars = tuple(
                        bar for bar in sequence.bars if bar.timestamp < request.selected_end
                    )
                    if source_input_manifest is not None:
                        feed = HistoryFeed.model_validate(source_input_manifest["feed"])
                        # The same row lock used by acquisition holds through publication
                        # of the initialized run and manifest, including detector warmup.
                        key = _lock_feed(connection, feed)
                        pinned = {bar.timestamp: bar for bar in bars}
                        for row in connection.execute(
                            select(live_candles).where(
                                live_candles.c.feed_key == key,
                                live_candles.c.timestamp >= sequence.lineage.requested_start,
                                live_candles.c.timestamp < sequence.lineage.requested_end,
                            )
                        ).mappings():
                            retained = _decode(row)
                            if pinned.get(retained.timestamp) != retained:
                                raise ValueError("live bootstrap conflicts with retained feed")
                    if not bars or not any(bar.timestamp >= request.selected_start for bar in bars):
                        raise ValueError("analysis interval has no source candles")
                    revision = load_dataset_revision(connection, request.dataset_revision_id)
                    if revision is None:
                        raise ValueError("source revision not found")
                    calendar = resolve_replay_calendar(
                        request.instrument_id,
                        request.streams[0].config.calendar_id,
                        revision.calendar_version,
                    )
                    streams = []
                    for settings in request.streams:
                        resolved = resolve_detection_config(
                            settings.config, pattern_definitions=PATTERN_DEFINITIONS
                        )
                        detector_bindings(
                            resolved
                        )  # validate the complete selection, including dependencies
                        for pattern in resolved.patterns:
                            policy = settings.policies.get(pattern.pattern_id, "live")
                            if not pattern.enabled or (
                                request.automatic and not automatic_policy(policy, "history")
                            ):
                                continue
                            config = resolved.model_copy(update={"patterns": (pattern,)})
                            snapshot = create_run_snapshot(
                                connection,
                                run_id=uuid4(),
                                dataset_revision_id=source_input_id or request.dataset_revision_id,
                                calendar_version=calendar.version,
                                build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
                                detection_config=config,
                                pattern_definitions=PATTERN_DEFINITIONS,
                            )
                            runtime = DetectorRuntime(
                                config,
                                detector_bindings(config),
                                run_id=snapshot.run_id,
                                dataset_revision_id=source_input_id or request.dataset_revision_id,
                                calendar=calendar,
                                pinned_calendar_version=calendar.version,
                                pinned_config_hash=snapshot.detection_config_hash,
                            )
                            streams.append(
                                DetectorStream(
                                    runtime, ReplayEventStore(PATTERN_DEFINITIONS), snapshot, policy
                                )
                            )
                    timeframes = set(MINUTES)
                    manifest: dict[str, Any] = {
                        "version": "analysis-session-v1",
                        "request": request.model_dump(mode="json"),
                        "source_checksum": sequence.lineage.canonical_checksum,
                        **({"source_input_id": source_input_id} if source_input_id else {}),
                        "initialization_origin": stamp(bars[0].timestamp),
                        "aggregation": AGGREGATION_VERSION,
                        "calendar_version": calendar.version,
                        "streams": [
                            {
                                "run_id": item.runtime.run_id,
                                "config_hash": item.runtime.detection_config_hash,
                                "binding": item.runtime.binding_fingerprint,
                                "build_id": item.snapshot.build_id,
                                "code_revision": item.snapshot.code_revision,
                            }
                            for item in streams
                        ],
                    }
                    reuse = {
                        key: value
                        for key, value in manifest.items()
                        if key not in {"request", "streams"}
                    }
                    reuse["streams"] = [
                        {key: value for key, value in stream.items() if key != "run_id"}
                        for stream in manifest["streams"]
                    ]
                    # Dirty working trees have no stable reproducible build identity.
                    reuse["dirty_sessions"] = [
                        stream.runtime.run_id for stream in streams if stream.snapshot.code_dirty
                    ]
                    manifest["reuse_identity"] = sha256(
                        json.dumps(reuse, sort_keys=True).encode()
                    ).hexdigest()
                    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                    manifest_hash = sha256(manifest_json.encode()).hexdigest()
                    session_id = str(uuid4())
                    session = AnalysisSession(
                        session_id,
                        request,
                        bars,
                        calendar,
                        sequence.lineage.canonical_checksum,
                        sequence.lineage.source_dataset_id,
                        streams,
                        {tf: CandleAggregator(tf, calendar) for tf in timeframes},
                        {tf: [] for tf in timeframes},
                        manifest_hash,
                    )
                    while (
                        session.index + 1 < len(bars)
                        and bars[session.index + 1].timestamp < request.selected_start
                    ):
                        session.consume(connection)
                    session.advance_closed(connection, request.selected_start)
                    if request.mode == "replay":
                        if source_input_id is not None:
                            while session.index + 1 < len(bars):
                                session.consume(connection)
                            session.advance_closed(connection, request.selected_end)
                        for stream in streams:
                            required = required_warmup_bars(stream.runtime.config)
                            if stream.processed < required:
                                raise ValueError(
                                    f"insufficient {stream.runtime.config.timeframe.value} "
                                    "initialization: "
                                    f"{stream.processed} preceding bars; {required} required"
                                )
                    else:
                        while session.index + 1 < len(bars):
                            session.consume(connection)
                        session.advance_closed(connection, request.selected_end)
                        session.status = "completed"
                    connection.execute(
                        analysis_session_revisions.insert().values(
                            session_id=session_id,
                            manifest_json=manifest_json,
                            manifest_hash=manifest_hash,
                        )
                    )
                    if source_input_manifest is not None:
                        input_json = json.dumps(
                            source_input_manifest, sort_keys=True, separators=(",", ":")
                        )
                        connection.execute(
                            live_analysis_inputs.insert().values(
                                input_id=source_input_id,
                                session_id=session_id,
                                manifest_json=input_json,
                                manifest_hash=sha256(input_json.encode()).hexdigest(),
                            )
                        )
                if request.mode == "history":
                    self._history[session_id] = session
                    while len(self._history) > self._history_capacity:
                        self._history.popitem(last=False)
                else:
                    self._sessions[session_id] = session
                return session.state()
            finally:
                engine.dispose()

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                item.state()
                for item in self._sessions.values()
                if item.status != "aborted" and item.request.mode == "replay"
            ]

    def state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self.current(session_id).state()

    def command(
        self, session_id: str, action: str, timeframe: Timeframe | None = None
    ) -> dict[str, Any]:
        with self._lock:
            session = self.current(session_id)
            if action == "stop":
                session.status = "aborted"
                result = session.state()
                self._sessions.pop(session_id, None)
                self._history.pop(session_id, None)
                return result
            if action == "reset":
                request = session.request
                result = self.launch(request, replacing=session_id)
                self._sessions.pop(session_id, None)
                self._history.pop(session_id, None)
                return result
            if action == "pause":
                if session.status == "running":
                    session.status = "paused"
                return session.state()
            if session.status in {"completed", "failed", "aborted"}:
                raise ValueError("analysis session cannot advance after terminal status")
            if action == "play":
                session.status = "running"
                return session.state()
            if action not in {"step", "tick", "next-event"}:
                raise ValueError("unknown analysis command")
            if action == "tick" and session.status != "running":
                raise ValueError("playback is paused")
            engine = self.engine()
            try:
                with engine.begin() as connection:
                    previous_index = session.index
                    previous_watermark = session.watermark
                    previous_streams = [
                        (len(s.frames), len(s.events), s.processed, s.valid_from, s.available_from)
                        for s in session.streams
                    ]
                    try:
                        current_time = session.bars[
                            min(session.index + 1, len(session.bars) - 1)
                        ].timestamp
                        display = timeframe or session.request.chart_timeframe
                        before_display = len(session.candles[display])
                        target = (
                            session.aggregators[display].pending_end
                            or bucket_bounds(
                                current_time,
                                display,
                                session.calendar,
                            )[1]
                        )
                        prior_events = sum(len(item.events) for item in session.streams)
                        while session.index + 1 < len(session.bars):
                            next_source = session.bars[session.index + 1]
                            if action != "next-event" and next_source.timestamp >= target:
                                session.advance_closed(connection, target)
                                if len(session.candles[display]) > before_display:
                                    break
                                target = bucket_bounds(
                                    next_source.timestamp, display, session.calendar
                                )[1]
                                continue
                            session.consume(connection)
                            if action == "next-event":
                                if sum(len(item.events) for item in session.streams) > prior_events:
                                    break
                            elif session.watermark >= target:
                                break
                        if session.index + 1 == len(session.bars):
                            session.advance_closed(connection, session.request.selected_end)
                    except Exception as error:
                        session.index = previous_index
                        session.revealed_at = previous_watermark
                        for stream, (frames, events, processed, valid_from, available_from) in zip(
                            session.streams, previous_streams, strict=True
                        ):
                            del stream.frames[frames:]
                            del stream.events[events:]
                            stream.processed = processed
                            stream.valid_from = valid_from
                            stream.available_from = available_from
                        for candles in session.candles.values():
                            candles[:] = [c for c in candles if c.available_at <= session.watermark]
                        session.status = "failed"
                        session.failure = str(error)
                        raise
                session.status = (
                    "completed"
                    if session.index + 1 == len(session.bars)
                    else "running"
                    if action == "tick"
                    else "paused"
                )
                if action == "next-event":
                    emitted = [
                        event
                        for stream, (_, count, _, _, _) in zip(
                            session.streams, previous_streams, strict=True
                        )
                        for event in stream.events[count:]
                    ]
                    emitted.sort(
                        key=lambda event: (
                            event["available_at"],
                            event["timeframe"],
                            event["stream_emission_order"],
                        )
                    )
                    session.navigation = {
                        "processed_bars": session.index - previous_index,
                        "stopped_on_event": bool(emitted),
                        "matched_event": emitted[0] if emitted else None,
                    }
                return session.state()
            finally:
                engine.dispose()

    def view(
        self,
        session_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> dict[str, Any]:
        with self._lock:
            session = self.current(session_id)
            if start.tzinfo is None or end.tzinfo is None or start >= end or not 1 <= limit <= 500:
                raise ValueError("view requires aware ordered bounds and limit 1..500")
            cutoff = min(end, session.watermark)
            candles = session.candles[timeframe]
            displayed = [c for c in candles if c.available_at <= cutoff][-limit:]
            coverage = session.coverage(start, cutoff)
            events: list[dict[str, Any]] = []
            for stream in session.streams:
                if stream.available_from is None or cutoff < stream.available_from:
                    continue
                visible = [
                    event
                    for event in stream.events
                    if datetime.fromisoformat(event["available_at"]) <= cutoff
                ]
                latest = {event["instance_id"]: event for event in visible}
                excluded = {
                    key
                    for key, event in latest.items()
                    if event["to_state"]
                    in PATTERN_DEFINITIONS[
                        event["pattern_id"], event["pattern_version"]
                    ].effective_terminal_states
                    and datetime.fromisoformat(event["available_at"]) < stream.available_from
                }
                events.extend(
                    dict(event) for event in visible if event["instance_id"] not in excluded
                )
            events.sort(
                key=lambda event: (
                    event["available_at"],
                    MINUTES[Timeframe(event["timeframe"])],
                    event["pattern_id"],
                    event["pattern_version"],
                    event["stream_emission_order"],
                )
            )
            for order, event in enumerate(events):
                event["emission_order"] = order
            observations = next(
                (
                    stream.frames
                    for stream in session.streams
                    if stream.runtime.config.timeframe is timeframe
                ),
                [],
            )
            visible_times = {stamp(item.bar.timestamp) for item in displayed}
            latest_source = session.bars[session.index] if session.index >= 0 else None
            return {
                "run_id": session_id,
                "cursor_index": session.index,
                "cursor_time": latest_source.timestamp
                if latest_source is not None
                and latest_source.timestamp >= session.request.selected_start
                else None,
                "limit": limit,
                "available_at": stamp(session.watermark),
                "history_start": stamp(candles[0].bar.timestamp) if candles else None,
                "timeframe": timeframe.value,
                "bars": [
                    {**bar_payload(item.bar), "available_at": stamp(item.available_at)}
                    for item in displayed
                ],
                "observations": [
                    frame for frame in observations if frame["timestamp"] in visible_times
                ],
                "events": events,
                "coverage": (
                    "not_requested"
                    if not coverage
                    else "calculated"
                    if all(item["status"] == "calculated" for item in coverage)
                    else "partial"
                    if any(item["status"] != "initialization_unavailable" for item in coverage)
                    else "initialization_unavailable"
                ),
                "stream_coverage": coverage,
                "view_start": stamp(start),
                "view_end": stamp(cutoff),
            }

    def evidence_run_ids(self, session_id: str) -> tuple[str, ...]:
        with self._lock:
            session = self._sessions.get(session_id) or self._history.get(session_id)
            if session is not None:
                return tuple(stream.runtime.run_id for stream in session.streams)
            engine = self.engine()
            try:
                with engine.connect() as connection:
                    row = (
                        connection.execute(
                            select(analysis_session_revisions).where(
                                analysis_session_revisions.c.session_id == session_id,
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        row is None
                        or sha256(row["manifest_json"].encode()).hexdigest() != row["manifest_hash"]
                    ):
                        raise ValueError("analysis evidence session is unavailable")
                    manifest = json.loads(row["manifest_json"])
                    if manifest["request"]["mode"] != "history" and not manifest.get(
                        "source_input_id"
                    ):
                        raise ValueError("analysis evidence session is unavailable")
                    return tuple(stream["run_id"] for stream in manifest["streams"])
            finally:
                engine.dispose()

    def validate_review_interval(self, run_id: str, start: datetime, end: datetime) -> None:
        scope = self.review_scope(run_id)
        engine = self.engine()
        try:
            with engine.connect() as connection:
                lineage = load_analysis_lineage(connection, run_id)
                if lineage is None:
                    raise ValueError("analytical lineage is unavailable")
                calendar = resolve_replay_calendar(
                    scope.instrument_id,
                    lineage.config.calendar_id,
                    lineage.snapshot.calendar_version,
                )
                if (
                    bucket_bounds(start, scope.timeframe, calendar)[0] != start
                    or bucket_bounds(end - MINUTE, scope.timeframe, calendar)[1] != end
                ):
                    raise ValueError(
                        "review interval must follow completed analytical candle boundaries"
                    )
        finally:
            engine.dispose()

    def review_scope(self, run_id: str) -> ReplayReviewScope:
        with self._lock:
            for session in (*self._sessions.values(), *self._history.values()):
                for stream in session.streams:
                    if stream.runtime.run_id != run_id:
                        continue
                    return ReplayReviewScope(
                        run_id,
                        stream.runtime.dataset_revision_id,
                        session.request.instrument_id,
                        stream.runtime.config.timeframe,
                        bucket_bounds(
                            session.request.selected_start,
                            stream.runtime.config.timeframe,
                            session.calendar,
                        )[0],
                        session.request.selected_end,
                        session.watermark,
                        frozenset(event["event_id"] for event in stream.events),
                        frozenset(event["instance_id"] for event in stream.events),
                        analysis_run_id=run_id,
                    )
            engine = self.engine()
            try:
                with engine.connect() as connection:
                    lineage = load_analysis_lineage(connection, run_id)
                    if lineage is None or (
                        lineage.manifest["request"]["mode"] != "history"
                        and not lineage.manifest.get("source_input_id")
                    ):
                        raise ValueError("analysis evidence session is unavailable")
                    request = lineage.manifest["request"]
                    selected_start = datetime.fromisoformat(request["selected_start"])
                    visible_end = datetime.fromisoformat(request["selected_end"])
                    if lineage.manifest.get("source_input_id"):
                        from .live_inputs import read_live_input

                        live = read_live_input(
                            connection, _store(), lineage.snapshot.dataset_revision_id
                        )
                        selected_start = datetime.fromisoformat(
                            live["manifest"]["initialization_origin"]
                        )
                        visible_end = datetime.fromisoformat(live["available_at"])
                    calendar = resolve_replay_calendar(
                        lineage.config.instrument_id,
                        lineage.config.calendar_id,
                        lineage.snapshot.calendar_version,
                    )
                    instances = frozenset(
                        connection.scalars(
                            select(pattern_instances.c.instance_id).where(
                                pattern_instances.c.run_id == run_id
                            )
                        )
                    )
                    events = frozenset(
                        connection.scalars(
                            select(pattern_instance_transitions.c.event_id).where(
                                pattern_instance_transitions.c.instance_id.in_(instances)
                            )
                        )
                    )
                    return ReplayReviewScope(
                        run_id,
                        lineage.snapshot.dataset_revision_id,
                        lineage.config.instrument_id,
                        lineage.config.timeframe,
                        bucket_bounds(
                            selected_start,
                            lineage.config.timeframe,
                            calendar,
                        )[0],
                        visible_end,
                        visible_end,
                        events,
                        instances,
                        analysis_run_id=run_id,
                    )
            finally:
                engine.dispose()


analysis_sessions = AnalysisSessionManager()


def history_candles(
    revision_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: int = 500,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None or start >= end or not 1 <= limit <= 500:
        raise ValueError("history needs ordered aware bounds and limit 1..500")
    engine = analysis_sessions.engine()
    try:
        with engine.connect() as connection:
            lineage = load_dataset_lineage(connection, revision_id, instrument_id, Timeframe.M1)
            if lineage is None:
                raise ValueError("source lineage not found")
            from .replay_catalog import demo_detection_config

            revision = load_dataset_revision(connection, revision_id)
            if revision is None:
                raise ValueError("source revision not found")
            calendar = resolve_replay_calendar(
                instrument_id,
                demo_detection_config(instrument_id).calendar_id,
                revision.calendar_version,
            )
            candles = load_completed_candles(
                connection,
                _store(),
                lineage,
                calendar,
                timeframe,
                start,
                end,
                limit,
            )
            return {
                "bars": [
                    {**bar_payload(c.bar), "available_at": stamp(c.available_at)} for c in candles
                ],
                "canonical_checksum": lineage.canonical_checksum,
                "aggregation_version": AGGREGATION_VERSION,
            }
    finally:
        engine.dispose()
