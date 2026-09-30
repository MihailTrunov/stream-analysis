"""SCRUM-81: real detector decisions survive durable instance restart.

The market-state chain is rebuilt by full replay. The detector receives its
non-terminal occurrence from the reloaded database row, not from that replay's
in-memory detector slot. No unsupported partial-runtime restore is assumed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from test_detector_runtime import bar, chain_series, config_with, pattern_selection

from alembic import command
from alembic.config import Config
from market_analysis.application.pattern_instance_bridge import lifecycle_step_from_intent
from market_analysis.config import ComponentSelection, ConfigParameter, DetectionAnalysisConfig
from market_analysis.detection import (
    COMPRESSION_V1,
    CONTINUATION_V1,
    REVERSAL_V1,
    CompressionDetector,
    ContinuationDetector,
    DetectorBinding,
    DetectorInput,
    DetectorRuntime,
    PatternDetector,
    PatternInstance,
    ReversalDetector,
    TransitionIntent,
    json_value,
)
from market_analysis.domain import Bar, Timeframe
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.pattern_instances import (
    advance_pattern_instance,
    create_pattern_instance,
    load_pattern_instance,
    load_pattern_instance_by_key,
)
from market_analysis.persistence.runs import create_run_snapshot, metadata


@dataclass(frozen=True)
class Scenario:
    name: str
    definition: PatternDefinition
    detector_factory: Callable[[], PatternDetector]
    config: DetectionAnalysisConfig
    bars: tuple[Bar, ...]
    checkpoint_index: int
    checkpoint_state: str
    next_state: str
    next_triggers: tuple[str, ...]
    frozen_ref_field: str


def _compression_config() -> DetectionAnalysisConfig:
    def parameter(name: str, value: int) -> ConfigParameter:
        return ConfigParameter(name=name, value=value)

    return DetectionAnalysisConfig(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        calendar_id="cal-v1",
        components=(
            ComponentSelection(
                component_id="atr",
                component_version="1",
                parameters=(parameter("period", 2),),
            ),
            ComponentSelection(
                component_id="range_state",
                component_version="1",
                parameters=(
                    parameter("chop_period", 3),
                    parameter("bandwidth_period", 4),
                    parameter("compression_reference_bars", 6),
                ),
            ),
        ),
        patterns=(pattern_selection(COMPRESSION_V1),),
    )


SCENARIOS = (
    Scenario(
        "reversal",
        REVERSAL_V1,
        ReversalDetector,
        config_with([pattern_selection(REVERSAL_V1)]),
        (*chain_series(), bar(15, "70", "110", "69")),
        14,
        "CANDIDATE",
        "CONFIRMED",
        ("protected_swing_break",),
        "source_leg_ref",
    ),
    Scenario(
        "compression",
        COMPRESSION_V1,
        CompressionDetector,
        _compression_config(),
        tuple(bar(i, str(10 + i % 2), "12", "9") for i in range(13)),
        11,
        "CANDIDATE",
        "ACTIVE",
        ("persistence_confirmed",),
        "source_range_ref",
    ),
    Scenario(
        "continuation",
        CONTINUATION_V1,
        ContinuationDetector,
        config_with([pattern_selection(CONTINUATION_V1)]),
        (*chain_series(), bar(15, "150", "160", "119")),
        14,
        "CANDIDATE",
        "CONFIRMED",
        ("ema_reclaim", "continuation_break"),
        "protected_swing_ref",
    ),
)


def _runtime(scenario: Scenario, run_id: str) -> DetectorRuntime:
    return DetectorRuntime(
        scenario.config,
        [DetectorBinding(scenario.definition, scenario.detector_factory(), reentrant=True)],
        run_id=run_id,
        dataset_revision_id="dataset-81-real-detectors",
    )


def _intent_from_event(event: object) -> TransitionIntent:
    """Map a committed event back to the public intent accepted by the bridge."""
    from market_analysis.detection import DetectorEvent

    assert isinstance(event, DetectorEvent)
    return TransitionIntent(
        pattern_id=event.pattern_id,
        pattern_version=event.pattern_version,
        instance_id=event.instance_id,
        from_state=event.from_state,
        to_state=event.to_state,
        trigger_id=event.trigger_id,
        event_time=event.event_time,
        detection_time=event.detection_time,
        rationale=event.rationale,
    )


def _semantic_steps(events: tuple[object, ...]) -> tuple[tuple[object, ...], ...]:
    from market_analysis.detection import DetectorEvent

    assert all(isinstance(event, DetectorEvent) for event in events)
    return tuple(
        (
            event.trigger_id,
            event.from_state,
            event.to_state,
            event.event_time,
            event.detection_time,
            json_value(event.rationale),
        )
        for event in events
    )


def _semantic_intents(intents: tuple[TransitionIntent, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            intent.trigger_id,
            intent.from_state,
            intent.to_state,
            intent.event_time,
            intent.detection_time,
            json_value(intent.rationale),
        )
        for intent in intents
    )


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_persisted_real_detector_next_decision_matches_uninterrupted_execution(
    scenario: Scenario,
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if backend == "postgres":
        url = os.getenv("STREAM_ANALYSIS_TEST_DATABASE_URL")
        if not url:
            pytest.skip("PostgreSQL integration URL is not configured")
        monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
    else:
        url = f"sqlite+pysqlite:///{tmp_path / 'detector-restart.db'}"

    run_id = uuid4()
    uninterrupted = _runtime(scenario, str(run_id))
    first_engine = create_engine(url)
    if backend == "sqlite":
        metadata.create_all(first_engine)
    with first_engine.begin() as connection:
        snapshot = create_run_snapshot(
            connection,
            run_id=run_id,
            dataset_revision_id=uninterrupted.dataset_revision_id,
            calendar_version="cal-v1",
            build_id="test-build-81",
            detection_config=scenario.config,
            pattern_definitions={scenario.definition.identity: scenario.definition},
        )
        assert snapshot.detection_config_hash == uninterrupted.detection_config_hash
        occurrence = None
        for item in scenario.bars[: scenario.checkpoint_index + 1]:
            result = uninterrupted.process_bar(item)
            if result.events and occurrence is None:
                opening = result.events[0]
                occurrence = create_pattern_instance(
                    connection,
                    run_id=run_id,
                    definition=scenario.definition,
                    binding_fingerprint=uninterrupted.binding_fingerprint,
                    occurrence_event_time=opening.event_time,
                    occurrence_detection_time=opening.detection_time,
                    occurrence_ordinal=0,
                    at=item.timestamp,
                )
            if occurrence is not None:
                occurrence = advance_pattern_instance(
                    connection,
                    occurrence.instance_id,
                    scenario.definition,
                    expected_revision=occurrence.revision,
                    bar=item,
                    steps=tuple(
                        lifecycle_step_from_intent(_intent_from_event(event))
                        for event in result.events
                    ),
                    context=result.instances[0].context,
                )
        assert occurrence is not None
        assert occurrence.state == scenario.checkpoint_state
        assert occurrence.context == uninterrupted.instances[0].context
        assert occurrence.context[scenario.frozen_ref_field]
        semantic_key = occurrence.instance_semantic_key
        instance_id = occurrence.instance_id
        revision = occurrence.revision
    first_engine.dispose()

    # A new connection/engine must recover the actual detector's typed state.
    restarted_engine = create_engine(url)
    with restarted_engine.begin() as connection:
        loaded = load_pattern_instance(connection, instance_id, scenario.definition)
        assert loaded is not None
        assert loaded.instance_semantic_key == semantic_key
        assert loaded.revision == revision
        assert loaded.context == uninterrupted.instances[0].context
        assert (
            load_pattern_instance_by_key(connection, run_id, semantic_key, scenario.definition)
            == loaded
        )

        # Rebuild only the market-state chain by replay. The detector below is
        # initialized from the loaded row, never from the replayed runtime slot.
        replayed = _runtime(scenario, str(run_id))
        for item in scenario.bars[: scenario.checkpoint_index + 1]:
            replayed.process_bar(item)
        next_bar = scenario.bars[scenario.checkpoint_index + 1]
        frame = replayed.aggregator.update(next_bar)
        resumed = scenario.detector_factory().process_bar(
            DetectorInput(
                frame=frame,
                definition=scenario.definition,
                parameters=scenario.definition.resolve_parameters({}),
                config=replayed.config,
                detection_config_hash=replayed.detection_config_hash,
                run_id=str(run_id),
                dataset_revision_id=replayed.dataset_revision_id,
                instance=PatternInstance(
                    scenario.definition.pattern_id,
                    scenario.definition.pattern_version,
                    loaded.instance_id,
                    loaded.state,
                    loaded.context,
                ),
            )
        )
        direct = uninterrupted.process_bar(next_bar)
        assert direct.frame.market_events_this_bar == frame.market_events_this_bar
        assert resumed.instance.state == direct.instances[0].state == scenario.next_state
        assert resumed.instance.context == direct.instances[0].context
        assert tuple(intent.trigger_id for intent in resumed.transitions) == scenario.next_triggers
        assert _semantic_intents(resumed.transitions) == _semantic_steps(direct.events)

        advanced = advance_pattern_instance(
            connection,
            loaded.instance_id,
            scenario.definition,
            expected_revision=loaded.revision,
            bar=next_bar,
            steps=tuple(lifecycle_step_from_intent(intent) for intent in resumed.transitions),
            context=resumed.instance.context,
        )
        assert advanced.state == scenario.next_state
        assert advanced.context == direct.instances[0].context
        assert tuple(event.sequence for event in advanced.detector_events) == tuple(
            range(len(advanced.detector_events))
        )
        for stored, observed in zip(
            advanced.detector_events[-len(direct.events) :], direct.events, strict=True
        ):
            assert (
                stored.sequence,
                stored.transition_reason,
                stored.old_state,
                stored.new_state,
                stored.event_time,
                stored.detection_time,
                json_value(stored.rationale),
            ) == (
                observed.sequence,
                observed.trigger_id,
                observed.from_state,
                observed.to_state,
                observed.event_time,
                observed.detection_time,
                json_value(observed.rationale),
            )
            assert stored.event_kind == observed.to_state.upper()
        assert advanced.detector_events[-1].build_id == "test-build-81"
        assert advanced.detector_events[-1].detection_config_hash == (
            uninterrupted.detection_config_hash
        )
    restarted_engine.dispose()
