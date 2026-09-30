"""Create and reload pinned ReplayRuns without exposing future market data."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Connection

from market_analysis.code_version import CodeVersion, capture_code_version
from market_analysis.config import DetectionAnalysisConfig, resolve_detection_config
from market_analysis.domain import DatasetLineage, SimulationClock, ValidationStatus
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
    advance_replay_cursor,
    insert_replay_run,
    load_replay_run,
    transition_replay_run,
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
            "code_version": {
                "revision": self.snapshot.code_revision,
                "dirty": self.snapshot.code_dirty,
                "status": self.snapshot.code_capture_status,
                "source": self.snapshot.code_capture_source,
            },
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
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
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
            revision_provider=revision_provider,
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


def _validate_replay_clock(context: ReplayRunContext, clock: SimulationClock) -> None:
    if (
        clock.dataset_revision_id != context.snapshot.dataset_revision_id
        or clock.source_dataset_id != context.lineage.source_dataset_id
        or clock.canonical_checksum != context.lineage.canonical_checksum
        or clock.source_bar_count != context.lifecycle.source_bar_count
        or clock.selected_start != context.lifecycle.selected_start
        or clock.selected_end != context.lifecycle.selected_end
        or clock.index != context.lifecycle.cursor_index
    ):
        raise ReplayRunError("clock does not match pinned replay context and cursor")


def step_replay(
    connection: Connection,
    run_id: UUID,
    clock: SimulationClock,
    *,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
) -> None:
    """Advance a matched clock and persisted cursor by exactly one bar."""
    context = load_replay_context(
        connection, run_id, component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    if context is None:
        raise ReplayRunError("replay run does not exist")
    _validate_replay_clock(context, clock)
    if not clock.has_next:
        raise ReplayRunError("clock has no next observable bar")
    advance_replay_cursor(connection, run_id, clock.index + 1)
    clock.step()


def complete_replay(
    connection: Connection,
    run_id: UUID,
    clock: SimulationClock,
    *,
    at: datetime,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
) -> ReplayRunRecord:
    """Complete only after the selected clock interval has been exhausted."""
    context = load_replay_context(
        connection, run_id, component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    if context is None:
        raise ReplayRunError("replay run does not exist")
    _validate_replay_clock(context, clock)
    if clock.has_next:
        raise ReplayRunError("clock has not exhausted the selected interval")
    return transition_replay_run(connection, run_id, ReplayStatus.COMPLETED, at=at, exhausted=True)
