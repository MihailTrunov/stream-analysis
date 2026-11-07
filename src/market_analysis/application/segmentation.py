"""Read-only source-context partitions, independent of downstream outcome values."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, func, select

from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.segment_registry import SEGMENT_DEFINITIONS
from market_analysis.config import EvaluationPlan
from market_analysis.detection.context_snapshots import DetectionTimeContextSnapshot
from market_analysis.domain.evaluation import EvaluationError, EvaluationNotFound, EvaluationStatus
from market_analysis.domain.segments import SegmentBucket, SegmentDefinition, SegmentValue
from market_analysis.persistence.evaluations import EvaluationRunRecord, evaluation_event_sets
from market_analysis.persistence.pattern_instances import (
    pattern_instance_transitions,
    pattern_instances,
)

PAGE_SIZE = 256


def assign_segment(
    definition: SegmentDefinition, instrument_id: str, snapshot: DetectionTimeContextSnapshot
) -> SegmentBucket:
    if snapshot.instrument_id != instrument_id:
        raise EvaluationError("segment source instrument differs from captured context")
    components = snapshot.values.get("components")
    session = components.get("session") if isinstance(components, Mapping) else None
    name = session.get("session_name") if isinstance(session, Mapping) else None
    captured_session = name if isinstance(name, str) and name.strip() else None
    values = tuple(
        SegmentValue(
            field=field,
            value=instrument_id if field == "instrument_id" else captured_session,
            availability="AVAILABLE"
            if field == "instrument_id" or captured_session is not None
            else "UNAVAILABLE",
        )
        for field in definition.fields
    )
    return SegmentBucket(
        segment_id=definition.segment_id,
        segment_version=definition.segment_version,
        definition_fingerprint=definition.fingerprint(),
        values=values,
    )


def pinned_segments(
    record: EvaluationRunRecord, *, for_execution: bool = False
) -> dict[str, tuple[SegmentDefinition, ...]]:
    result = {}
    for stream in record.inputs.streams:
        plan = EvaluationPlan.from_canonical_json(stream.plan_json)
        definitions = tuple(
            SegmentDefinition.model_validate_json(raw) for raw in stream.segment_definitions
        )
        selected = {(item.segment_id, item.segment_version): item for item in plan.segments}
        by_id = {item.identity: item for item in definitions}
        if len(by_id) != len(definitions) or set(by_id) != set(selected):
            raise EvaluationError("segment descriptors differ from pinned selections")
        for definition in definitions:
            if definition.resolve(selected[definition.identity]) != selected[definition.identity]:
                raise EvaluationError("segment configuration differs from pinned defaults")
            if for_execution:
                current = SEGMENT_DEFINITIONS.get(definition.identity)
                if current is None or current.fingerprint() != definition.fingerprint():
                    raise EvaluationError("segment definition differs from pinned contract")
        result[str(stream.run_id)] = definitions
    return result


@dataclass(frozen=True)
class SegmentMembership:
    event_id: str
    event_semantic_ref: str
    context_snapshot_id: str
    source_run_id: str
    detection_config_hash: str
    evaluation_plan_hash: str
    emission_order: int
    bucket: SegmentBucket


def segment_memberships(
    connection: Connection, record: EvaluationRunRecord, *, through: int | None = None
) -> Iterator[SegmentMembership]:
    definitions = pinned_segments(record)
    streams = {str(stream.run_id): stream for stream in record.inputs.streams}
    selected_runs = [key for key, value in definitions.items() if value]
    if not selected_runs:
        return
    if through is None:
        through = connection.scalar(
            select(func.max(evaluation_event_sets.c.emission_order)).where(
                evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id)
            )
        )
        if through is None:
            return
    after = -1
    while True:
        rows = (
            connection.execute(
                select(evaluation_event_sets, pattern_instances.c.instrument_id)
                .join(
                    pattern_instance_transitions,
                    evaluation_event_sets.c.event_id == pattern_instance_transitions.c.event_id,
                )
                .join(
                    pattern_instances,
                    pattern_instance_transitions.c.instance_id == pattern_instances.c.instance_id,
                )
                .where(
                    evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
                    evaluation_event_sets.c.source_run_id.in_(selected_runs),
                    evaluation_event_sets.c.eligible == 1,
                    evaluation_event_sets.c.emission_order > after,
                    evaluation_event_sets.c.emission_order <= through,
                )
                .order_by(evaluation_event_sets.c.emission_order)
                .limit(PAGE_SIZE)
            )
            .mappings()
            .all()
        )
        if not rows:
            return
        for row in rows:
            after = row["emission_order"]
            snapshot = load_detection_context(connection, row["event_id"])
            if (
                snapshot is None
                or snapshot.snapshot_id != row["context_snapshot_id"]
                or snapshot.event_id != row["event_id"]
                or snapshot.run_id != row["source_run_id"]
                or snapshot.source_event_semantic_ref != row["event_semantic_ref"]
                or row["instrument_id"] != record.inputs.instrument_id
            ):
                raise EvaluationError("segment membership source differs from immutable EventSet/X")
            stream = streams[row["source_run_id"]]
            for definition in definitions[row["source_run_id"]]:
                yield SegmentMembership(
                    event_id=row["event_id"],
                    event_semantic_ref=row["event_semantic_ref"],
                    context_snapshot_id=row["context_snapshot_id"],
                    source_run_id=row["source_run_id"],
                    detection_config_hash=stream.detection_config_hash,
                    evaluation_plan_hash=stream.evaluation_plan_hash,
                    emission_order=after,
                    bucket=assign_segment(definition, row["instrument_id"], snapshot),
                )


def evaluation_segments(connection: Connection, evaluation_id: str) -> dict[str, Any]:
    from market_analysis.application.evaluation_runs import read_evaluation_run

    record = read_evaluation_run(connection, evaluation_id)
    if record is None:
        raise EvaluationNotFound("evaluation run not found")
    pinned = pinned_segments(record)
    watermark = connection.scalar(
        select(func.max(evaluation_event_sets.c.emission_order)).where(
            evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id)
        )
    )
    through = -1 if watermark is None else watermark
    count_rows = connection.execute(
        select(evaluation_event_sets.c.source_run_id, func.count())
        .where(
            evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
            evaluation_event_sets.c.eligible == 1,
            evaluation_event_sets.c.emission_order <= through,
        )
        .group_by(evaluation_event_sets.c.source_run_id)
    ).all()
    native_counts: dict[str, int] = {str(row[0]): int(row[1]) for row in count_rows}
    definitions: dict[tuple[str, str], dict[str, Any]] = {}
    for stream in record.inputs.streams:
        for definition in pinned[str(stream.run_id)]:
            previous = definitions.get(definition.identity)
            if (
                previous is not None
                and previous["definition_fingerprint"] != definition.fingerprint()
            ):
                raise EvaluationError("same segment identity has conflicting pinned definitions")
            descriptor = definitions.setdefault(
                definition.identity,
                dict(
                    definition=definition.model_dump(mode="json"),
                    definition_fingerprint=definition.fingerprint(),
                    eligible_event_count=0,
                    source_streams=[],
                ),
            )
            descriptor["eligible_event_count"] += native_counts.get(str(stream.run_id), 0)
            descriptor["source_streams"].append(
                dict(
                    source_run_id=str(stream.run_id),
                    detection_config_hash=stream.detection_config_hash,
                    evaluation_plan_hash=stream.evaluation_plan_hash,
                )
            )
    groups: dict[str, dict[str, Any]] = {}
    observed: dict[tuple[str, str], int] = {}
    for membership in segment_memberships(connection, record, through=through):
        bucket = membership.bucket
        group = groups.setdefault(
            bucket.semantic_key,
            dict(bucket_id=bucket.semantic_key, **bucket.model_dump(mode="json"), event_count=0),
        )
        group["event_count"] += 1
        identity = bucket.segment_id, bucket.segment_version
        observed[identity] = observed.get(identity, 0) + 1
    if any(
        observed.get(key, 0) != value["eligible_event_count"] for key, value in definitions.items()
    ):
        raise EvaluationError("segment counts do not reconcile to eligible source events")
    return dict(
        evaluation_id=str(record.inputs.evaluation_id),
        status=record.status.value,
        complete=record.status is EvaluationStatus.COMPLETED,
        event_set_id=record.inputs.event_set_id,
        through_emission_order=through,
        eligible_event_count=sum(native_counts.values()),
        lineage=dict(
            dataset_revision_id=record.inputs.dataset_revision_id,
            canonical_checksum=record.inputs.canonical_checksum,
            calendar_id=record.inputs.calendar_id,
            calendar_version=record.inputs.calendar_version,
            build_id=record.inputs.build_id,
            code_revision=record.inputs.code_revision,
        ),
        definitions=[definitions[key] for key in sorted(definitions)],
        groups=[groups[key] for key in sorted(groups)],
    )
