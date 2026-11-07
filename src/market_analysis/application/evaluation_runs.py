"""Pin reproducible evaluation inputs and select primary events after X capture."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, func, select

from market_analysis.application.analysis_clock import verify_closed_interval
from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.event_sets import (
    event_set_decision,
    field_types,
    normalize_event_set,
)
from market_analysis.application.outcome_registry import OUTCOME_DEFINITIONS
from market_analysis.application.replay_catalog import (
    PATTERN_DEFINITIONS,
    detector_bindings,
    required_warmup_bars,
    resolve_replay_calendar,
)
from market_analysis.application.segment_registry import SEGMENT_DEFINITIONS, SEGMENT_PARAMETERS
from market_analysis.code_version import CodeVersion, capture_code_version
from market_analysis.config import (
    DetectionAnalysisConfig,
    EvaluationPlan,
    detection_config_hash,
    resolve_detection_config,
    resolve_evaluation_plan,
)
from market_analysis.detection import DetectorRuntime
from market_analysis.detection.context_snapshots import CONTEXT_SCHEMA_VERSION
from market_analysis.domain import Timeframe, TradingCalendar, ValidationStatus
from market_analysis.domain.aggregation import MINUTE, MINUTES, CandleAggregator
from market_analysis.domain.evaluation import (
    EvaluationError,
    EvaluationInput,
    EvaluationNotFound,
    EvaluationRequest,
    EvaluationStatus,
    EvaluationStreamInput,
)
from market_analysis.domain.outcomes import OutcomeDefinition, outcome_semantic_key
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluations import (
    EvaluationRunRecord,
    evaluation_event_sets,
    insert_evaluation,
    load_evaluation,
    mark_evaluation_completed,
)
from market_analysis.persistence.market_data import load_dataset_revision
from market_analysis.persistence.outcomes import load_outcome_observation, outcome_observations
from market_analysis.persistence.pattern_instances import (
    PersistedDetectorEvent,
    load_pattern_instance,
    pattern_instance_transitions,
    pattern_instances,
)
from market_analysis.persistence.runs import create_run_snapshot, load_run_snapshot


def create_evaluation_run(
    connection: Connection,
    request: EvaluationRequest,
    store: DatasetStore,
    *,
    build_id: str,
    created_at: datetime,
    calendar: TradingCalendar | None = None,
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
    outcome_definitions: Mapping[tuple[str, str], OutcomeDefinition] = OUTCOME_DEFINITIONS,
) -> EvaluationRunRecord:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise EvaluationError("evaluation creation time must be timezone-aware")
    code = revision_provider(build_id)
    if not code.is_clean_committed or code.build_id != build_id:
        raise EvaluationError(
            "autonomous evaluation requires an identifiable clean committed build"
        )
    sequence = store.load_sequence(
        connection, request.dataset_revision_id, request.instrument_id, Timeframe.M1
    )
    revision = load_dataset_revision(connection, request.dataset_revision_id)
    if revision is None or sequence.lineage.validation_status is ValidationStatus.FAIL:
        raise EvaluationError("evaluation source revision is unavailable or failed validation")
    if (
        request.selected_start < sequence.bars[0].timestamp
        or request.selected_end > sequence.lineage.requested_end
        or not any(
            request.selected_start <= item.timestamp < request.selected_end
            for item in sequence.bars
        )
    ):
        raise EvaluationError("evaluation interval is outside usable pinned source coverage")
    calendar_id = request.streams[0].config.calendar_id
    calendar = calendar or resolve_replay_calendar(
        request.instrument_id, calendar_id, revision.calendar_version
    )
    if (
        calendar.instrument_id != request.instrument_id
        or calendar.calendar_id != calendar_id
        or calendar.version != revision.calendar_version
    ):
        raise EvaluationError("evaluation calendar differs from source/configuration lineage")
    normalized = []
    for item in sorted(request.streams, key=lambda value: MINUTES[value.config.timeframe]):
        config = resolve_detection_config(item.config, pattern_definitions=PATTERN_DEFINITIONS)
        if config.calendar_id != calendar_id or not any(value.enabled for value in config.patterns):
            raise EvaluationError("evaluation requires enabled detectors and one pinned calendar")
        detector_bindings(config)  # reject unsupported detectors/timeframes before any run writes
        if item.plan.detection_config_hash != detection_config_hash(
            config,
            pattern_definitions=PATTERN_DEFINITIONS,
        ):
            raise EvaluationError(
                "EvaluationPlan references a different copied detection configuration"
            )
        if (
            item.plan.context_schema_version != CONTEXT_SCHEMA_VERSION
            or item.plan.schema_version not in {"evaluation-plan-v1", "evaluation-plan-v2"}
        ):
            raise EvaluationError("unsupported evaluation/context schema")
        if item.plan.context_fields:
            raise EvaluationError(
                "selective context fields are unsupported; v1 captures its full schema"
            )
        if item.plan.export_settings:
            raise EvaluationError("export execution contracts are not registered yet")
        plan = resolve_evaluation_plan(
            item.plan,
            outcome_parameters={
                key: value.parameters for key, value in outcome_definitions.items()
            },
            segment_parameters=SEGMENT_PARAMETERS,
        )
        for selected in plan.outcomes:
            outcome_definitions[(selected.outcome_id, selected.outcome_version)].resolve(selected)
        for selected_segment in plan.segments:
            identity = selected_segment.segment_id, selected_segment.segment_version
            SEGMENT_DEFINITIONS[identity].resolve(selected_segment)
        plan = normalize_event_set(plan, field_types([config], PATTERN_DEFINITIONS))
        normalized.append((config, plan))
    aggregators = {
        config.timeframe: CandleAggregator(config.timeframe, calendar) for config, _ in normalized
    }
    warmup = {timeframe: 0 for timeframe in aggregators}
    expected_counts = {timeframe: 0 for timeframe in aggregators}
    expected_last: dict[Timeframe, datetime] = {}
    previous = sequence.bars[0].timestamp
    for bar in sequence.bars:
        if bar.timestamp >= request.selected_end:
            break
        for timeframe, aggregator in aggregators.items():
            for candle in aggregator.push(bar):
                warmup[timeframe] += int(candle.available_at <= request.selected_start)
                expected_counts[timeframe] += 1
                expected_last[timeframe] = candle.available_at
        previous = bar.timestamp + MINUTE
    verify_closed_interval(calendar, previous, request.selected_end)
    for timeframe, aggregator in aggregators.items():
        for candle in aggregator.advance(request.selected_end):
            warmup[timeframe] += int(candle.available_at <= request.selected_start)
            expected_counts[timeframe] += 1
            expected_last[timeframe] = candle.available_at
    for config, _ in normalized:
        if warmup[config.timeframe] < required_warmup_bars(config):
            raise EvaluationError(
                f"insufficient {config.timeframe.value} warmup at pinned source origin"
            )
    streams = []
    # All validation above precedes creation. Nested transaction also makes this
    # operation atomic when its caller catches an insertion failure.
    with connection.begin_nested():
        for config, plan in normalized:
            for pattern in sorted(
                config.patterns,
                key=lambda value: (
                    value.pattern_id,
                    value.pattern_version,
                ),
            ):
                if not pattern.enabled:
                    continue
                copied = config.model_copy(update={"patterns": (pattern,)})
                copied_hash = detection_config_hash(copied, pattern_definitions=PATTERN_DEFINITIONS)
                copied_plan = plan.model_copy(update={"detection_config_hash": copied_hash})
                snapshot = create_run_snapshot(
                    connection,
                    run_id=uuid4(),
                    dataset_revision_id=request.dataset_revision_id,
                    calendar_version=calendar.version,
                    build_id=build_id,
                    detection_config=copied,
                    evaluation_plan=copied_plan,
                    pattern_definitions=PATTERN_DEFINITIONS,
                    outcome_parameters={
                        key: value.parameters for key, value in outcome_definitions.items()
                    },
                    segment_parameters=SEGMENT_PARAMETERS,
                    revision_provider=lambda _: code,
                )
                runtime = DetectorRuntime(
                    copied,
                    detector_bindings(copied),
                    run_id=snapshot.run_id,
                    dataset_revision_id=request.dataset_revision_id,
                    calendar=calendar,
                    pinned_calendar_version=calendar.version,
                    pinned_config_hash=snapshot.detection_config_hash,
                )
                streams.append(
                    EvaluationStreamInput(
                        run_id=UUID(snapshot.run_id),
                        timeframe=copied.timeframe,
                        pattern_id=pattern.pattern_id,
                        pattern_version=pattern.pattern_version,
                        definition_fingerprint=PATTERN_DEFINITIONS[
                            (pattern.pattern_id, pattern.pattern_version)
                        ].semantic_fingerprint(),
                        binding_fingerprint=runtime.binding_fingerprint,
                        config_json=snapshot.detection_config_json,
                        detection_config_hash=snapshot.detection_config_hash,
                        plan_json=cast(str, snapshot.evaluation_plan_json),
                        evaluation_plan_hash=cast(str, snapshot.evaluation_plan_hash),
                        outcome_definitions=tuple(
                            outcome_definitions[
                                (item.outcome_id, item.outcome_version)
                            ].model_dump_json()
                            for item in copied_plan.outcomes
                        ),
                        segment_definitions=tuple(
                            SEGMENT_DEFINITIONS[
                                (item.segment_id, item.segment_version)
                            ].model_dump_json()
                            for item in sorted(
                                copied_plan.segments,
                                key=lambda item: (item.segment_id, item.segment_version),
                            )
                        ),
                        warmup_required=required_warmup_bars(copied),
                        warmup_available=warmup[copied.timeframe],
                        expected_bars=expected_counts[copied.timeframe],
                        expected_last_available_at=expected_last[copied.timeframe],
                    )
                )
        inputs = EvaluationInput(
            evaluation_id=uuid4(),
            dataset_revision_id=request.dataset_revision_id,
            source_dataset_id=sequence.lineage.source_dataset_id,
            canonical_checksum=sequence.lineage.canonical_checksum,
            source_checksum=sequence.lineage.source_checksum,
            instrument_id=request.instrument_id,
            source_bar_count=len(sequence.bars),
            initialization_origin=sequence.bars[0].timestamp,
            execution_bar_count=sum(bar.timestamp < request.selected_end for bar in sequence.bars),
            selected_start=request.selected_start,
            selected_end=request.selected_end,
            calendar_id=calendar.calendar_id,
            calendar_version=calendar.version,
            build_id=build_id,
            code_revision=cast(str, code.revision),
            code_capture_source=code.source,
            original_configs=tuple(config.canonical_json() for config, _ in normalized),
            original_plans=tuple(plan.canonical_json() for _, plan in normalized),
            streams=tuple(streams),
        )
        insert_evaluation(connection, inputs, created_at.astimezone(UTC))
    result = read_evaluation_run(connection, str(inputs.evaluation_id))
    assert result is not None
    return result


def read_evaluation_run(connection: Connection, evaluation_id: str) -> EvaluationRunRecord | None:
    record = load_evaluation(connection, evaluation_id)
    if record is None:
        return None
    for stream in record.inputs.streams:
        snapshot = load_run_snapshot(connection, stream.run_id)
        if snapshot is None or (
            snapshot.dataset_revision_id != record.inputs.dataset_revision_id
            or snapshot.calendar_version != record.inputs.calendar_version
            or snapshot.detection_config_json != stream.config_json
            or snapshot.evaluation_plan_json != stream.plan_json
            or snapshot.detection_config_hash != stream.detection_config_hash
            or snapshot.evaluation_plan_hash != stream.evaluation_plan_hash
            or snapshot.build_id != record.inputs.build_id
            or snapshot.code_revision != record.inputs.code_revision
            or snapshot.code_dirty is not False
            or snapshot.code_capture_status != "available"
            or sha256(
                f"{snapshot.detection_hash_version}\n{stream.config_json}".encode()
            ).hexdigest()
            != stream.detection_config_hash
            or sha256(
                f"{snapshot.evaluation_hash_version}\n{stream.plan_json}".encode()
            ).hexdigest()
            != stream.evaluation_plan_hash
        ):
            raise EvaluationError("evaluation source snapshot differs from immutable manifest")
    return record


def record_event_membership(
    connection: Connection,
    record: EvaluationRunRecord,
    event: PersistedDetectorEvent,
    *,
    emission_order: int,
    owner_token: str,
) -> bool:
    current = read_evaluation_run(connection, str(record.inputs.evaluation_id))
    if (
        current is None
        or current.status is not EvaluationStatus.RUNNING
        or (current.owner_token != owner_token)
    ):
        raise EvaluationError("only the active evaluation owner can select primary events")
    record = current
    stream = next(
        (item for item in record.inputs.streams if str(item.run_id) == event.run_id), None
    )
    if stream is None:
        raise EvaluationError("primary event belongs to another evaluation stream")
    occurrence = load_pattern_instance(
        connection,
        event.instance_id,
        PATTERN_DEFINITIONS[(stream.pattern_id, stream.pattern_version)],
    )
    if occurrence is None or event not in occurrence.detector_events:
        raise EvaluationError("EventSet source is not the persisted detector event")
    snapshot = load_detection_context(connection, event.event_id)
    if snapshot is None:
        raise EvaluationError("EventSet filtering cannot run before context capture")
    configs = [
        DetectionAnalysisConfig.from_canonical_json(value)
        for value in record.inputs.original_configs
    ]
    configs = [item for item in configs if item.timeframe is stream.timeframe]
    plan = EvaluationPlan.from_canonical_json(stream.plan_json)
    eligible, reason = event_set_decision(
        event,
        snapshot,
        plan,
        selected_start=record.inputs.selected_start,
        selected_end=record.inputs.selected_end,
        fields=field_types(configs, PATTERN_DEFINITIONS),
    )
    connection.execute(
        evaluation_event_sets.insert().values(
            evaluation_id=str(record.inputs.evaluation_id),
            event_id=event.event_id,
            context_snapshot_id=snapshot.snapshot_id,
            source_run_id=event.run_id,
            event_semantic_ref=event.event_semantic_key,
            available_at=snapshot.available_at,
            emission_order=emission_order,
            eligible=int(eligible),
            exclusion_reason=reason,
            within_bar_ordinal=event.within_bar_ordinal,
        )
    )
    return eligible


def complete_evaluation_if_ready(
    connection: Connection,
    evaluation_id: str,
    at: datetime,
) -> bool:
    record = read_evaluation_run(connection, evaluation_id)
    if record is None or record.status is not EvaluationStatus.DETECTION_COMPLETED:
        raise EvaluationError("all required detection stages must finish before completion")
    source_runs = [str(item.run_id) for item in record.inputs.streams]
    event_count = connection.scalar(
        select(func.count())
        .select_from(pattern_instance_transitions.join(pattern_instances))
        .where(pattern_instances.c.run_id.in_(source_runs))
    )
    members = (
        connection.execute(
            select(evaluation_event_sets).where(
                evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
            )
        )
        .mappings()
        .all()
    )
    if len(members) != event_count:
        raise EvaluationError("EventSet stage is incomplete for captured detector events")
    for member in members:
        if not member["eligible"]:
            continue
        stream = next(
            item for item in record.inputs.streams if str(item.run_id) == member["source_run_id"]
        )
        plan = EvaluationPlan.from_canonical_json(stream.plan_json)
        for selection in plan.outcomes:
            semantic_key = outcome_semantic_key(member["event_semantic_ref"], selection)
            observation = load_outcome_observation(
                connection,
                str(
                    uuid5(
                        UUID(member["event_id"]),
                        semantic_key,
                    )
                ),
            )
            if observation is None:
                return False
            definition = next(
                OutcomeDefinition.model_validate_json(value)
                for value in stream.outcome_definitions
                if (json.loads(value)["outcome_id"], json.loads(value)["outcome_version"])
                == (selection.outcome_id, selection.outcome_version)
            )
            if (
                observation.lineage.source_event_id != member["event_id"]
                or observation.lineage.context_snapshot_id != member["context_snapshot_id"]
                or observation.lineage.evaluation_plan_hash != stream.evaluation_plan_hash
                or observation.lineage.definition_fingerprint != definition.fingerprint()
            ):
                raise EvaluationError("outcome observation does not satisfy this evaluation stage")
    mark_evaluation_completed(connection, evaluation_id, at)
    return True


def evaluation_state(connection: Connection, evaluation_id: str) -> dict[str, object]:
    record = read_evaluation_run(connection, evaluation_id)
    if record is None:
        raise EvaluationNotFound("evaluation run not found")
    counts = connection.execute(
        select(
            func.count(),
            func.coalesce(func.sum(evaluation_event_sets.c.eligible), 0),
        ).where(evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id))
    ).one()
    return {
        "evaluation_id": str(record.inputs.evaluation_id),
        "status": record.status.value,
        "revision": record.revision,
        "cursor_index": record.cursor_index,
        "available_at": record.available_at,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
        "failure_reason": record.failure_reason,
        "semantic_key": record.inputs.semantic_key,
        "event_set_id": record.inputs.event_set_id,
        "event_count": counts[0],
        "eligible_event_count": counts[1],
        "inputs": json.loads(record.inputs.canonical_json()),
    }


def evaluation_events(
    connection: Connection,
    evaluation_id: str,
    *,
    after: int = -1,
    limit: int = 100,
    eligible_only: bool = True,
) -> dict[str, object]:
    record = read_evaluation_run(connection, evaluation_id)
    if record is None:
        raise EvaluationNotFound("evaluation run not found")
    if type(after) is not int or after < -1 or not 1 <= limit <= 500:
        raise EvaluationError("invalid evaluation event pagination")
    query = (
        select(
            evaluation_event_sets,
            pattern_instances.c.instance_id,
            pattern_instances.c.pattern_id,
            pattern_instances.c.pattern_version,
            pattern_instances.c.instrument_id,
            pattern_instances.c.timeframe,
            pattern_instance_transitions.c.event_time,
            pattern_instance_transitions.c.detection_time,
            pattern_instance_transitions.c.to_state,
            pattern_instance_transitions.c.trigger_id,
            pattern_instance_transitions.c.rationale_json,
        )
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
            evaluation_event_sets.c.emission_order > after,
        )
    )
    if eligible_only:
        query = query.where(evaluation_event_sets.c.eligible == 1)
    rows = connection.execute(query.order_by(evaluation_event_sets.c.emission_order).limit(limit))
    result = []
    for row in rows.mappings():
        snapshot = load_detection_context(connection, row["event_id"])
        if snapshot is None:
            raise EvaluationError("persisted EventSet member has no detection-time context")
        outcomes = []
        for identity in connection.execute(
            select(outcome_observations.c.observation_id).where(
                outcome_observations.c.event_id == row["event_id"],
            )
        ).scalars():
            observation = load_outcome_observation(connection, identity)
            assert observation is not None
            outcomes.append(observation.model_dump(mode="json"))
        event_time = row["event_time"]
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        result.append(
            {
                "event_id": row["event_id"],
                "event_semantic_ref": row["event_semantic_ref"],
                "instance_id": row["instance_id"],
                "run_id": row["source_run_id"],
                "pattern_id": row["pattern_id"],
                "pattern_version": row["pattern_version"],
                "instrument_id": row["instrument_id"],
                "timeframe": row["timeframe"],
                "event_kind": row["to_state"].upper(),
                "transition_reason": row["trigger_id"],
                "event_time": event_time,
                "detection_time": snapshot.detection_time,
                "available_at": snapshot.available_at,
                "within_bar_ordinal": row["within_bar_ordinal"],
                "emission_order": row["emission_order"],
                "eligible": bool(row["eligible"]),
                "exclusion_reason": row["exclusion_reason"],
                "rationale": None
                if row["rationale_json"] is None
                else json.loads(row["rationale_json"]),
                "context": snapshot.to_dict(),
                "outcomes": sorted(
                    outcomes,
                    key=lambda item: (item["outcome_id"], item["outcome_version"]),
                ),
            }
        )
    return {
        "evaluation_id": str(record.inputs.evaluation_id),
        "status": record.status.value,
        "complete": record.status is EvaluationStatus.COMPLETED,
        "events": result,
        "next_after": after if not result else result[-1]["emission_order"],
    }
