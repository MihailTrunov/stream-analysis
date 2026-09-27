from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import (
    ConditionGroup,
    LifecycleRunner,
    PatternDefinition,
    PatternDefinitionError,
    TransitionSpec,
    assert_semantic_change_is_versioned,
)


def bar(index: int, *, complete: bool = True) -> Bar:
    return Bar(
        "US30", Timeframe.M1, datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        "1", "1", "1", "1", is_complete=complete,
    )


def lifecycle(
    states: tuple[str, ...],
    transitions: tuple[TransitionSpec, ...],
    precedence: tuple[str, ...] = (),
    chains: tuple[tuple[str, str], ...] = (),
    terminal: tuple[str, ...] = (),
) -> PatternDefinition:
    triggers = tuple(dict.fromkeys(edge.trigger_id for edge in transitions))
    return PatternDefinition(
        "fixture", "1", "Fixture", "Lifecycle fixture", (), (), (), states, transitions,
        (ConditionGroup("events", triggers),), precedence, (), triggers,
        terminal_states=terminal, same_bar_chains=chains,
    )


def test_reduced_lifecycle_and_expiry_are_terminal() -> None:
    definition = lifecycle(
        ("idle", "candidate", "confirmed", "expired"),
        (
            TransitionSpec("idle", "candidate", "cross"),
            TransitionSpec("candidate", "confirmed", "confirm"),
            TransitionSpec("candidate", "expired", "expiry", "candidate timed out"),
        ),
        ("confirm", "expiry"),
    )
    runner = LifecycleRunner(definition)
    assert runner.advance(bar(0), {"cross"})[0].to_state == "candidate"
    assert runner.advance(bar(1), {"expiry"})[0].reason == "candidate timed out"
    assert runner.advance(bar(2), {"cross", "confirm"}) == ()
    assert runner.state == "expired"


def test_candidate_confirmed_active_completed_and_no_reactivation() -> None:
    definition = lifecycle(
        ("candidate", "confirmed", "active", "completed"),
        (
            TransitionSpec("candidate", "confirmed", "confirm"),
            TransitionSpec("confirmed", "active", "activate"),
            TransitionSpec("active", "completed", "finish"),
        ),
    )
    runner = LifecycleRunner(definition)
    for index, trigger in enumerate(("confirm", "activate", "finish")):
        assert len(runner.advance(bar(index), {trigger})) == 1
    assert runner.state == "completed"
    assert runner.advance(bar(3), {"confirm", "activate", "finish"}) == ()


def test_confirmation_bar_can_also_release_to_active_when_declared() -> None:
    definition = lifecycle(
        ("candidate", "confirmed", "active", "completed"),
        (
            TransitionSpec("candidate", "confirmed", "confirm"),
            TransitionSpec("confirmed", "active", "release"),
            TransitionSpec("active", "completed", "finish"),
        ),
        chains=(("confirm", "release"),),
    )
    runner = LifecycleRunner(definition)
    events = runner.advance(bar(0), {"release", "confirm"})
    assert [(event.sequence, event.from_state, event.to_state) for event in events] == [
        (0, "candidate", "confirmed"), (1, "confirmed", "active"),
    ]
    assert events[0].bar_timestamp == events[1].bar_timestamp
    assert runner.state == "active"
    assert runner.advance(bar(1), {"finish"})[0].to_state == "completed"


def test_candidate_reclaimed_confirmed_same_bar_records_two_ordered_events() -> None:
    definition = lifecycle(
        ("candidate", "reclaimed", "confirmed"),
        (
            TransitionSpec("candidate", "reclaimed", "reclaim"),
            TransitionSpec("reclaimed", "confirmed", "break"),
        ),
        chains=(("reclaim", "break"),),
    )
    runner = LifecycleRunner(definition)
    events = runner.advance(bar(0), {"break", "reclaim"})
    assert [(event.sequence, event.from_state, event.to_state) for event in events] == [
        (0, "candidate", "reclaimed"), (1, "reclaimed", "confirmed"),
    ]
    assert events[0].bar_timestamp == events[1].bar_timestamp
    assert runner.history == events
    assert len(LifecycleRunner(replace(definition, same_bar_chains=())).advance(
        bar(0), {"break", "reclaim"}
    )) == 1


