"""Run-scoped PatternInstance rows and immutable ordered lifecycle evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import LifecycleRunner, PatternDefinition, PatternDefinitionError
from market_analysis.patterns.instance_context import (
    PatternInstanceError,
    decode_context,
    digest_value,
    encode_context,
    instance_semantic_key,
    utc_text,
    utc_time,
)
from market_analysis.persistence.runs import RunSnapshotRecord, load_run_snapshot, metadata

pattern_instances = Table(
    "pattern_instances",
    metadata,
    Column("instance_id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id"), nullable=False),
    Column("instance_semantic_key", String(64), nullable=False),
    Column("dataset_revision_id", String(200), nullable=False),
    Column("instrument_id", String(200), nullable=False),
    Column("timeframe", String(10), nullable=False),
    Column("detection_config_hash", String(64), nullable=False),
    Column("calendar_version", String(200), nullable=False),
    Column("build_id", String(200), nullable=False),
    Column("binding_fingerprint", String(64), nullable=False),
    Column("pattern_id", String(200), nullable=False),
    Column("pattern_version", String(100), nullable=False),
    Column("definition_fingerprint", String(64), nullable=False),
    Column("occurrence_event_time", DateTime(timezone=True), nullable=False),
    Column("occurrence_detection_time", DateTime(timezone=True), nullable=False),
    Column("occurrence_ordinal", Integer, nullable=False),
    Column("state", String(100), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("last_bar_time", DateTime(timezone=True)),
    Column("context_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "instance_semantic_key", name="uq_pattern_instance_run_semantic"),
    CheckConstraint("revision >= 0 AND occurrence_ordinal >= 0", name="ck_pattern_progress"),
    CheckConstraint(
        "occurrence_event_time <= occurrence_detection_time", name="ck_pattern_occurrence_time"
    ),
)
Index("ix_pattern_instances_run", pattern_instances.c.run_id)

pattern_instance_transitions = Table(
    "pattern_instance_transitions",
    metadata,
    Column(
        "instance_id", String(36), ForeignKey("pattern_instances.instance_id"), primary_key=True
    ),
    Column("sequence", Integer, primary_key=True),
    Column("event_semantic_ref", String(64), nullable=False),
    Column("bar_time", DateTime(timezone=True), nullable=False),
    Column("event_time", DateTime(timezone=True), nullable=False),
    Column("detection_time", DateTime(timezone=True), nullable=False),
    Column("from_state", String(100), nullable=False),
    Column("to_state", String(100), nullable=False),
    Column("trigger_id", String(200), nullable=False),
    CheckConstraint("sequence >= 0", name="ck_pattern_transition_sequence"),
    CheckConstraint("event_time <= detection_time", name="ck_pattern_transition_time"),
    CheckConstraint("bar_time = detection_time", name="ck_pattern_transition_bar_time"),
)


def _uuid(value: UUID | str, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise PatternInstanceError(f"{name} must be a UUID") from exc


def _stored_time(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LifecycleStep:
    from_state: str
    to_state: str
    trigger_id: str
    event_time: datetime
    detection_time: datetime

    def __post_init__(self) -> None:
        for name in ("from_state", "to_state", "trigger_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PatternInstanceError(f"{name} must be non-empty")
        object.__setattr__(self, "event_time", utc_time(self.event_time, "event_time"))
        object.__setattr__(self, "detection_time", utc_time(self.detection_time, "detection_time"))
        if self.event_time > self.detection_time:
            raise PatternInstanceError("event_time cannot follow detection_time")


@dataclass(frozen=True, slots=True)
class PatternTransitionRecord:
    sequence: int
    event_semantic_ref: str
    bar_time: datetime
    step: LifecycleStep


@dataclass(frozen=True, slots=True)
class PatternInstanceRecord:
    instance_id: str
    run_id: str
    instance_semantic_key: str
    dataset_revision_id: str
    instrument_id: str
    timeframe: Timeframe
    detection_config_hash: str
    calendar_version: str
    build_id: str
    binding_fingerprint: str
    pattern_id: str
    pattern_version: str
    definition_fingerprint: str
    occurrence_event_time: datetime
    occurrence_detection_time: datetime
    occurrence_ordinal: int
    state: str
    revision: int
    last_bar_time: datetime | None
    context: Mapping[str, object]
    transitions: tuple[PatternTransitionRecord, ...]
    created_at: datetime

    @property
    def emitted_event_refs(self) -> tuple[str, ...]:
        return tuple(item.event_semantic_ref for item in self.transitions)

    @property
    def phase_times(self) -> Mapping[str, datetime]:
        """First observable entry time for each declared lifecycle phase."""
        phases: dict[str, datetime] = {}
        for item in self.transitions:
            phases.setdefault(item.step.to_state.lower(), item.step.detection_time)
        return MappingProxyType(phases)

    @property
    def last_event_time(self) -> datetime | None:
        return self.transitions[-1].step.event_time if self.transitions else None

    @property
    def last_detection_time(self) -> datetime | None:
        return self.transitions[-1].step.detection_time if self.transitions else None


def _event_ref(key: str, sequence: int, step: LifecycleStep) -> str:
    payload = {
        "instance_semantic_key": key,
        "sequence": sequence,
        "from_state": step.from_state,
        "to_state": step.to_state,
        "trigger_id": step.trigger_id,
        "event_time": utc_text(step.event_time),
        "detection_time": utc_text(step.detection_time),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(f"detector-transition-ref-v1\n{canonical}".encode()).hexdigest()


def _run_context(
    connection: Connection, run_id: str, definition: PatternDefinition
) -> tuple[RunSnapshotRecord, DetectionAnalysisConfig]:
    snapshot = load_run_snapshot(connection, UUID(run_id))
    if snapshot is None:
        raise PatternInstanceError("run snapshot is unavailable")
    try:
        config = DetectionAnalysisConfig.from_canonical_json(snapshot.detection_config_json)
    except ValueError as exc:
        raise PatternInstanceError("run detection configuration is invalid") from exc
    if not any(
        item.enabled and item.pattern_id == definition.pattern_id
        and item.pattern_version == definition.pattern_version
        for item in config.patterns
    ):
        raise PatternInstanceError("pattern definition is not selected by the run")
    return snapshot, config


def create_pattern_instance(
    connection: Connection,
    *,
    run_id: UUID | str,
    definition: PatternDefinition,
    binding_fingerprint: str,
    occurrence_event_time: datetime,
    occurrence_detection_time: datetime,
    occurrence_ordinal: int,
    at: datetime,
    instance_id: UUID | str | None = None,
) -> PatternInstanceRecord:
    """Create one occurrence; a same-start ordinal separates overlapping peers."""
    selected_run = _uuid(run_id, "run_id")
    selected_id = _uuid(instance_id or uuid4(), "instance_id")
    digest_value(binding_fingerprint, "binding_fingerprint")
    snapshot, config = _run_context(connection, selected_run, definition)
    key = instance_semantic_key(
        dataset_revision_id=snapshot.dataset_revision_id,
        detection_config_hash=snapshot.detection_config_hash,
        instrument_id=config.instrument_id,
        timeframe=config.timeframe.value,
        definition=definition,
        occurrence_event_time=occurrence_event_time,
        occurrence_detection_time=occurrence_detection_time,
        occurrence_ordinal=occurrence_ordinal,
    )
    try:
        with connection.begin_nested():
            connection.execute(pattern_instances.insert().values(
                instance_id=selected_id,
                run_id=selected_run,
                instance_semantic_key=key,
                dataset_revision_id=snapshot.dataset_revision_id,
                instrument_id=config.instrument_id,
                timeframe=config.timeframe.value,
                detection_config_hash=snapshot.detection_config_hash,
                calendar_version=snapshot.calendar_version,
                build_id=snapshot.build_id,
                binding_fingerprint=binding_fingerprint,
                pattern_id=definition.pattern_id,
                pattern_version=definition.pattern_version,
                definition_fingerprint=definition.semantic_fingerprint(),
                occurrence_event_time=utc_time(occurrence_event_time, "occurrence_event_time"),
                occurrence_detection_time=utc_time(
                    occurrence_detection_time, "occurrence_detection_time"
                ),
                occurrence_ordinal=occurrence_ordinal,
                state=definition.lifecycle_states[0],
                revision=0,
                last_bar_time=None,
                context_json=encode_context(definition, {}),
                created_at=utc_time(at, "created_at"),
            ))
    except IntegrityError as exc:
        raise PatternInstanceError(
            "pattern occurrence identity already exists in this run"
        ) from exc
    created = load_pattern_instance(connection, selected_id, definition)
    assert created is not None
    return created


def load_pattern_instance(
    connection: Connection, instance_id: UUID | str, definition: PatternDefinition
) -> PatternInstanceRecord | None:
    selected_id = _uuid(instance_id, "instance_id")
    row = connection.execute(
        select(pattern_instances).where(pattern_instances.c.instance_id == selected_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    if (
        (row["pattern_id"], row["pattern_version"]) != definition.identity
        or row["definition_fingerprint"] != definition.semantic_fingerprint()
    ):
        raise PatternInstanceError("incompatible PatternDefinition version or semantics")
    snapshot, config = _run_context(connection, row["run_id"], definition)
    if (
        row["dataset_revision_id"] != snapshot.dataset_revision_id
        or row["detection_config_hash"] != snapshot.detection_config_hash
        or row["calendar_version"] != snapshot.calendar_version
        or row["build_id"] != snapshot.build_id
        or row["instrument_id"] != config.instrument_id
        or row["timeframe"] != config.timeframe.value
    ):
        raise PatternInstanceError("pattern instance run lineage differs")
    key = instance_semantic_key(
        dataset_revision_id=row["dataset_revision_id"],
        detection_config_hash=row["detection_config_hash"],
        instrument_id=row["instrument_id"],
        timeframe=row["timeframe"],
        definition=definition,
        occurrence_event_time=_stored_time(row["occurrence_event_time"]),
        occurrence_detection_time=_stored_time(row["occurrence_detection_time"]),
        occurrence_ordinal=row["occurrence_ordinal"],
    )
    if key != row["instance_semantic_key"]:
        raise PatternInstanceError("pattern occurrence semantic key differs")
    digest_value(row["binding_fingerprint"], "binding_fingerprint")
    context = decode_context(definition, row["context_json"])
    transitions = _load_transitions(connection, selected_id, key, definition, dict(row))
    last_bar = None if row["last_bar_time"] is None else _stored_time(row["last_bar_time"])
    if (transitions and row["revision"] == 0) or (transitions and last_bar is None):
        raise PatternInstanceError("pattern instance revision is behind its transitions")
    if transitions and last_bar is not None and transitions[-1].bar_time > last_bar:
        raise PatternInstanceError("pattern transition occurs after the last processed bar")
    return PatternInstanceRecord(
        selected_id, row["run_id"], key, row["dataset_revision_id"],
        row["instrument_id"], Timeframe(row["timeframe"]), row["detection_config_hash"],
        row["calendar_version"], row["build_id"], row["binding_fingerprint"],
        row["pattern_id"], row["pattern_version"], row["definition_fingerprint"],
        _stored_time(row["occurrence_event_time"]),
        _stored_time(row["occurrence_detection_time"]), row["occurrence_ordinal"],
        row["state"], row["revision"], last_bar, context, transitions,
        _stored_time(row["created_at"]),
    )


def _load_transitions(
    connection: Connection,
    instance_id: str,
    key: str,
    definition: PatternDefinition,
    instance_row: Mapping[str, object],
) -> tuple[PatternTransitionRecord, ...]:
    rows = connection.execute(
        select(pattern_instance_transitions)
        .where(pattern_instance_transitions.c.instance_id == instance_id)
        .order_by(pattern_instance_transitions.c.sequence)
    ).mappings()
    records: list[PatternTransitionRecord] = []
    state = definition.lifecycle_states[0]
    previous_detection: datetime | None = None
    previous_trigger: str | None = None
    allowed = {
        (edge.from_state, edge.to_state, edge.trigger_id) for edge in definition.transitions
    }
    for row in rows:
        step = LifecycleStep(
            row["from_state"], row["to_state"], row["trigger_id"],
            _stored_time(row["event_time"]), _stored_time(row["detection_time"]),
        )
        bar_time = _stored_time(row["bar_time"])
        if (
            row["sequence"] != len(records)
            or step.from_state != state
            or (step.from_state, step.to_state, step.trigger_id) not in allowed
            or state in definition.effective_terminal_states
            or bar_time != step.detection_time
            or step.detection_time < _stored_time(
                cast(datetime, instance_row["occurrence_detection_time"])
            )
        ):
            raise PatternInstanceError("persisted lifecycle transition is invalid")
        if previous_detection is not None:
            if step.detection_time < previous_detection:
                raise PatternInstanceError("persisted transitions are out of time order")
            if step.detection_time == previous_detection and (
                previous_trigger, step.trigger_id
            ) not in definition.same_bar_chains:
                raise PatternInstanceError("persisted same-bar transition is undeclared")
        if row["event_semantic_ref"] != _event_ref(key, len(records), step):
            raise PatternInstanceError("persisted transition semantic reference differs")
        records.append(
            PatternTransitionRecord(len(records), row["event_semantic_ref"], bar_time, step)
        )
        state = step.to_state
        previous_detection = step.detection_time
        previous_trigger = step.trigger_id
    if state != instance_row["state"]:
        raise PatternInstanceError("persisted lifecycle state differs from transition history")
    return tuple(records)


def advance_pattern_instance(
    connection: Connection,
    instance_id: UUID | str,
    definition: PatternDefinition,
    *,
    expected_revision: int,
    bar: Bar,
    steps: Sequence[LifecycleStep],
    context: Mapping[str, object],
) -> PatternInstanceRecord:
    """Atomically record one completed bar's context and ordered transitions."""
    record = load_pattern_instance(connection, instance_id, definition)
    if record is None:
        raise PatternInstanceError("pattern instance is unavailable")
    if type(expected_revision) is not int or expected_revision != record.revision:
        raise PatternInstanceError("stale pattern instance revision")
    if record.state in definition.effective_terminal_states:
        raise PatternInstanceError("terminal pattern instance cannot advance")
    if not isinstance(bar, Bar) or not bar.is_complete:
        raise PatternInstanceError("instance advances only on a completed canonical Bar")
    if (bar.instrument_id, bar.timeframe) != (record.instrument_id, record.timeframe):
        raise PatternInstanceError("bar identity differs from pattern instance")
    if record.last_bar_time is not None and bar.timestamp <= record.last_bar_time:
        raise PatternInstanceError("pattern instance bars must advance strictly")
    if bar.timestamp < record.occurrence_detection_time:
        raise PatternInstanceError("bar precedes observable occurrence start")
    proposed = tuple(steps)
    if any(
        not isinstance(step, LifecycleStep) or step.detection_time != bar.timestamp
        for step in proposed
    ):
        raise PatternInstanceError("transition detection_time must equal this completed bar")
    runner = LifecycleRunner(definition, initial_state=record.state)
    try:
        derived = runner.advance(bar, (step.trigger_id for step in proposed))
    except PatternDefinitionError as exc:
        raise PatternInstanceError(f"lifecycle rejected transition: {exc}") from exc
    if tuple((item.from_state, item.to_state, item.trigger_id) for item in derived) != tuple(
        (item.from_state, item.to_state, item.trigger_id) for item in proposed
    ):
        raise PatternInstanceError("transition chain differs from declared lifecycle")
    encoded = encode_context(definition, context)
    selected_id = record.instance_id
    try:
        with connection.begin_nested():
            result = connection.execute(
                update(pattern_instances)
                .where(
                    pattern_instances.c.instance_id == selected_id,
                    pattern_instances.c.revision == expected_revision,
                    pattern_instances.c.state == record.state,
                )
                .values(
                    state=runner.state,
                    revision=expected_revision + 1,
                    last_bar_time=bar.timestamp,
                    context_json=encoded,
                )
            )
            if result.rowcount != 1:
                raise PatternInstanceError("stale pattern instance revision")
            for offset, step in enumerate(proposed):
                sequence = len(record.transitions) + offset
                connection.execute(pattern_instance_transitions.insert().values(
                    instance_id=selected_id,
                    sequence=sequence,
                    event_semantic_ref=_event_ref(record.instance_semantic_key, sequence, step),
                    bar_time=bar.timestamp,
                    event_time=step.event_time,
                    detection_time=step.detection_time,
                    from_state=step.from_state,
                    to_state=step.to_state,
                    trigger_id=step.trigger_id,
                ))
    except IntegrityError as exc:
        raise PatternInstanceError("concurrent pattern transition conflicts") from exc
    changed = load_pattern_instance(connection, selected_id, definition)
    assert changed is not None
    return changed
