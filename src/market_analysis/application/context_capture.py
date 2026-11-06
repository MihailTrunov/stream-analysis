"""Capture emitting detector frames through the append-only context repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Connection

from market_analysis.detection.context_snapshots import (
    CONTEXT_SCHEMA_VERSION,
    ContextSnapshotError,
    DetectionTimeContextSnapshot,
    canonical_json,
    capture_values,
)
from market_analysis.detection.records import DetectorEvent, PatternInstance, json_value
from market_analysis.indicators import MarketStateFrame
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.detection_contexts import insert_context_row, load_context_row
from market_analysis.persistence.pattern_instances import (
    PersistedDetectorEvent,
    load_pattern_instance,
)
from market_analysis.persistence.runs import load_run_snapshot


def _record(row: Any) -> DetectionTimeContextSnapshot:
    try:
        payload = json.loads(row["payload_json"])
        for name in ("detection_time", "available_at"):
            payload[name] = datetime.fromisoformat(payload[name])
        record = DetectionTimeContextSnapshot(**payload)
        if record.content_hash != row["content_hash"] or any(
            getattr(record, name) != row[name]
            for name in ("snapshot_id", "event_id", "run_id", "context_schema_version")
        ):
            raise ContextSnapshotError("stored context hash or identity mismatch")
        return record
    except (TypeError, KeyError, ValueError) as exc:
        raise ContextSnapshotError("invalid stored detection-time context") from exc


def load_detection_context(
    connection: Connection, event_id: str,
    context_schema_version: str = CONTEXT_SCHEMA_VERSION,
) -> DetectionTimeContextSnapshot | None:
    row = load_context_row(connection, event_id, context_schema_version)
    return None if row is None else _record(row)


def capture_detection_context(
    connection: Connection, *, frame: MarketStateFrame, event: DetectorEvent,
    instance: PatternInstance | None, stored: PersistedDetectorEvent,
    definition: PatternDefinition, available_at: datetime,
) -> DetectionTimeContextSnapshot:
    """Capture in the source event's transaction, before any subsequent candle."""
    if (
        event.run_id != stored.run_id
        or event.dataset_revision_id != stored.dataset_revision_id
        or event.detection_config_hash != stored.detection_config_hash
        or event.instrument_id != stored.instrument_id
        or event.pattern_id != stored.pattern_id
        or event.pattern_version != stored.pattern_version
        or event.timeframe != stored.timeframe.value
        or event.sequence != stored.sequence
        or event.from_state != stored.old_state
        or event.to_state != stored.new_state
        or event.trigger_id != stored.transition_reason
        or event.event_time != stored.event_time
        or event.detection_time != stored.detection_time
        or json_value(event.rationale) != json_value(stored.rationale)
    ):
        raise ContextSnapshotError("snapshot source differs from persisted detector event")
    occurrence = load_pattern_instance(connection, stored.instance_id, definition)
    if occurrence is None or next((
        item for item in occurrence.detector_events if item.event_id == stored.event_id
    ), None) != stored:
        raise ContextSnapshotError("snapshot source is not the persisted detector event")
    run = load_run_snapshot(connection, UUID(stored.run_id))
    if run is None or run.dataset_revision_id != frame.dataset_revision_id or (
        run.detection_config_hash != frame.detection_config_hash
    ):
        raise ContextSnapshotError("snapshot frame differs from pinned run")
    if run.evaluation_plan_json is not None and json.loads(run.evaluation_plan_json).get(
        "context_schema_version"
    ) != CONTEXT_SCHEMA_VERSION:
        raise ContextSnapshotError("evaluation plan requests unsupported context_schema_version")
    snapshot = DetectionTimeContextSnapshot(
        snapshot_id=str(uuid5(UUID(stored.event_id), CONTEXT_SCHEMA_VERSION)),
        event_id=stored.event_id, run_id=stored.run_id,
        source_event_semantic_ref=stored.event_semantic_key,
        instance_semantic_key=stored.instance_semantic_key,
        instrument_id=stored.instrument_id, detection_time=event.detection_time,
        available_at=available_at, within_bar_ordinal=stored.within_bar_ordinal,
        context_schema_version=CONTEXT_SCHEMA_VERSION,
        lineage={
            "dataset_revision_id": run.dataset_revision_id,
            "timeframe": event.timeframe,
            "detection_config_hash": run.detection_config_hash,
            "calendar_version": run.calendar_version,
            "config_schema_version": run.config_schema_version,
            "build_id": run.build_id, "code_revision": run.code_revision,
            "code_dirty": run.code_dirty, "code_capture_status": run.code_capture_status,
            "component_versions": frame.component_versions,
            "pattern_id": definition.pattern_id, "pattern_version": definition.pattern_version,
            "definition_fingerprint": definition.semantic_fingerprint(),
        },
        values=capture_values(frame, event, instance, definition),
    )
    existing = load_detection_context(connection, stored.event_id)
    if existing is not None:
        if existing != snapshot:
            raise ContextSnapshotError("immutable context already captured with different values")
        return existing
    insert_context_row(connection, {
        "snapshot_id": snapshot.snapshot_id, "event_id": snapshot.event_id,
        "run_id": snapshot.run_id, "context_schema_version": snapshot.context_schema_version,
        "payload_json": canonical_json(snapshot.to_dict()), "content_hash": snapshot.content_hash,
    })
    return snapshot