@pytest.mark.parametrize(
    ("precedence", "expected"),
    [(("confirm", "invalidate", "expiry"), "confirmed"),
     (("invalidate", "confirm", "expiry"), "invalidated"),
     (("expiry", "confirm", "invalidate"), "expired")],
)
def test_confirmation_invalidation_and_expiry_boundary_precedence(
    precedence: tuple[str, ...], expected: str,
) -> None:
    definition = lifecycle(
        ("candidate", "confirmed", "invalidated", "expired"),
        (
            TransitionSpec("candidate", "confirmed", "confirm"),
            TransitionSpec("candidate", "invalidated", "invalidate"),
            TransitionSpec("candidate", "expired", "expiry"),
        ),
        precedence,
    )
    events = LifecycleRunner(definition).advance(bar(60), {"expiry", "invalidate", "confirm"})
    assert len(events) == 1
    assert events[0].to_state == expected


def test_graph_validation_and_invalid_evidence_are_atomic() -> None:
    edges = (TransitionSpec("idle", "candidate", "start"),)
    with pytest.raises(PatternDefinitionError, match="unreachable"):
        lifecycle(("idle", "candidate", "orphan"), edges)
    with pytest.raises(PatternDefinitionError, match="terminal states"):
        lifecycle(("idle", "completed"), (
            TransitionSpec("idle", "completed", "start"),
            TransitionSpec("completed", "idle", "restart"),
        ))
    with pytest.raises(PatternDefinitionError, match="unknown lifecycle state"):
        lifecycle(("idle", "candidate"), (TransitionSpec("idle", "missing", "start"),))
    with pytest.raises(PatternDefinitionError, match="terminal states"):
        lifecycle(("idle", "invalidated"), (
            TransitionSpec("idle", "invalidated", "start"),
            TransitionSpec("invalidated", "idle", "restart"),
        ))
    with pytest.raises(PatternDefinitionError, match="same-bar chain"):
        lifecycle(("idle", "candidate"), edges, chains=(("start", "missing"),))
    definition = lifecycle(("idle", "candidate"), edges)
    runner = LifecycleRunner(definition)
    with pytest.raises(PatternDefinitionError, match="undeclared trigger"):
        runner.advance(bar(0), {"unknown"})
    with pytest.raises(PatternDefinitionError, match="completed bar"):
        runner.advance(bar(0, complete=False), {"start"})
    assert runner.history == ()
    assert runner.advance(bar(0), {"start"})[0].to_state == "candidate"
    with pytest.raises(PatternDefinitionError, match="strictly ordered"):
        runner.advance(bar(0), set())


def test_same_bar_cycle_stops_before_revisiting_state() -> None:
    definition = lifecycle(
        ("idle", "candidate"),
        (TransitionSpec("idle", "candidate", "start"),
         TransitionSpec("candidate", "idle", "reset")),
        chains=(("start", "reset"), ("reset", "start")),
    )
    runner = LifecycleRunner(definition)
    assert [event.trigger_id for event in runner.advance(bar(0), {"start", "reset"})] == [
        "start"
    ]


def test_only_current_bar_evidence_can_drive_transitions() -> None:
    definition = lifecycle(
        ("idle", "candidate", "confirmed"),
        (TransitionSpec("idle", "candidate", "start"),
         TransitionSpec("candidate", "confirmed", "confirm")),
        chains=(("start", "confirm"),),
    )
    runner = LifecycleRunner(definition)
    assert [event.trigger_id for event in runner.advance(bar(0), {"start"})] == [
        "start"
    ]
    assert runner.advance(bar(1), set()) == ()
    assert runner.state == "candidate"
    assert [event.trigger_id for event in runner.advance(bar(2), {"confirm"})] == [
        "confirm"
    ]


def test_new_lifecycle_semantics_require_version_and_serialize_stably() -> None:
    original = lifecycle(
        ("idle", "candidate"), (TransitionSpec("idle", "candidate", "start"),)
    )
    changed = replace(original, terminal_states=("candidate",))
    assert "terminal_states" not in original.canonical_dict()
    assert changed.canonical_json() == replace(changed).canonical_json()
    with pytest.raises(PatternDefinitionError, match="pattern_version"):
        assert_semantic_change_is_versioned(original, changed)
    assert_semantic_change_is_versioned(original, replace(changed, pattern_version="2"))
    reason_changed = replace(original, transitions=(
        TransitionSpec("idle", "candidate", "start", "candidate opened"),
    ))
    with pytest.raises(PatternDefinitionError, match="pattern_version"):
        assert_semantic_change_is_versioned(original, reason_changed)
