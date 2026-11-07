"""Bounded registered outcome work after detector/context/population persistence."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice

from sqlalchemy import Engine, select

from market_analysis.application.outcome_evaluation import OutcomeAlgorithm, evaluate_outcome
from market_analysis.application.outcome_registry import OUTCOME_ALGORITHMS, OUTCOME_DEFINITIONS
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS
from market_analysis.application.structural_event_inventory import structural_event_inventory
from market_analysis.code_version import CodeVersion
from market_analysis.config import EvaluationPlan, OutcomeSelection
from market_analysis.domain import BarSequence, TradingCalendar
from market_analysis.domain.evaluation import EvaluationError
from market_analysis.domain.outcomes import OutcomeDefinition
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.evaluations import (
    EvaluationRunRecord,
    advance_evaluation,
    evaluation_event_sets,
)
from market_analysis.persistence.pattern_instances import (
    PersistedDetectorEvent,
    load_pattern_instance,
    pattern_instance_transitions,
)
from market_analysis.persistence.transactions import atomic_transaction

OUTCOME_BATCH_SIZE = 64


@dataclass(frozen=True)
class _Task:
    event: PersistedDetectorEvent
    pattern: PatternDefinition
    definition: OutcomeDefinition
    definitions: Mapping[tuple[str, str], OutcomeDefinition]
    selection: OutcomeSelection
    algorithm: OutcomeAlgorithm
    available_at: datetime


def _tasks(engine: Engine, record: EvaluationRunRecord) -> Iterator[_Task]:
    streams = {str(stream.run_id): stream for stream in record.inputs.streams}
    plans = {
        key: EvaluationPlan.from_canonical_json(stream.plan_json) for key, stream in streams.items()
    }
    if not any(
        (item.outcome_id, item.outcome_version) in OUTCOME_ALGORITHMS
        for plan in plans.values()
        for item in plan.outcomes
    ):
        return
    definitions = {
        key: {
            definition.identity: definition
            for definition in (
                OutcomeDefinition.model_validate_json(raw) for raw in stream.outcome_definitions
            )
        }
        for key, stream in streams.items()
    }
    after = -1
    while True:
        # Close the reader before yielding work to a separate write transaction.
        with engine.connect() as connection:
            member = (
                connection.execute(
                    select(
                        evaluation_event_sets,
                        pattern_instance_transitions.c.instance_id,
                    )
                    .join(
                        pattern_instance_transitions,
                        evaluation_event_sets.c.event_id == pattern_instance_transitions.c.event_id,
                    )
                    .where(
                        evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
                        evaluation_event_sets.c.eligible == 1,
                        evaluation_event_sets.c.emission_order > after,
                    )
                    .order_by(evaluation_event_sets.c.emission_order)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if member is None:
                return
            stream = streams[member["source_run_id"]]
            pattern = PATTERN_DEFINITIONS[(stream.pattern_id, stream.pattern_version)]
            occurrence = load_pattern_instance(connection, member["instance_id"], pattern)
            if occurrence is None:
                raise EvaluationError("outcome source occurrence is missing")
            event = next(
                item for item in occurrence.detector_events if item.event_id == member["event_id"]
            )
        after = member["emission_order"]
        available_at = member["available_at"]
        available_at = (
            available_at.replace(tzinfo=UTC)
            if available_at.tzinfo is None
            else available_at.astimezone(UTC)
        )
        for selection in plans[member["source_run_id"]].outcomes:
            identity = selection.outcome_id, selection.outcome_version
            if identity not in OUTCOME_ALGORITHMS:
                continue  # Explicit injected/unavailable selections remain pending, never complete.
            definition = definitions[member["source_run_id"]][identity]
            if definition.fingerprint() != OUTCOME_DEFINITIONS[identity].fingerprint():
                raise EvaluationError("outcome definition differs from its pinned input")
            yield _Task(
                event,
                pattern,
                definition,
                definitions[member["source_run_id"]],
                selection,
                OUTCOME_ALGORITHMS[identity],
                available_at,
            )


def evaluate_registered_outcomes(
    engine: Engine,
    record: EvaluationRunRecord,
    sequence: BarSequence,
    calendar: TradingCalendar,
    *,
    token: str,
    check_stop: Callable[[], None],
    revision_provider: Callable[[str], CodeVersion],
    on_commit: Callable[[], None] | None = None,
) -> None:
    inventory = structural_event_inventory(record, sequence, calendar, check_stop=check_stop)
    tasks = _tasks(engine, record)
    while True:
        check_stop()
        batch = tuple(islice(tasks, OUTCOME_BATCH_SIZE))
        if not batch:
            return
        with atomic_transaction(engine) as connection:
            for task in batch:
                check_stop()
                candidates = inventory.get(task.event.run_id, ())
                _, day_end = calendar.trading_day_bounds(task.event.detection_time)
                first = bisect_left(
                    candidates, task.available_at, key=lambda item: item.available_at
                )
                last = bisect_right(candidates, day_end, key=lambda item: item.available_at)
                evaluate_outcome(
                    connection,
                    event=task.event,
                    pattern=task.pattern,
                    definition=task.definition,
                    definitions=task.definitions,
                    selection=task.selection,
                    bar_sequence=sequence,
                    calendar=calendar,
                    algorithm=task.algorithm,
                    events=candidates[first:last],
                    revision_provider=revision_provider,
                )
            # Fence the whole observation batch against cancellation/recovery of its owner.
            advance_evaluation(
                connection,
                str(record.inputs.evaluation_id),
                token,
                cursor_index=record.inputs.execution_bar_count - 1,
                available_at=record.inputs.selected_end,
                at=datetime.now(UTC),
            )
        if on_commit is not None:
            on_commit()
