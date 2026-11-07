"""Downstream-only outcome execution over bounded immutable source windows."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import Connection

from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.segment_registry import SEGMENT_PARAMETERS
from market_analysis.code_version import CodeVersion, capture_code_version
from market_analysis.config import (
    DetectionAnalysisConfig,
    EvaluationPlan,
    OutcomeSelection,
    evaluation_plan_hash,
)
from market_analysis.detection.records import freeze_mapping
from market_analysis.domain import Bar, BarSequence, Timeframe, TradingCalendar
from market_analysis.domain.outcomes import (
    ObservationWindow,
    OutcomeDefinition,
    OutcomeError,
    OutcomeEventReference,
    OutcomeLineage,
    OutcomeObservation,
    OutputType,
    ReferenceMode,
    ResultStatus,
    outcome_semantic_key,
    selection_payload,
)
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.market_data import load_dataset_lineage, load_dataset_revision
from market_analysis.persistence.outcomes import load_outcome_observation, save_outcome_observation
from market_analysis.persistence.pattern_instances import (
    PersistedDetectorEvent,
    load_pattern_instance,
)
from market_analysis.persistence.runs import load_run_snapshot

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class OutcomeInput:
    window: ObservationWindow
    bars: tuple[Bar, ...]
    events: tuple[OutcomeEventReference, ...]
    context: Mapping[str, object]
    configuration: OutcomeSelection | None = None


@dataclass(frozen=True, slots=True)
class OutcomeResult:
    status: ResultStatus
    value: Decimal | bool | str | OutcomeEventReference | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ResultStatus):
            raise OutcomeError("outcome result requires an explicit ResultStatus")
        if self.value is not None and not isinstance(
            self.value, Decimal | bool | str | OutcomeEventReference
        ):
            raise OutcomeError("outcome result contains an unsupported value type")
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise OutcomeError("numeric outcome values must be finite")


OutcomeAlgorithm = Callable[[OutcomeInput], OutcomeResult]


def evaluate_outcome(
    connection: Connection,
    *,
    event: PersistedDetectorEvent,
    pattern: PatternDefinition,
    definition: OutcomeDefinition,
    definitions: Mapping[tuple[str, str], OutcomeDefinition],
    bar_sequence: BarSequence,
    calendar: TradingCalendar,
    algorithm: OutcomeAlgorithm,
    selection: OutcomeSelection | None = None,
    events: tuple[OutcomeEventReference, ...] = (),
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
) -> OutcomeObservation:
    """Algorithms cannot receive undeclared future bars or write back into X."""
    occurrence = load_pattern_instance(connection, event.instance_id, pattern)
    if occurrence is None or event not in occurrence.detector_events:
        raise OutcomeError("outcome source DetectorEvent must already exist")
    snapshot = load_detection_context(connection, event.event_id)
    if snapshot is None:
        raise OutcomeError("outcomes require the source detection-time context")
    run = load_run_snapshot(connection, UUID(event.run_id))
    if run is None or run.evaluation_plan_json is None or run.evaluation_plan_hash is None:
        raise OutcomeError("outcome source requires a pinned EvaluationPlan")
    current_code = revision_provider(run.build_id)
    pinned_code = run.code_version
    if any(
        getattr(current_code, key) != getattr(pinned_code, key)
        for key in ("build_id", "revision", "dirty", "status")
    ):
        raise OutcomeError("outcome executor build differs from the pinned source run")
    plan = EvaluationPlan.from_canonical_json(run.evaluation_plan_json)
    specs = {key: item.parameters for key, item in definitions.items()}
    if (
        evaluation_plan_hash(plan, outcome_parameters=specs, segment_parameters=SEGMENT_PARAMETERS)
        != run.evaluation_plan_hash
    ):
        raise OutcomeError("outcome plan hash differs from pinned input")
    if definitions.get(definition.identity) != definition:
        raise OutcomeError("unregistered outcome definition")
    matches = [
        item
        for item in plan.outcomes
        if (item.outcome_id, item.outcome_version) == definition.identity
    ]
    if not matches:
        raise OutcomeError("outcome is not selected by the pinned EvaluationPlan")
    if selection is None:
        if len(matches) != 1:
            raise OutcomeError("an exact outcome configuration is required for multiple selections")
        selection = matches[0]
    selected = definition.resolve(selection)
    if not any(
        selection_payload(definition.resolve(item)) == selection_payload(selected)
        for item in matches
    ):
        raise OutcomeError("outcome configuration is not selected by the pinned EvaluationPlan")
    revision = load_dataset_revision(connection, event.dataset_revision_id)
    stored_lineage = load_dataset_lineage(
        connection,
        event.dataset_revision_id,
        event.instrument_id,
        Timeframe.M1,
    )
    if (
        bar_sequence.lineage.dataset_revision_id != event.dataset_revision_id
        or bar_sequence.lineage.instrument_id != event.instrument_id
        or bar_sequence.lineage.timeframe is not Timeframe.M1
        or calendar.version != run.calendar_version
        or calendar.instrument_id != event.instrument_id
        or calendar.calendar_id
        != DetectionAnalysisConfig.from_canonical_json(run.detection_config_json).calendar_id
        or revision is None
        or revision.calendar_version != calendar.version
        or stored_lineage != bar_sequence.lineage
        or snapshot.source_event_semantic_ref != event.event_semantic_key
        or snapshot.instance_semantic_key != event.instance_semantic_key
        or plan.detection_config_hash != event.detection_config_hash
    ):
        raise OutcomeError("outcome input lineage differs from source detection")
    horizon = cast(
        int,
        next(
            item.value for item in selected.parameters if item.name == definition.horizon_parameter
        ),
    )
    retrospective = definition.reference_mode is ReferenceMode.FROM_EVENT_TIME
    reference = event.event_time + MINUTE if retrospective else snapshot.available_at
    reference_price = Decimal(str(cast(Mapping[str, object], snapshot.values["bar"])["close"]))
    early: OutcomeResult | None = None
    if definition.eligible_event_kinds and event.event_kind not in definition.eligible_event_kinds:
        early = OutcomeResult(ResultStatus.NOT_APPLICABLE, reason="source event is ineligible")
    if retrospective:
        if event.timeframe is not Timeframe.M1:
            early = OutcomeResult(
                ResultStatus.NOT_APPLICABLE,
                reason="v1 retrospective reference requires an M1 source event",
            )
        index = bisect_left(bar_sequence.bars, event.event_time, key=lambda bar: bar.timestamp)
        if index == len(bar_sequence.bars) or (
            bar_sequence.bars[index].timestamp != event.event_time
        ):
            early = OutcomeResult(
                ResultStatus.INSUFFICIENT_DATA,
                reason="retrospective reference minute is unavailable",
            )
        else:
            reference_price = bar_sequence.bars[index].close
    if reference.second or reference.microsecond:
        raise OutcomeError("v1 outcome reference must be minute-aligned")
    _, day_end = calendar.trading_day_bounds(
        event.event_time if retrospective else event.detection_time
    )
    expected: list[datetime] = []
    cursor = reference
    while cursor < day_end and len(expected) < horizon:
        if calendar.derive(cursor).is_open:
            expected.append(cursor)
        cursor += MINUTE
    # A reference at a daily close cannot acquire an implicit overnight horizon.
    end = max(reference, cursor)
    start_index = bisect_left(bar_sequence.bars, reference, key=lambda bar: bar.timestamp)
    stop_index = bisect_left(bar_sequence.bars, end, key=lambda bar: bar.timestamp)
    bounded = bar_sequence.bars[start_index:stop_index]
    if any(bar.timeframe is not Timeframe.M1 or not bar.is_complete for bar in bounded):
        raise OutcomeError("outcomes require completed canonical M1 bars")
    actual = tuple(bar.timestamp for bar in bounded)
    if early is None and len(expected) != horizon:
        early = OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA,
            reason="requested horizon crosses the source trading-day boundary",
        )
    if early is None and actual != tuple(expected):
        missing = next((time for time in expected if time not in actual), reference)
        early = OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA,
            reason=f"required source minute unavailable: {missing.isoformat()}",
        )
    window = ObservationWindow(
        reference_mode=definition.reference_mode,
        retrospective_label=definition.retrospective_label,
        reference_time=reference,
        end=end,
        horizon_bars=horizon,
        observed_bars=min(horizon, len(bounded)),
        reference_price=reference_price,
    )
    source_order = snapshot.available_at, 1, event.within_bar_ordinal
    bounded_events = []
    for item in events:
        if (
            item.dataset_revision_id != event.dataset_revision_id
            or item.instrument_id != event.instrument_id
            or item.timeframe is not event.timeframe
            or item.detection_config_hash != event.detection_config_hash
        ):
            raise OutcomeError("referenced analytical event has incompatible lineage")
        after = (
            item.available_at >= reference
            if retrospective
            else (item.observable_order > source_order)
        )
        if after and item.available_at <= end:
            bounded_events.append(item)
    bounded_events.sort(key=lambda item: item.observable_order)
    result = early or algorithm(
        OutcomeInput(
            window,
            tuple(bounded),
            tuple(bounded_events),
            freeze_mapping(snapshot.values),
            selected,
        )
    )
    if not isinstance(result, OutcomeResult):
        raise OutcomeError("outcome algorithm must return a typed OutcomeResult")
    if isinstance(result.value, OutcomeEventReference) and result.value not in bounded_events:
        raise OutcomeError("outcome references an event outside the declared observable window")
    if result.status is ResultStatus.AVAILABLE and definition.output_type is OutputType.CATEGORICAL:
        if result.value not in definition.categories:
            raise OutcomeError("outcome category is not declared by its versioned definition")
    semantic_key = outcome_semantic_key(event.event_semantic_key, selected)
    observation = OutcomeObservation(
        observation_id=str(uuid5(UUID(event.event_id), semantic_key)),
        semantic_key=semantic_key,
        outcome_id=definition.outcome_id,
        outcome_version=definition.outcome_version,
        configuration=selected,
        output_type=definition.output_type,
        status=result.status,
        value=result.value,
        reason=result.reason,
        window=window,
        lineage=OutcomeLineage(
            source_event_id=event.event_id,
            source_run_id=run.run_id,
            context_snapshot_id=snapshot.snapshot_id,
            source_event_semantic_ref=event.event_semantic_key,
            instance_semantic_key=event.instance_semantic_key,
            dataset_revision_id=event.dataset_revision_id,
            instrument_id=event.instrument_id,
            source_dataset_id=bar_sequence.lineage.source_dataset_id,
            canonical_checksum=bar_sequence.lineage.canonical_checksum,
            source_timeframe=event.timeframe,
            calendar_version=run.calendar_version,
            detection_config_hash=event.detection_config_hash,
            evaluation_plan_hash=run.evaluation_plan_hash,
            definition_fingerprint=definition.fingerprint(),
            config_schema_version=run.config_schema_version,
            build_id=run.build_id,
            code_revision=run.code_revision,
            code_dirty=run.code_dirty,
            code_capture_status=run.code_capture_status,
        ),
    )
    existing = load_outcome_observation(connection, observation.observation_id)
    if existing is not None:
        if existing != observation:
            raise OutcomeError("immutable outcome already exists with different values")
        return existing
    save_outcome_observation(connection, observation, definition)
    return observation
