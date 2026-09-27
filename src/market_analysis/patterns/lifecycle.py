"""Deterministic lifecycle transitions from one completed bar's evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from market_analysis.domain import Bar, Timeframe

from .definition import PatternDefinition, PatternDefinitionError, TransitionSpec


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    bar_timestamp: datetime
    sequence: int
    from_state: str
    to_state: str
    trigger_id: str
    reason: str


class LifecycleRunner:
    """Advance one occurrence using only trigger IDs observed on the supplied bar.

    Detector code owns trigger calculation. A trigger can be reused on a later
    bar, but each state and trigger can participate at most once on one bar.
    """

    def __init__(
        self,
        definition: PatternDefinition,
        *,
        initial_state: str | None = None,
    ) -> None:
        self.definition = definition
        self.state = initial_state or definition.lifecycle_states[0]
        if self.state not in definition.lifecycle_states:
            raise PatternDefinitionError("initial state is undeclared")
        self._last_timestamp: datetime | None = None
        self._instrument_id: str | None = None
        self._timeframe: Timeframe | None = None
        self._history: list[LifecycleTransition] = []
        self._conditions = {
            condition for group in definition.condition_groups for condition in group.condition_ids
        }

    @property
    def history(self) -> tuple[LifecycleTransition, ...]:
        return tuple(self._history)

    def advance(
        self,
        bar: Bar,
        triggered_conditions: Iterable[str],
    ) -> tuple[LifecycleTransition, ...]:
        if not isinstance(bar, Bar) or not bar.is_complete:
            raise PatternDefinitionError("lifecycle requires a completed bar")
        if self._last_timestamp is not None and bar.timestamp <= self._last_timestamp:
            raise PatternDefinitionError("completed bars must be strictly ordered")
        if self._instrument_id is not None and (
            bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe
        ):
            raise PatternDefinitionError("bar instrument/timeframe changed")
        evidence = frozenset(triggered_conditions)
        if evidence - self._conditions:
            raise PatternDefinitionError("undeclared trigger evidence")

        # Validate before committing any state or history mutation.
        events: list[LifecycleTransition] = []
        state = self.state
        visited = {state}
        previous_trigger: str | None = None
        used_triggers: set[str] = set()
        precedence = {
            trigger: rank
            for rank, trigger in enumerate(self.definition.simultaneous_precedence)
        }
        while state not in self.definition.effective_terminal_states:
            candidates = [
                transition
                for transition in self.definition.transitions
                if transition.from_state == state
                and transition.trigger_id in evidence
                and transition.trigger_id not in used_triggers
                and transition.to_state not in visited
                and (
                    previous_trigger is None
                    or (previous_trigger, transition.trigger_id)
                    in self.definition.same_bar_chains
                )
            ]
            if not candidates:
                break
            selected = min(
                candidates,
                key=lambda item: precedence.get(item.trigger_id, len(precedence)),
            )
            events.append(self._event(bar, len(events), selected))
            state = selected.to_state
            visited.add(state)
            used_triggers.add(selected.trigger_id)
            previous_trigger = selected.trigger_id

        self.state = state
        self._history.extend(events)
        self._last_timestamp = bar.timestamp
        self._instrument_id = bar.instrument_id
        self._timeframe = bar.timeframe
        return tuple(events)

    @staticmethod
    def _event(bar: Bar, sequence: int, transition: TransitionSpec) -> LifecycleTransition:
        return LifecycleTransition(
            bar_timestamp=bar.timestamp,
            sequence=sequence,
            from_state=transition.from_state,
            to_state=transition.to_state,
            trigger_id=transition.trigger_id,
            reason=transition.reason or transition.trigger_id,
        )
