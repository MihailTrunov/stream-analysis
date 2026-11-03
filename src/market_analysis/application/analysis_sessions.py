"""Independent tab sessions over pinned M1 input and the existing detector runtime."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, create_engine

from market_analysis.config import (
    DetectionAnalysisConfig,
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
from market_analysis.persistence.analysis_sessions import analysis_session_revisions
from market_analysis.persistence.market_data import load_dataset_revision
from market_analysis.persistence.runs import RunSnapshotRecord, create_run_snapshot

from .browser_replay import ReplayReviewScope, _store
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

    def process(self, connection: Connection, candle: AnalyticalCandle) -> None:
        result = self.runtime.process_bar(candle.bar)
        self.store(connection, candle.bar, result, self.runtime.binding_fingerprint)
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
    bars: tuple[Bar, ...]
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

    @property
    def watermark(self) -> datetime:
        return (
            self.bars[self.index].timestamp + MINUTE if self.index >= 0 else self.bars[0].timestamp
        )

    def consume(self, connection: Connection) -> None:
        self.index += 1
        source = self.bars[self.index]
        for timeframe in sorted(self.aggregators, key=lambda value: MINUTES[value]):
            closed = self.aggregators[timeframe].push(source)
            for candle in closed:
                self.candles[timeframe].append(candle)
                dispatch_candle(
                    connection, candle, self.streams, self.request.mode, self.request.automatic
                )

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
                    "required": required_warmup_bars(stream.runtime.config),
                }
                for stream in self.streams
            ],
            "failure": self.failure,
            "navigation": self.navigation,
        }


class AnalysisSessionManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, AnalysisSession] = {}

    @staticmethod
    def engine() -> Any:
        url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
        if not url:
            raise ValueError("database is not configured")
        return create_engine(url)

    def current(self, session_id: str) -> AnalysisSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("analysis session is unavailable; start a new session")
        return session

    def launch(self, request: AnalysisRequest) -> dict[str, Any]:
        with self._lock:
            if len(self._sessions) >= 20:
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
                                dataset_revision_id=request.dataset_revision_id,
                                calendar_version=calendar.version,
                                build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
                                detection_config=config,
                                pattern_definitions=PATTERN_DEFINITIONS,
                            )
                            runtime = DetectorRuntime(
                                config,
                                detector_bindings(config),
                                run_id=snapshot.run_id,
                                dataset_revision_id=request.dataset_revision_id,
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
                    if request.mode == "replay":
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
                        session.status = "completed"
                    connection.execute(
                        analysis_session_revisions.insert().values(
                            session_id=session_id,
                            manifest_json=manifest_json,
                            manifest_hash=manifest_hash,
                        )
                    )
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
                del self._sessions[session_id]
                return result
            if action == "reset":
                request = session.request
                result = self.launch(request)
                del self._sessions[session_id]
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
                    previous_streams = [
                        (len(s.frames), len(s.events), s.processed) for s in session.streams
                    ]
                    try:
                        current_time = session.bars[
                            min(session.index + 1, len(session.bars) - 1)
                        ].timestamp
                        target = bucket_bounds(
                            current_time,
                            timeframe or session.request.chart_timeframe,
                            session.calendar,
                        )[1]
                        prior_events = sum(len(item.events) for item in session.streams)
                        while session.index + 1 < len(session.bars):
                            session.consume(connection)
                            if action == "next-event":
                                if sum(len(item.events) for item in session.streams) > prior_events:
                                    break
                            elif session.watermark >= target:
                                break
                    except Exception as error:
                        session.index = previous_index
                        for stream, (frames, events, processed) in zip(
                            session.streams, previous_streams, strict=True
                        ):
                            del stream.frames[frames:]
                            del stream.events[events:]
                            stream.processed = processed
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
                        for stream, (_, count, _) in zip(
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
            displayed = [
                c for c in candles if c.bar.timestamp < end and c.available_at <= session.watermark
            ][-limit:]
            events = [
                dict(event)
                for stream in session.streams
                for event in stream.events
                if datetime.fromisoformat(event["available_at"]) <= cutoff
            ]
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
            return {
                "run_id": session_id,
                "cursor_index": session.index,
                "cursor_time": session.state()["cursor_time"],
                "limit": limit,
                "available_at": stamp(session.watermark),
                "timeframe": timeframe.value,
                "bars": [
                    {**bar_payload(item.bar), "available_at": stamp(item.available_at)}
                    for item in displayed
                ],
                "observations": [
                    frame for frame in observations if frame["timestamp"] in visible_times
                ],
                "events": events,
                "coverage": "calculated",
                "view_start": stamp(start),
                "view_end": stamp(cutoff),
            }

    def evidence_run_ids(self, session_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(stream.runtime.run_id for stream in self.current(session_id).streams)

    def review_scope(self, run_id: str) -> ReplayReviewScope:
        with self._lock:
            for session in self._sessions.values():
                for stream in session.streams:
                    if stream.runtime.run_id != run_id:
                        continue
                    return ReplayReviewScope(
                        run_id,
                        session.request.dataset_revision_id,
                        session.request.instrument_id,
                        stream.runtime.config.timeframe,
                        session.request.selected_start,
                        session.request.selected_end,
                        session.watermark,
                        frozenset(event["event_id"] for event in stream.events),
                        frozenset(event["instance_id"] for event in stream.events),
                    )
            raise ValueError("analysis evidence session is unavailable")


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
            sequence = _store().load_sequence(connection, revision_id, instrument_id, Timeframe.M1)
            if start < sequence.lineage.requested_start or end > sequence.lineage.requested_end:
                raise ValueError("history lies outside the retained revision")
            from .replay_catalog import demo_detection_config

            revision = load_dataset_revision(connection, revision_id)
            if revision is None:
                raise ValueError("source revision not found")
            calendar = resolve_replay_calendar(
                instrument_id,
                demo_detection_config(instrument_id).calendar_id,
                revision.calendar_version,
            )
            aggregator = CandleAggregator(timeframe, calendar)
            candles = [
                c for bar in sequence.bars if bar.timestamp < end for c in aggregator.push(bar)
            ]
            return {
                "bars": [
                    {**bar_payload(c.bar), "available_at": stamp(c.available_at)}
                    for c in candles
                    if c.bar.timestamp >= start
                ][-limit:],
                "canonical_checksum": sequence.lineage.canonical_checksum,
                "aggregation_version": AGGREGATION_VERSION,
            }
    finally:
        engine.dispose()
