"""Single-user browser walkthrough over pinned, causally advanced replay runs."""

from __future__ import annotations

import json
import os
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, create_engine, select

from market_analysis.application.replay_catalog import (
    DEMO_CONFIG_ID,
    DEMO_CONFIG_VERSION,
    PATTERN_DEFINITIONS,
    calendar_resolver,
    detector_bindings,
    required_warmup_bars,
    resolve_replay_calendar,
)
from market_analysis.application.replay_pipeline import DetectorEventFilter, ReplayPipeline
from market_analysis.application.replay_run import create_replay_run
from market_analysis.config import DetectionAnalysisConfig, resolve_detection_config
from market_analysis.demo.replay_seed import DEMO_END, DEMO_SELECTED_START
from market_analysis.domain import Bar, BarSequence, Timeframe
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.market_data import (
    dataset_memberships,
    dataset_revisions,
    load_dataset_lineage,
    load_dataset_revision,
)
from market_analysis.persistence.replay_runs import (
    ReplayStatus,
    load_replay_run,
    transition_replay_run,
)


class BrowserReplayError(ValueError):
    """A launch, navigation, or active-session contract failed."""


def _store() -> DatasetStore:
    return DatasetStore(Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data")))


def replay_sources(connection: Connection) -> list[dict[str, object]]:
    """List published M1 memberships, never source bars or credentials."""
    rows = connection.execute(
        select(dataset_revisions, dataset_memberships)
        .join(dataset_memberships)
        .where(dataset_memberships.c.timeframe == Timeframe.M1.value)
        .order_by(dataset_memberships.c.instrument_id, dataset_revisions.c.created_at)
    ).mappings()
    result: list[dict[str, object]] = []
    for row in rows:
        if row["instrument_id"] not in ("US30", "DAX") or row["bar_count"] <= 0:
            continue
        lineage = load_dataset_lineage(
            connection, row["dataset_revision_id"], row["instrument_id"], Timeframe.M1
        )
        if lineage is None or lineage.validation_status.value == "fail":
            continue
        is_demo = row["provider"] == "seeded-demo"
        result.append({
            "dataset_revision_id": row["dataset_revision_id"],
            "source_dataset_id": lineage.source_dataset_id,
            "instrument_id": row["instrument_id"],
            "timeframe": Timeframe.M1.value,
            "range_start": lineage.requested_start,
            "range_end": lineage.requested_end,
            "bar_count": lineage.bar_count,
            "canonical_checksum": lineage.canonical_checksum,
            "calendar_version": row["calendar_version"],
            "non_research_grade": is_demo,
            "suggested_start": DEMO_SELECTED_START if is_demo else lineage.requested_start,
            "suggested_end": DEMO_END if is_demo else lineage.requested_end,
            "config_id": DEMO_CONFIG_ID,
            "config_version": DEMO_CONFIG_VERSION,
        })
    return result


def _preflight(
    connection: Connection,
    revision_id: str,
    config: DetectionAnalysisConfig,
    start: datetime,
    end: datetime,
) -> tuple[DetectionAnalysisConfig, BarSequence, int]:
    if config.instrument_id not in ("US30", "DAX") or config.timeframe is not Timeframe.M1:
        raise BrowserReplayError("MVP browser replay supports US30/DAX M1 only")
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise BrowserReplayError("selected interval needs ordered timezone-aware boundaries")
    resolved = resolve_detection_config(config, pattern_definitions=PATTERN_DEFINITIONS)
    detector_bindings(resolved)  # fail closed on unbound or unsupported versions
    revision = load_dataset_revision(connection, revision_id)
    if revision is None:
        raise BrowserReplayError("dataset revision does not exist")
    calendar = resolve_replay_calendar(
        resolved.instrument_id, resolved.calendar_id, revision.calendar_version
    )
    sequence = _store().load_sequence(
        connection, revision_id, resolved.instrument_id, resolved.timeframe
    )
    bars = sequence.bars
    first = bisect_left(bars, start, key=lambda bar: bar.timestamp)
    stop = bisect_left(bars, end, key=lambda bar: bar.timestamp)
    if first >= stop:
        raise BrowserReplayError("selected interval has no completed canonical bars")
    warmup = required_warmup_bars(resolved)
    if first < warmup:
        raise BrowserReplayError(
            f"insufficient warm-up: {first} preceding bars; {warmup} required"
        )
    if start < sequence.lineage.requested_start or end > sequence.lineage.requested_end:
        raise BrowserReplayError("selected interval lies outside the immutable dataset")
    previous: Bar | None = None
    for bar in bars[:stop]:
        if not calendar.derive(bar.timestamp).is_open:
            raise BrowserReplayError(
                f"bar outside pinned open session: {bar.timestamp.isoformat()}"
            )
        if previous is not None:
            missing = previous.timestamp + timedelta(minutes=1)
            while missing < bar.timestamp:
                if calendar.derive(missing).is_open:
                    raise BrowserReplayError(f"open-session M1 gap at {missing.isoformat()}")
                missing += timedelta(minutes=1)
        previous = bar
    return resolved, sequence, warmup


@dataclass(slots=True)
class _Session:
    pipeline: ReplayPipeline
    revision_id: str
    config: DetectionAnalysisConfig
    selected_start: datetime
    selected_end: datetime
    warmup_required: int
    source_id: str
    checksum: str


class BrowserReplayManager:
    """One active analytical state, protected against overlapping browser calls."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._session: _Session | None = None

    def _connection(self) -> Engine:
        url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
        if not url:
            raise BrowserReplayError("database is not configured")
        return create_engine(url)

    def _current(self, run_id: str) -> _Session:
        session = self._session
        if session is None or session.pipeline.run_id != run_id:
            raise BrowserReplayError("walkthrough is not active; launch a new run")
        return session

    @staticmethod
    def _state(connection: Connection, session: _Session) -> dict[str, object]:
        record = load_replay_run(connection, UUID(session.pipeline.run_id))
        assert record is not None
        last = session.pipeline.steps[-1] if session.pipeline.steps else None
        return {
            "run_id": session.pipeline.run_id,
            "status": record.status.value,
            "dataset_revision_id": session.revision_id,
            "source_dataset_id": session.source_id,
            "canonical_checksum": session.checksum,
            "instrument_id": session.config.instrument_id,
            "timeframe": session.config.timeframe.value,
            "selected_start": session.selected_start,
            "selected_end": session.selected_end,
            "detection_config_hash": session.pipeline.snapshot.detection_config_hash,
            "calendar_version": session.pipeline.snapshot.calendar_version,
            "warmup_required": session.warmup_required,
            "warmup_processed": session.pipeline.warmup_bars,
            "visible_bars": session.pipeline.visible_bars,
            "cursor_index": session.pipeline.cursor_index,
            "cursor_time": last.view.timestamp if last is not None else None,
            "has_next": session.pipeline.has_next,
            "events": [event.to_canonical_dict() for event in last.result.events]
            if last is not None and last.is_visible else [],
        }

    def launch(
        self, revision_id: str, config: DetectionAnalysisConfig,
        start: datetime, end: datetime,
    ) -> dict[str, object]:
        with self._lock:
            if self._session is not None:
                raise BrowserReplayError("stop the active walkthrough before changing inputs")
            engine = self._connection()
            try:
                with engine.begin() as connection:
                    resolved, sequence, warmup = _preflight(
                        connection, revision_id, config, start, end
                    )
                    run_id = uuid4()
                    create_replay_run(
                        connection, run_id=run_id, dataset_revision_id=revision_id,
                        detection_config=resolved, selected_start=start, selected_end=end,
                        created_at=datetime.now(UTC),
                        build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
                        pattern_definitions=PATTERN_DEFINITIONS,
                    )
                    pipeline = ReplayPipeline.from_run(
                        connection, run_id, bars=sequence.bars,
                        bindings_factory=lambda: detector_bindings(resolved),
                        pattern_definitions=PATTERN_DEFINITIONS,
                        calendar_resolver=calendar_resolver(resolved.instrument_id),
                    )
                    session = _Session(
                        pipeline, revision_id, resolved, start, end, warmup,
                        sequence.lineage.source_dataset_id,
                        sequence.lineage.canonical_checksum,
                    )
                    state = self._state(connection, session)
                self._session = session
                return state
            finally:
                engine.dispose()

    def state(self, run_id: str) -> dict[str, object]:
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            try:
                with engine.connect() as connection:
                    return self._state(connection, session)
            finally:
                engine.dispose()

    def active(self) -> dict[str, object] | None:
        """Allow a refreshed local browser to reattach to the same API process."""
        with self._lock:
            session = self._session
            if session is None:
                return None
            engine = self._connection()
            try:
                with engine.connect() as connection:
                    return self._state(connection, session)
            finally:
                engine.dispose()

    def stop(self, run_id: str) -> dict[str, object]:
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            try:
                with engine.begin() as connection:
                    record = load_replay_run(connection, UUID(run_id))
                    assert record is not None
                    if record.status in (
                        ReplayStatus.CREATED, ReplayStatus.RUNNING, ReplayStatus.PAUSED
                    ):
                        transition_replay_run(
                            connection, UUID(run_id), ReplayStatus.ABORTED, at=datetime.now(UTC)
                        )
                    state = self._state(connection, session)
                self._session = None
                return state
            finally:
                engine.dispose()

    def step_visible(self, run_id: str, *, keep_running: bool = False) -> dict[str, object]:
        """Process warm-up causally, then expose exactly one new selected bar."""
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            failure: Exception | None = None
            result: dict[str, object] | None = None
            try:
                with engine.begin() as connection:
                    try:
                        record = load_replay_run(connection, UUID(run_id))
                        assert record is not None
                        if record.status is ReplayStatus.CREATED:
                            if keep_running:
                                raise BrowserReplayError("play before requesting playback ticks")
                            session.pipeline.start(connection, at=datetime.now(UTC))
                        elif record.status is ReplayStatus.PAUSED:
                            if keep_running:
                                raise BrowserReplayError("playback is paused")
                            session.pipeline.resume(connection, at=datetime.now(UTC))
                        elif record.status is not ReplayStatus.RUNNING:
                            raise BrowserReplayError("replay cannot advance after terminal status")
                        if not session.pipeline.has_next:
                            raise BrowserReplayError(
                                "replay is at the end of the selected interval"
                            )
                        while session.pipeline.has_next:
                            step = session.pipeline.step(connection)
                            if step.is_visible:
                                break
                        if session.pipeline.has_next:
                            if not keep_running:
                                transition_replay_run(
                                    connection, UUID(run_id), ReplayStatus.PAUSED,
                                    at=datetime.now(UTC),
                                )
                        else:
                            session.pipeline.run_to_completion(connection, at=datetime.now(UTC))
                        result = self._state(connection, session)
                    except Exception as exc:
                        # ReplayPipeline records FAILED after its nested rollback.
                        # Commit that audit row even though this HTTP action fails.
                        failure = exc
                if failure is not None:
                    raise BrowserReplayError(str(failure)) from failure
                assert result is not None
                return result
            finally:
                engine.dispose()

    def play(self, run_id: str) -> dict[str, object]:
        """Start or resume; browser cadence only schedules later step requests."""
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            try:
                with engine.begin() as connection:
                    record = load_replay_run(connection, UUID(run_id))
                    assert record is not None
                    if record.status is ReplayStatus.CREATED:
                        session.pipeline.start(connection, at=datetime.now(UTC))
                    elif record.status is ReplayStatus.PAUSED:
                        session.pipeline.resume(connection, at=datetime.now(UTC))
                    elif record.status is not ReplayStatus.RUNNING:
                        raise BrowserReplayError("completed or failed replay cannot play")
                    return self._state(connection, session)
            finally:
                engine.dispose()

    def pause(self, run_id: str) -> dict[str, object]:
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            try:
                with engine.begin() as connection:
                    record = load_replay_run(connection, UUID(run_id))
                    assert record is not None
                    if record.status is ReplayStatus.RUNNING:
                        transition_replay_run(
                            connection, UUID(run_id), ReplayStatus.PAUSED,
                            at=datetime.now(UTC),
                        )
                    elif record.status not in (ReplayStatus.CREATED, ReplayStatus.PAUSED):
                        raise BrowserReplayError("completed or failed replay cannot pause")
                    return self._state(connection, session)
            finally:
                engine.dispose()

    def next_event(
        self, run_id: str, event_filter: DetectorEventFilter | None = None,
    ) -> dict[str, object]:
        """Run the shared pipeline sequentially; no future event index is consulted."""
        with self._lock:
            session = self._current(run_id)
            engine = self._connection()
            failure: Exception | None = None
            state: dict[str, object] | None = None
            try:
                with engine.begin() as connection:
                    try:
                        outcome = session.pipeline.run_until_event(
                            connection, event_filter, at=datetime.now(UTC)
                        )
                        if outcome.stopped_on_event and not session.pipeline.has_next:
                            # Keep the terminal event as the navigation result,
                            # but complete the exhausted persisted lifecycle.
                            session.pipeline.run_until_event(
                                connection, event_filter, at=datetime.now(UTC)
                            )
                        state = self._state(connection, session)
                        state["navigation"] = {
                            "processed_bars": outcome.processed_bars,
                            "stopped_on_event": outcome.stopped_on_event,
                            "matched_event": (
                                outcome.matched_event.to_canonical_dict()
                                if outcome.matched_event else None
                            ),
                        }
                    except Exception as exc:
                        failure = exc
                if failure is not None:
                    raise BrowserReplayError(str(failure)) from failure
                assert state is not None
                return state
            finally:
                engine.dispose()

    def replace(
        self, run_id: str, *, target: datetime | None = None,
    ) -> dict[str, object]:
        """Reset or seek with a fresh persisted identity and fresh analytical state."""
        with self._lock:
            old = self._current(run_id)
            normalized_target = target.astimezone(UTC) if target and target.tzinfo else None
            if target is not None and (
                normalized_target is None
                or not old.selected_start <= normalized_target < old.selected_end
            ):
                raise BrowserReplayError("seek target must lie inside the selected interval")
            engine = self._connection()
            try:
                with engine.begin() as connection:
                    resolved, sequence, warmup = _preflight(
                        connection, old.revision_id, old.config,
                        old.selected_start, old.selected_end,
                    )
                    if normalized_target is not None:
                        target_index = bisect_left(
                            sequence.bars, normalized_target,
                            key=lambda bar: bar.timestamp,
                        )
                        if (
                            target_index >= len(sequence.bars)
                            or sequence.bars[target_index].timestamp != normalized_target
                        ):
                            raise BrowserReplayError(
                                "seek target is not a completed selected bar"
                            )
                    new_id = uuid4()
                    create_replay_run(
                        connection, run_id=new_id, dataset_revision_id=old.revision_id,
                        detection_config=resolved,
                        selected_start=old.selected_start, selected_end=old.selected_end,
                        created_at=datetime.now(UTC),
                        build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
                        pattern_definitions=PATTERN_DEFINITIONS,
                    )
                    pipeline = ReplayPipeline.from_run(
                        connection, new_id, bars=sequence.bars,
                        bindings_factory=lambda: detector_bindings(resolved),
                        pattern_definitions=PATTERN_DEFINITIONS,
                        calendar_resolver=calendar_resolver(resolved.instrument_id),
                    )
                    new = _Session(
                        pipeline, old.revision_id, resolved, old.selected_start,
                        old.selected_end, warmup,
                        sequence.lineage.source_dataset_id,
                        sequence.lineage.canonical_checksum,
                    )
                    if normalized_target is not None:
                        pipeline.start(connection, at=datetime.now(UTC))
                        while pipeline.has_next:
                            step = pipeline.step(connection)
                            if step.view.timestamp == normalized_target:
                                break
                        if pipeline.has_next:
                            transition_replay_run(
                                connection, new_id, ReplayStatus.PAUSED,
                                at=datetime.now(UTC),
                            )
                        else:
                            pipeline.run_to_completion(connection, at=datetime.now(UTC))
                    old_record = load_replay_run(connection, UUID(run_id))
                    assert old_record is not None
                    if old_record.status in (
                        ReplayStatus.CREATED, ReplayStatus.RUNNING, ReplayStatus.PAUSED
                    ):
                        transition_replay_run(
                            connection, UUID(run_id), ReplayStatus.ABORTED,
                            at=datetime.now(UTC),
                        )
                    state = self._state(connection, new)
                self._session = new
                return state
            finally:
                engine.dispose()

    def visible_bars(
        self, run_id: str, start: datetime, end: datetime, limit: int,
    ) -> dict[str, object]:
        """Return a bounded, cursor-clamped price viewport, never source-future bars."""
        if (
            start.tzinfo is None or end.tzinfo is None or end <= start
            or not 1 <= limit <= 500
        ):
            raise BrowserReplayError("viewport needs ordered UTC bounds and limit 1..500")
        with self._lock:
            session = self._current(run_id)
            if start < session.selected_start or end > session.selected_end:
                raise BrowserReplayError("viewport must lie inside the selected interval")
            visible = [
                step.view.current_bar
                for step in session.pipeline.steps
                if step.is_visible and start <= step.view.timestamp < end
            ][-limit:]
            return {
                "run_id": run_id,
                "cursor_index": session.pipeline.cursor_index,
                "cursor_time": (
                    session.pipeline.steps[-1].view.timestamp
                    if session.pipeline.steps else None
                ),
                "bars": [{
                    "timestamp": bar.timestamp,
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                } for bar in visible],
                "limit": limit,
            }

    def visible_view(
        self, run_id: str, start: datetime, end: datetime, limit: int,
    ) -> dict[str, object]:
        """One coherent, causal chart and event snapshot from committed steps."""
        if (start.tzinfo is None or end.tzinfo is None or end <= start
                or not 1 <= limit <= 500):
            raise BrowserReplayError("viewport needs ordered UTC bounds and limit 1..500")
        with self._lock:
            session = self._current(run_id)
            if start < session.selected_start or end > session.selected_end:
                raise BrowserReplayError("viewport must lie inside the selected interval")
            steps = tuple(step for step in session.pipeline.steps if step.is_visible)
            viewport = tuple(
                step for step in steps if start <= step.view.timestamp < end
            )[-limit:]
            observations = []
            for step in viewport:
                frame = json.loads(step.result.frame.debug_json())
                observations.append({
                    "timestamp": frame["bar"]["timestamp"],
                    "availability": frame["availability"],
                    "components": frame["components"],
                    "market_events": frame["market_events_this_bar"],
                })
            events: list[dict[str, object]] = []
            for step in steps:
                for event in step.result.events:
                    definition = PATTERN_DEFINITIONS.get(
                        (event.pattern_id, event.pattern_version)
                    )
                    # Preserve the runtime's within-bar emission order, even
                    # when two instances emit at the same detection timestamp.
                    events.append({
                        **event.to_canonical_dict(),
                        "pattern_name": definition.name if definition else None,
                        "definition_fingerprint": (
                            definition.semantic_fingerprint() if definition else None
                        ),
                        "build_id": session.pipeline.snapshot.build_id,
                        "code_revision": session.pipeline.snapshot.code_revision,
                        "code_dirty": session.pipeline.snapshot.code_dirty,
                        "code_capture_status": session.pipeline.snapshot.code_capture_status,
                        "emission_order": len(events),
                    })
            return {
                "run_id": run_id,
                "cursor_index": session.pipeline.cursor_index,
                "cursor_time": steps[-1].view.timestamp if steps else None,
                "bars": [{
                    "timestamp": step.view.timestamp,
                    "open": str(step.view.current_bar.open),
                    "high": str(step.view.current_bar.high),
                    "low": str(step.view.current_bar.low),
                    "close": str(step.view.current_bar.close),
                } for step in viewport],
                "observations": observations,
                "events": events,
                "limit": limit,
            }


browser_replay = BrowserReplayManager()
