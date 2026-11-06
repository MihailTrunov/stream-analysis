"""Audited research judgments, stored separately from immutable detector evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError

from market_analysis.domain import Timeframe
from market_analysis.persistence.analysis_lineage import load_analysis_lineage
from market_analysis.persistence.live_analysis import live_analysis_advances, live_analysis_inputs
from market_analysis.persistence.market_data import dataset_memberships
from market_analysis.persistence.pattern_instances import (
    pattern_instance_transitions,
    pattern_instances,
)
from market_analysis.persistence.runs import metadata

REVIEW_LABELS = frozenset(
    {"correct", "incorrect", "partially_correct", "missed_context", "needs_review"}
)
MISSED_PATTERN_LABELS = frozenset({"missed_pattern", "needs_review"})


class AnnotationError(ValueError):
    """An annotation target, revision, or judgment is invalid."""


validation_annotations = Table(
    "validation_annotations",
    metadata,
    Column("annotation_id", String(36), primary_key=True),
    Column("target_kind", String(20), nullable=False),
    Column("event_id", String(36), ForeignKey("pattern_instance_transitions.event_id")),
    Column("instance_id", String(36), ForeignKey("pattern_instances.instance_id")),
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id")),
    Column("dataset_revision_id", String(200), nullable=False),
    Column(
        "missed_dataset_revision_id",
        String(200),
        ForeignKey("dataset_revisions.dataset_revision_id"),
    ),
    Column("missed_live_input_id", String(200), ForeignKey("live_analysis_inputs.input_id")),
    Column("instrument_id", String(200), nullable=False),
    Column("timeframe", String(10), nullable=False),
    Column("pattern_id", String(200), nullable=False),
    Column("pattern_version", String(100), nullable=False),
    Column("detection_config_hash", String(64)),
    Column("calendar_version", String(200)),
    Column("build_id", String(200)),
    Column("interval_start", DateTime(timezone=True)),
    Column("interval_end", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(target_kind = 'event' AND event_id IS NOT NULL AND instance_id IS NOT NULL "
        "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
        "AND missed_live_input_id IS NULL AND interval_start IS NULL AND interval_end IS NULL) OR "
        "(target_kind = 'instance' AND event_id IS NULL AND instance_id IS NOT NULL "
        "AND run_id IS NOT NULL AND missed_dataset_revision_id IS NULL "
        "AND missed_live_input_id IS NULL AND interval_start IS NULL AND interval_end IS NULL) OR "
        "(target_kind = 'missed_pattern' AND event_id IS NULL AND instance_id IS NULL "
        "AND run_id IS NULL AND ((missed_dataset_revision_id IS NOT NULL "
        "AND missed_dataset_revision_id = dataset_revision_id "
        "AND missed_live_input_id IS NULL) OR (missed_live_input_id IS NOT NULL "
        "AND missed_live_input_id = dataset_revision_id "
        "AND missed_dataset_revision_id IS NULL)) "
        "AND interval_start IS NOT NULL AND interval_end IS NOT NULL "
        "AND interval_end > interval_start)",
        name="ck_validation_annotation_target",
    ),
)
Index("ix_validation_annotations_event", validation_annotations.c.event_id)
Index("ix_validation_annotations_instance", validation_annotations.c.instance_id)
Index("ix_validation_annotations_dataset", validation_annotations.c.dataset_revision_id)

validation_annotation_revisions = Table(
    "validation_annotation_revisions",
    metadata,
    Column(
        "annotation_id",
        String(36),
        ForeignKey("validation_annotations.annotation_id"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("label", String(30), nullable=False),
    Column("note", Text),
    Column("reviewer_id", String(200)),
    Column("changed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_annotation_revision_positive"),
)


analysis_annotation_lineage = Table(
    "analysis_annotation_lineage",
    metadata,
    Column(
        "annotation_id",
        String(36),
        ForeignKey("validation_annotations.annotation_id"),
        primary_key=True,
    ),
    Column("lineage_json", Text, nullable=False),
)


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AnnotationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _identity(value: str, name: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise AnnotationError(f"{name} must be a UUID") from exc


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnnotationError(f"{name} must be non-empty")
    return value.strip()


def _optional_text(value: str | None, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _label(value: str, kind: str) -> str:
    permitted = MISSED_PATTERN_LABELS if kind == "missed_pattern" else REVIEW_LABELS
    if value not in permitted:
        raise AnnotationError(f"unsupported {kind} review label: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class AnnotationRevision:
    revision: int
    label: str
    note: str | None
    reviewer_id: str | None
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class ValidationAnnotation:
    annotation_id: str
    target_kind: str
    event_id: str | None
    instance_id: str | None
    run_id: str | None
    dataset_revision_id: str
    instrument_id: str
    timeframe: Timeframe
    pattern_id: str
    pattern_version: str
    detection_config_hash: str | None
    calendar_version: str | None
    build_id: str | None
    interval_start: datetime | None
    interval_end: datetime | None
    created_at: datetime
    history: tuple[AnnotationRevision, ...]
    analysis_lineage: Mapping[str, object] | None = None

    @property
    def current(self) -> AnnotationRevision:
        return self.history[-1]


def load_annotation(connection: Connection, annotation_id: str) -> ValidationAnnotation | None:
    row = (
        connection.execute(
            select(validation_annotations).where(
                validation_annotations.c.annotation_id == _identity(annotation_id, "annotation_id")
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    revisions = (
        connection.execute(
            select(validation_annotation_revisions)
            .where(validation_annotation_revisions.c.annotation_id == annotation_id)
            .order_by(validation_annotation_revisions.c.revision)
        )
        .mappings()
        .all()
    )
    if not revisions or [item["revision"] for item in revisions] != list(
        range(1, len(revisions) + 1)
    ):
        raise AnnotationError("annotation revision history is incomplete")
    lineage_json = connection.scalar(
        select(analysis_annotation_lineage.c.lineage_json).where(
            analysis_annotation_lineage.c.annotation_id == annotation_id
        )
    )
    return ValidationAnnotation(
        analysis_lineage=None if lineage_json is None else json.loads(lineage_json),
        annotation_id=row["annotation_id"],
        target_kind=row["target_kind"],
        event_id=row["event_id"],
        instance_id=row["instance_id"],
        run_id=row["run_id"],
        dataset_revision_id=row["dataset_revision_id"],
        instrument_id=row["instrument_id"],
        timeframe=Timeframe(row["timeframe"]),
        pattern_id=row["pattern_id"],
        pattern_version=row["pattern_version"],
        detection_config_hash=row["detection_config_hash"],
        calendar_version=row["calendar_version"],
        build_id=row["build_id"],
        interval_start=None
        if row["interval_start"] is None
        else _stored_utc(row["interval_start"]),
        interval_end=None if row["interval_end"] is None else _stored_utc(row["interval_end"]),
        created_at=_stored_utc(row["created_at"]),
        history=tuple(
            AnnotationRevision(
                revision=item["revision"],
                label=item["label"],
                note=item["note"],
                reviewer_id=item["reviewer_id"],
                changed_at=_stored_utc(item["changed_at"]),
            )
            for item in revisions
        ),
    )


def _create(
    connection: Connection,
    *,
    target: dict[str, object],
    label: str,
    note: str | None,
    reviewer_id: str | None,
    at: datetime | None,
) -> ValidationAnnotation:
    kind = str(target["target_kind"])
    now = _utc(at or datetime.now(UTC), "at")
    label = _label(label, kind)
    reviewer_id = _optional_text(reviewer_id, "reviewer_id")
    if note is not None and not isinstance(note, str):
        raise AnnotationError("note must be text or None")
    annotation_id = str(uuid4())
    try:
        with connection.begin_nested():
            connection.execute(
                validation_annotations.insert().values(
                    annotation_id=annotation_id,
                    created_at=now,
                    **target,
                )
            )
            connection.execute(
                validation_annotation_revisions.insert().values(
                    annotation_id=annotation_id,
                    revision=1,
                    label=label,
                    note=note,
                    reviewer_id=reviewer_id,
                    changed_at=now,
                )
            )
    except IntegrityError as exc:
        raise AnnotationError("annotation target is no longer available") from exc
    result = load_annotation(connection, annotation_id)
    assert result is not None
    return result


def _instance_target(row: object, *, event_id: str | None, kind: str) -> dict[str, object]:
    # SQLAlchemy RowMapping is deliberately accessed through the mapping protocol.
    item = cast(Mapping[str, object], row)
    return {
        "target_kind": kind,
        "event_id": event_id,
        "instance_id": item["instance_id"],
        "run_id": item["run_id"],
        "dataset_revision_id": item["dataset_revision_id"],
        "instrument_id": item["instrument_id"],
        "timeframe": item["timeframe"],
        "pattern_id": item["pattern_id"],
        "pattern_version": item["pattern_version"],
        "detection_config_hash": item["detection_config_hash"],
        "calendar_version": item["calendar_version"],
        "build_id": item["build_id"],
    }


def create_event_annotation(
    connection: Connection,
    *,
    event_id: str,
    label: str,
    note: str | None = None,
    reviewer_id: str | None = None,
    at: datetime | None = None,
) -> ValidationAnnotation:
    event_id = _identity(event_id, "event_id")
    row = (
        connection.execute(
            select(pattern_instances)
            .join(pattern_instance_transitions)
            .where(pattern_instance_transitions.c.event_id == event_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AnnotationError("target detector event does not exist")
    return _create(
        connection,
        target=_instance_target(row, event_id=event_id, kind="event"),
        label=label,
        note=note,
        reviewer_id=reviewer_id,
        at=at,
    )


def create_instance_annotation(
    connection: Connection,
    *,
    instance_id: str,
    label: str,
    note: str | None = None,
    reviewer_id: str | None = None,
    at: datetime | None = None,
) -> ValidationAnnotation:
    instance_id = _identity(instance_id, "instance_id")
    row = (
        connection.execute(
            select(pattern_instances).where(pattern_instances.c.instance_id == instance_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AnnotationError("target pattern instance does not exist")
    return _create(
        connection,
        target=_instance_target(row, event_id=None, kind="instance"),
        label=label,
        note=note,
        reviewer_id=reviewer_id,
        at=at,
    )


def create_missed_pattern_annotation(
    connection: Connection,
    *,
    dataset_revision_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    interval_start: datetime,
    interval_end: datetime,
    pattern_id: str,
    pattern_version: str,
    label: str = "missed_pattern",
    note: str | None = None,
    reviewer_id: str | None = None,
    at: datetime | None = None,
    analysis_run_id: str | None = None,
) -> ValidationAnnotation:
    revision_id = _text(dataset_revision_id, "dataset_revision_id")
    instrument_id = _text(instrument_id, "instrument_id")
    if not isinstance(timeframe, Timeframe):
        raise AnnotationError("timeframe must be a Timeframe")
    start = _utc(interval_start, "interval_start")
    end = _utc(interval_end, "interval_end")
    if end <= start:
        raise AnnotationError("interval_end must follow interval_start")
    lineage = (
        None if analysis_run_id is None else load_analysis_lineage(connection, analysis_run_id)
    )
    if analysis_run_id is not None and (
        lineage is None
        or lineage.snapshot.dataset_revision_id != revision_id
        or lineage.config.instrument_id != instrument_id
        or lineage.config.timeframe != timeframe
        or not any(
            item.pattern_id == pattern_id
            and item.pattern_version == pattern_version
            and item.enabled
            for item in lineage.config.patterns
        )
    ):
        raise AnnotationError("analytical review target differs from pinned lineage")
    source_timeframe = Timeframe.M1 if lineage is not None else timeframe
    membership = (
        connection.execute(
            select(dataset_memberships).where(
                dataset_memberships.c.dataset_revision_id == revision_id,
                dataset_memberships.c.instrument_id == instrument_id,
                dataset_memberships.c.timeframe == source_timeframe.value,
            )
        )
        .mappings()
        .one_or_none()
    )
    live_input = (
        None
        if lineage is None
        else connection.execute(
            select(live_analysis_inputs).where(
                live_analysis_inputs.c.input_id == revision_id,
                live_analysis_inputs.c.session_id == lineage.session_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if membership is None and live_input is None:
        raise AnnotationError("target dataset revision/instrument/timeframe does not exist")
    if live_input is not None:
        manifest = json.loads(live_input["manifest_json"])
        range_start = datetime.fromisoformat(manifest["initialization_origin"])
        latest = connection.scalar(
            select(func.max(live_analysis_advances.c.available_at)).where(
                live_analysis_advances.c.input_id == revision_id,
            )
        )
        range_end = (
            _stored_utc(latest)
            if latest is not None
            else datetime.fromisoformat(manifest["live_start"])
        )
    else:
        assert membership is not None
        range_start = _stored_utc(membership["range_start"])
        range_end = _stored_utc(membership["range_end"])
    if not range_start <= start < end <= range_end:
        raise AnnotationError("annotation interval is outside dataset membership")
    created = _create(
        connection,
        target={
            "target_kind": "missed_pattern",
            "event_id": None,
            "instance_id": None,
            "run_id": None,
            "dataset_revision_id": revision_id,
            "missed_dataset_revision_id": None if live_input is not None else revision_id,
            "missed_live_input_id": revision_id if live_input is not None else None,
            "instrument_id": instrument_id,
            "timeframe": timeframe.value,
            "pattern_id": _text(pattern_id, "pattern_id"),
            "pattern_version": _text(pattern_version, "pattern_version"),
            "detection_config_hash": None
            if lineage is None
            else lineage.snapshot.detection_config_hash,
            "calendar_version": None if lineage is None else lineage.snapshot.calendar_version,
            "build_id": None if lineage is None else lineage.snapshot.build_id,
            "interval_start": start,
            "interval_end": end,
        },
        label=label,
        note=note,
        reviewer_id=reviewer_id,
        at=at,
    )

    if lineage is not None:
        payload = {
            "analysis_run_id": analysis_run_id,
            "analysis_session_id": lineage.session_id,
            "manifest_hash": lineage.manifest_hash,
            "source_timeframe": "1m",
            "analytical_timeframe": timeframe.value,
            "aggregation_version": lineage.manifest["aggregation"],
            "source_checksum": lineage.manifest["source_checksum"],
        }
        connection.execute(
            analysis_annotation_lineage.insert().values(
                annotation_id=created.annotation_id,
                lineage_json=json.dumps(payload, sort_keys=True),
            )
        )
        loaded = load_annotation(connection, created.annotation_id)
        assert loaded is not None
        return loaded
    return created


def revise_annotation(
    connection: Connection,
    annotation_id: str,
    *,
    expected_revision: int,
    label: str,
    note: str | None = None,
    reviewer_id: str | None = None,
    at: datetime | None = None,
) -> ValidationAnnotation:
    annotation = load_annotation(connection, annotation_id)
    if annotation is None:
        raise AnnotationError("annotation does not exist")
    if expected_revision != annotation.current.revision:
        raise AnnotationError("annotation revision conflict")
    now = _utc(at or datetime.now(UTC), "at")
    if now < annotation.current.changed_at:
        raise AnnotationError("revision timestamp cannot precede prior revision")
    label = _label(label, annotation.target_kind)
    reviewer_id = _optional_text(reviewer_id, "reviewer_id")
    if note is not None and not isinstance(note, str):
        raise AnnotationError("note must be text or None")
    try:
        with connection.begin_nested():
            connection.execute(
                validation_annotation_revisions.insert().values(
                    annotation_id=annotation.annotation_id,
                    revision=expected_revision + 1,
                    label=label,
                    note=note,
                    reviewer_id=reviewer_id,
                    changed_at=now,
                )
            )
    except IntegrityError as exc:
        raise AnnotationError("annotation revision conflict") from exc
    result = load_annotation(connection, annotation.annotation_id)
    assert result is not None
    return result


def export_annotation(annotation: ValidationAnnotation) -> dict[str, object]:
    """Separate, JSON-serializable research metadata, including all revisions."""
    return {
        **(
            {"analysis_lineage": dict(annotation.analysis_lineage)}
            if annotation.analysis_lineage is not None
            else {}
        ),
        "annotation_id": annotation.annotation_id,
        "target_kind": annotation.target_kind,
        "event_id": annotation.event_id,
        "instance_id": annotation.instance_id,
        "run_id": annotation.run_id,
        "dataset_revision_id": annotation.dataset_revision_id,
        "instrument_id": annotation.instrument_id,
        "timeframe": annotation.timeframe.value,
        "pattern_id": annotation.pattern_id,
        "pattern_version": annotation.pattern_version,
        "detection_config_hash": annotation.detection_config_hash,
        "calendar_version": annotation.calendar_version,
        "build_id": annotation.build_id,
        "interval_start": annotation.interval_start.isoformat()
        if annotation.interval_start
        else None,
        "interval_end": annotation.interval_end.isoformat() if annotation.interval_end else None,
        "created_at": annotation.created_at.isoformat(),
        "history": [
            {
                "revision": item.revision,
                "label": item.label,
                "note": item.note,
                "reviewer_id": item.reviewer_id,
                "changed_at": item.changed_at.isoformat(),
            }
            for item in annotation.history
        ],
    }


def list_visible_annotations(
    connection: Connection,
    *,
    event_ids: frozenset[str],
    instance_ids: frozenset[str],
    dataset_revision_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    selected_start: datetime,
    visible_end: datetime,
) -> tuple[ValidationAnnotation, ...]:
    """Read reviews only for emitted targets or already visible missed intervals."""
    end = _utc(visible_end, "visible_end")
    start = _utc(selected_start, "selected_start")
    ids = (
        connection.execute(
            select(validation_annotations.c.annotation_id)
            .where(
                or_(
                    and_(
                        validation_annotations.c.target_kind == "event",
                        validation_annotations.c.event_id.in_(event_ids),
                    ),
                    and_(
                        validation_annotations.c.target_kind == "instance",
                        validation_annotations.c.instance_id.in_(instance_ids),
                    ),
                    and_(
                        validation_annotations.c.target_kind == "missed_pattern",
                        validation_annotations.c.dataset_revision_id == dataset_revision_id,
                        validation_annotations.c.instrument_id == instrument_id,
                        validation_annotations.c.timeframe == timeframe.value,
                        validation_annotations.c.interval_start >= start,
                        validation_annotations.c.interval_end <= end,
                    ),
                )
            )
            .order_by(validation_annotations.c.created_at, validation_annotations.c.annotation_id)
        )
        .scalars()
        .all()
    )
    records = tuple(load_annotation(connection, annotation_id) for annotation_id in ids)
    return tuple(record for record in records if record is not None)
