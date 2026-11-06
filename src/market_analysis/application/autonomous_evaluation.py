"""Bounded autonomous detector execution over the exact replay-compatible analytical path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, select

from market_analysis.application.analysis_clock import verify_closed_interval
from market_analysis.application.analytical_step import process_detector_candle
from market_analysis.application.evaluation_runs import (
    complete_evaluation_if_ready,
    read_evaluation_run,
)
from market_analysis.application.logging import research_logger
from market_analysis.application.replay_catalog import (
    PATTERN_DEFINITIONS,
    detector_bindings,
    resolve_replay_calendar,
)
from market_analysis.application.replay_event_store import ReplayEventStore
from market_analysis.code_version import CodeVersion, capture_code_version
from market_analysis.config import DetectionAnalysisConfig, EvaluationPlan
from market_analysis.detection import DetectorRuntime, DetectorRuntimeResult
from market_analysis.domain import ObservableBars, SimulationClock, Timeframe
from market_analysis.domain.aggregation import MINUTE, MINUTES, AnalyticalCandle, CandleAggregator
from market_analysis.domain.evaluation import (
    EvaluationError,
    EvaluationStatus,
    EvaluationStreamInput,
)
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluation_jobs import evaluation_jobs
from market_analysis.persistence.evaluations import (
    EvaluationRunRecord,
    advance_evaluation,
    advance_evaluation_stream,
    cancel_evaluation,
    claim_evaluation,
    fail_evaluation,
    finish_detection,
)
from market_analysis.persistence.pattern_instances import PersistedDetectorEvent
from market_analysis.persistence.transactions import atomic_transaction

from .evaluation_runs import record_event_membership

FrameObserver = Callable[
    [
        EvaluationStreamInput,
        AnalyticalCandle,
        DetectorRuntimeResult,
        tuple[PersistedDetectorEvent, ...],
    ],
    None,
]


class EvaluationStopped(EvaluationError):
    """The operator requested an orderly stop at a causal processing boundary."""


@dataclass
class _Stream:
    inputs: EvaluationStreamInput
    runtime: DetectorRuntime
    store: ReplayEventStore


def execute_evaluation(
    engine: Engine,
    store: DatasetStore,
    evaluation_id: str,
    *,
    chunk_size: int = 128,
    owner_token: str | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    revision_provider: Callable[[str], CodeVersion] = capture_code_version,
    on_source_step: Callable[[ObservableBars], None] | None = None,
    on_frame: FrameObserver | None = None,
    on_commit: Callable[[], None] | None = None,
) -> EvaluationRunRecord:
    """Execute a fresh run, preserving only committed chunks after failure/stop.

    Observer hooks are internal instrumentation; none can receive the clock's
    complete source sequence. Production execution retains no UI frame history.
    Full queue scheduling and stale-process recovery remain SCRUM-123.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= 2000:
        raise EvaluationError("evaluation chunk_size must be 1..2000 source minutes")
    evaluation_id = str(UUID(evaluation_id))
    with atomic_transaction(engine) as connection:
        record = read_evaluation_run(connection, evaluation_id)
        if record is None:
            raise EvaluationError("evaluation run not found")
        if owner_token is None:
            token = claim_evaluation(connection, evaluation_id, datetime.now(UTC))
            if connection.scalar(
                select(evaluation_jobs.c.job_id).where(
                    evaluation_jobs.c.evaluation_id == evaluation_id,
                )
            ):
                raise EvaluationError("queued evaluations require scheduler ownership")
        elif record.status is EvaluationStatus.RUNNING and record.owner_token == owner_token:
            token = owner_token
        else:
            raise EvaluationError("evaluation is not owned by the supplied scheduler token")
    inputs = record.inputs
    logger = research_logger(
        run_id=evaluation_id,
        dataset_id=inputs.dataset_revision_id,
        instrument=inputs.instrument_id,
        component="evaluation",
        build_id=inputs.build_id,
    )
    committed_index = -1
    emission_order = 0
    active_stream: str | None = None
    try:
        code = revision_provider(inputs.build_id)
        if (
            not code.is_clean_committed
            or code.build_id != inputs.build_id
            or code.revision != inputs.code_revision
        ):
            raise EvaluationError("executor build differs from the pinned clean evaluation build")
        with engine.connect() as connection:
            sequence = store.load_sequence(
                connection, inputs.dataset_revision_id, inputs.instrument_id, Timeframe.M1
            )
        if (
            sequence.lineage.canonical_checksum != inputs.canonical_checksum
            or sequence.lineage.source_checksum != inputs.source_checksum
            or sequence.lineage.source_dataset_id != inputs.source_dataset_id
            or sequence.bars[0].timestamp != inputs.initialization_origin
            or len(sequence.bars) != inputs.source_bar_count
        ):
            raise EvaluationError("immutable evaluation source differs from its pinned manifest")
        calendar = resolve_replay_calendar(
            inputs.instrument_id, inputs.calendar_id, inputs.calendar_version
        )
        streams = []
        for item in inputs.streams:
            definition = PATTERN_DEFINITIONS.get((item.pattern_id, item.pattern_version))
            if (
                definition is None
                or definition.semantic_fingerprint() != item.definition_fingerprint
            ):
                raise EvaluationError("executor detector definition differs from pinned version")
            config = DetectionAnalysisConfig.from_canonical_json(item.config_json)
            plan = EvaluationPlan.from_canonical_json(item.plan_json)
            if plan.context_schema_version != "detection-time-context-v1":
                raise EvaluationError("executor context schema is unsupported")
            runtime = DetectorRuntime(
                config,
                detector_bindings(config),
                run_id=str(item.run_id),
                dataset_revision_id=inputs.dataset_revision_id,
                calendar=calendar,
                pinned_calendar_version=inputs.calendar_version,
                pinned_config_hash=item.detection_config_hash,
            )
            if runtime.binding_fingerprint != item.binding_fingerprint:
                raise EvaluationError("executor detector binding differs from pinned input")
            streams.append(_Stream(item, runtime, ReplayEventStore(PATTERN_DEFINITIONS)))
        aggregators = {
            item.inputs.timeframe: CandleAggregator(item.inputs.timeframe, calendar)
            for item in streams
        }
        clock = SimulationClock(
            sequence, selected_start=inputs.selected_start, selected_end=inputs.selected_end
        )
        if clock.bar_count != inputs.execution_bar_count:
            raise EvaluationError("execution source count differs from pinned preflight")
        watermark = inputs.initialization_origin
        logger.info(
            "autonomous detector execution started",
            extra={
                "stream_count": len(streams),
                "source_minutes": clock.bar_count,
                "chunk_size": chunk_size,
            },
        )

        def dispatch(connection: Connection, candles: list[AnalyticalCandle]) -> None:
            nonlocal emission_order, active_stream
            for candle in sorted(
                candles,
                key=lambda value: (
                    value.available_at,
                    MINUTES[value.bar.timeframe],
                ),
            ):
                for stream in streams:
                    if stream.inputs.timeframe is not candle.bar.timeframe:
                        continue
                    if should_stop():
                        raise EvaluationStopped("operator stopped autonomous execution")
                    active_stream = str(stream.inputs.run_id)
                    result = process_detector_candle(
                        connection, candle, stream.runtime, stream.store
                    )
                    persisted = tuple(
                        stream.store.persisted_event(event) for event in result.events
                    )
                    for event in persisted:
                        record_event_membership(
                            connection,
                            record,
                            event,
                            emission_order=emission_order,
                            owner_token=token,
                        )
                        emission_order += 1
                    advance_evaluation_stream(
                        connection, evaluation_id, active_stream, token, candle.available_at
                    )
                    if on_frame is not None:
                        on_frame(stream.inputs, candle, result, persisted)

        while clock.has_next:
            if should_stop():
                raise EvaluationStopped("operator stopped autonomous execution")
            with atomic_transaction(engine) as connection:
                for _ in range(chunk_size):
                    if not clock.has_next:
                        break
                    if should_stop():
                        raise EvaluationStopped("operator stopped autonomous execution")
                    observable = clock.step()
                    source = observable.current_bar
                    verify_closed_interval(calendar, watermark, source.timestamp)
                    candles = [
                        candle
                        for aggregator in aggregators.values()
                        for candle in aggregator.advance(source.timestamp)
                    ]
                    candles.extend(
                        candle
                        for aggregator in aggregators.values()
                        for candle in aggregator.push(source)
                    )
                    dispatch(connection, candles)
                    watermark = source.timestamp + MINUTE
                    if on_source_step is not None:
                        on_source_step(observable)
                advance_evaluation(
                    connection,
                    evaluation_id,
                    token,
                    cursor_index=clock.index,
                    available_at=watermark,
                    at=datetime.now(UTC),
                )
            committed_index = clock.index
            if on_commit is not None:
                on_commit()
        with atomic_transaction(engine) as connection:
            if should_stop():
                raise EvaluationStopped("operator stopped autonomous execution")
            verify_closed_interval(calendar, watermark, inputs.selected_end)
            dispatch(
                connection,
                [
                    candle
                    for aggregator in aggregators.values()
                    for candle in aggregator.advance(inputs.selected_end)
                ],
            )
            advance_evaluation(
                connection,
                evaluation_id,
                token,
                cursor_index=clock.index,
                available_at=inputs.selected_end,
                at=datetime.now(UTC),
            )
            finish_detection(connection, evaluation_id, token, datetime.now(UTC))
            complete_evaluation_if_ready(connection, evaluation_id, datetime.now(UTC))
        logger.info(
            "autonomous detector execution finished",
            extra={
                "cursor_index": clock.index,
                "detector_event_count": emission_order,
            },
        )
    except BaseException as error:
        lifecycle_error: Exception | None = None
        try:
            with atomic_transaction(engine) as connection:
                current = read_evaluation_run(connection, evaluation_id)
                if (
                    current is not None
                    and current.status is EvaluationStatus.RUNNING
                    and (current.owner_token == token)
                ):
                    if isinstance(error, EvaluationStopped):
                        cancel_evaluation(connection, evaluation_id, token, datetime.now(UTC))
                    else:
                        fail_evaluation(
                            connection,
                            evaluation_id,
                            token,
                            f"{type(error).__name__}: {error}",
                            datetime.now(UTC),
                        )
        except Exception as persistence_error:
            lifecycle_error = persistence_error
            logger.exception(
                "could not persist evaluation termination",
                extra={
                    "last_committed_cursor": committed_index,
                },
            )
        logger.log(
            20 if isinstance(error, EvaluationStopped) else 40,
            "autonomous detector execution cancelled"
            if isinstance(error, EvaluationStopped)
            else "autonomous detector execution failed",
            extra={
                "failure_reason": str(error),
                "last_committed_cursor": committed_index,
                "stream_run_id": active_stream,
            },
            exc_info=not isinstance(error, EvaluationStopped),
        )
        if not isinstance(error, EvaluationStopped):
            raise
        if lifecycle_error is not None:
            raise EvaluationError("could not persist evaluation cancellation") from lifecycle_error
    with engine.connect() as connection:
        final = read_evaluation_run(connection, evaluation_id)
        assert final is not None
        return final
