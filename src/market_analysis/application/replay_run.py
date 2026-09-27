"""Create and reload pinned ReplayRuns without exposing future market data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Connection

from market_analysis.config import DetectionAnalysisConfig, resolve_detection_config
from market_analysis.domain import DatasetLineage, ValidationStatus
from market_analysis.patterns import ParameterSpec, PatternDefinition
from market_analysis.persistence.market_data import (
    load_dataset_lineage,
    load_dataset_revision,
    load_instrument,
)
from market_analysis.persistence.replay_runs import (
    ReplayRunError,
    ReplayRunRecord,
    ReplayStatus,
    insert_replay_run,
    load_replay_run,
)
from market_analysis.persistence.runs import (
    RunSnapshotRecord,
    create_run_snapshot,
    load_run_snapshot,
)


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReplayRunContext:
    """Pinned identity/configuration and current lifecycle, but no bar array."""

    snapshot: RunSnapshotRecord
    lifecycle: ReplayRunRecord
    lineage: DatasetLineage
    detection_config: DetectionAnalysisConfig

    def canonical_json(self) -> str:
        payload = {
            "run_id": self.lifecycle.run_id,
            "dataset_revision_id": self.snapshot.dataset_revision_id,
            "source_dataset_id": self.lineage.source_dataset_id,
            "detection_config": json.loads(self.snapshot.detection_config_json),
            "detection_config_hash": self.snapshot.detection_config_hash,
            "calendar_version": self.snapshot.calendar_version,
            "build_id": self.snapshot.build_id,
            "selected_start": _stamp(self.lifecycle.selected_start),
            "selected_end": _stamp(self.lifecycle.selected_end),
            "status": self.lifecycle.status.value,
            "cursor_index": self.lifecycle.cursor_index,
            "created_at": _stamp(self.lifecycle.created_at),
            "completed_at": _stamp(self.lifecycle.completed_at),
            "failure_reason": self.lifecycle.failure_reason,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def create_replay_run(
    connection: Connection,
    *,
    run_id: UUID,
    dataset_revision_id: str,
    detection_config: DetectionAnalysisConfig,
    selected_start: datetime,
    selected_end: datetime,
    created_at: datetime,
    build_id: str,
    preset_id: str | None = None,
    preset_revision: int | None = None,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
) -> ReplayRunContext:
    """Atomically pin a resolved snapshot and initial lifecycle row."""
    revision = load_dataset_revision(connection, dataset_revision_id)
    if revision is None:
        raise ReplayRunError("dataset revision does not exist")
    instrument = load_instrument(connection, detection_config.instrument_id)
    if instrument is None or instrument.calendar_id != detection_config.calendar_id:
        raise ReplayRunError("run calendar identity differs from dataset instrument")
    lineage = load_dataset_lineage(
        connection, dataset_revision_id, detection_config.instrument_id,
        detection_config.timeframe,
    )
    if (
        lineage is None
        or lineage.bar_count == 0
        or lineage.validation_status is ValidationStatus.FAIL
    ):
        raise ReplayRunError("dataset revision has no validated nonempty lineage")
    resolved = resolve_detection_config(
        detection_config,
        component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    lifecycle = ReplayRunRecord(
        run_id=str(run_id), selected_start=selected_start, selected_end=selected_end,
        status=ReplayStatus.CREATED, cursor_index=-1,
        source_bar_count=lineage.bar_count, created_at=created_at,
    )
    if not (
        lineage.requested_start <= lifecycle.selected_start
        < lifecycle.selected_end <= lineage.requested_end
    ):
        raise ReplayRunError("selected replay range must lie within dataset range")
    with connection.begin_nested():
        snapshot = create_run_snapshot(
            connection,
            run_id=run_id,
            dataset_revision_id=dataset_revision_id,
            calendar_version=revision.calendar_version,
            build_id=build_id,
            detection_config=resolved,
            preset_id=preset_id,
            preset_revision=preset_revision,
            component_parameters=component_parameters,
            pattern_definitions=pattern_definitions,
        )
        insert_replay_run(connection, lifecycle)
    canonical_config = DetectionAnalysisConfig.from_canonical_json(snapshot.detection_config_json)
    return ReplayRunContext(snapshot, lifecycle, lineage, canonical_config)


def load_replay_context(
    connection: Connection,
    run_id: UUID,
    *,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
) -> ReplayRunContext | None:
    lifecycle = load_replay_run(connection, run_id)
    if lifecycle is None:
        return None
    snapshot = load_run_snapshot(connection, run_id)
    if snapshot is None or snapshot.run_kind != "replay":
        raise ReplayRunError("replay lifecycle has no replay snapshot")
    expected_hash = sha256(
        f"{snapshot.detection_hash_version}\n{snapshot.detection_config_json}".encode()
    ).hexdigest()
    if expected_hash != snapshot.detection_config_hash:
        raise ReplayRunError("replay detection snapshot hash differs from stored payload")
    parsed = DetectionAnalysisConfig.from_canonical_json(snapshot.detection_config_json)
    resolved = resolve_detection_config(
        parsed,
        component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    if resolved.canonical_json() != snapshot.detection_config_json:
        raise ReplayRunError("replay resolved configuration changed on reload")
    lineage = load_dataset_lineage(
        connection, snapshot.dataset_revision_id, resolved.instrument_id, resolved.timeframe
    )
    revision = load_dataset_revision(connection, snapshot.dataset_revision_id)
    instrument = load_instrument(connection, resolved.instrument_id)
    if lineage is None or revision is None:
        raise ReplayRunError("replay dataset revision or lineage is missing")
    if instrument is None or instrument.calendar_id != resolved.calendar_id:
        raise ReplayRunError("replay calendar identity differs from dataset instrument")
    if revision.calendar_version != snapshot.calendar_version:
        raise ReplayRunError("replay calendar version differs from dataset revision")
    if lineage.bar_count != lifecycle.source_bar_count:
        raise ReplayRunError("replay source bar count differs from pinned lineage")
    return ReplayRunContext(snapshot, lifecycle, lineage, resolved)
