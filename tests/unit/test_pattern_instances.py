from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DatabaseError

from alembic import command
from alembic.config import Config
from market_analysis.application.pattern_instance_bridge import lifecycle_step_from_intent
from market_analysis.config import (
    DetectionAnalysisConfig,
    PatternSelection,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.config.hashing import (
    CANONICALIZATION_VERSION,
    DETECTION_HASH_VERSION,
    HASH_ALGORITHM,
)
from market_analysis.detection.records import TransitionIntent
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    MarketEvent,
    MarketEventType,
    MarketStateError,
    MarketStateFrame,
    SwingPoint,
    SwingType,
    market_event_semantic_ref,
)
from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    PatternDefinition,
    TransitionSpec,
)
from market_analysis.patterns.instance_context import (
    PatternInstanceError,
    decode_context,
    encode_context,
    instance_semantic_key,
)
from market_analysis.persistence.pattern_instances import (
    LifecycleStep,
    PatternInstanceRecord,
    _event_ref,
    advance_pattern_instance,
    create_pattern_instance,
    load_pattern_instance,
    load_pattern_instance_by_key,
    pattern_instances,
)
from market_analysis.persistence.runs import create_run_snapshot, metadata

START = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
BINDING = "a" * 64
EVENT = "b" * 64


def definition(pattern_id: str = "reversal", version: str = "1") -> PatternDefinition:
    return PatternDefinition(
        pattern_id,
        version,
        pattern_id,
        "test lifecycle",
        (),
        (),
        (),
        ("idle", "candidate", "active", "completed", "invalidated", "expired"),
        (
            TransitionSpec("idle", "candidate", "start"),
            TransitionSpec("candidate", "active", "confirm"),
            TransitionSpec("active", "completed", "finish"),
            TransitionSpec("candidate", "invalidated", "invalidate"),
            TransitionSpec("candidate", "expired", "expire"),
        ),
        (ConditionGroup("conditions", ("start", "confirm", "finish", "invalidate", "expire")),),
        ("confirm", "invalidate", "expire"),
        (
            ContextFieldSpec("qualifying_count", "int"),
            ContextFieldSpec("anchor_price", "decimal"),
            ContextFieldSpec("armed", "bool"),
            ContextFieldSpec("direction", "string"),
            ContextFieldSpec("candidate_at", "datetime"),
            ContextFieldSpec("frozen_event", "event_ref"),
        ),
        ("start", "confirm"),
        same_bar_chains=(("start", "confirm"),),
    )


def bar(at: datetime) -> Bar:
    return Bar(
        instrument_id="US30",
        timeframe=Timeframe.M1,
        timestamp=at,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
    )


def run(connection: Connection, selected: PatternDefinition, *, dataset: str = "dataset-1") -> UUID:
    run_id = uuid4()
    config = DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="demo-v1",
        patterns=(PatternSelection.from_definition(selected),),
    )
    create_run_snapshot(
        connection,
        run_id=run_id,
        dataset_revision_id=dataset,
        calendar_version="demo-v1",
        build_id="test-build",
        detection_config=config,
        pattern_definitions={selected.identity: selected},
    )
    return run_id


def create(
    connection: Connection, selected: PatternDefinition, run_id: UUID, ordinal: int = 0
) -> PatternInstanceRecord:
    return create_pattern_instance(
        connection,
        run_id=run_id,
        definition=selected,
        binding_fingerprint=BINDING,
        occurrence_event_time=START,
        occurrence_detection_time=START,
        occurrence_ordinal=ordinal,
        at=START,
    )


def test_cross_run_semantic_parity_and_overlapping_occurrences() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition()
    with engine.begin() as connection:
        first = create(connection, selected, run(connection, selected))
        other_run = run(connection, selected)
        equivalent = create(connection, selected, other_run)
        overlap = create(connection, selected, other_run, 1)
        assert first.instance_id != equivalent.instance_id
        assert first.instance_semantic_key == equivalent.instance_semantic_key
        assert overlap.instance_semantic_key != equivalent.instance_semantic_key
        assert overlap.instance_id != equivalent.instance_id
        with pytest.raises(PatternInstanceError, match="already exists"):
            create(connection, selected, other_run)
    engine.dispose()


@pytest.mark.parametrize("pattern_id", ["reversal", "compression", "continuation"])
def test_typed_context_survives_restart_for_planned_detector_shapes(pattern_id: str) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition(pattern_id)
    with engine.begin() as connection:
        instance = create(connection, selected, run(connection, selected))
        first_bar = START + timedelta(minutes=1)
        context = {
            "qualifying_count": 3,
            "anchor_price": Decimal("104.25"),
            "armed": True,
            "direction": "up",
            "candidate_at": first_bar,
            "frozen_event": EVENT,
        }
        changed = advance_pattern_instance(
            connection,
            instance.instance_id,
            selected,
            expected_revision=0,
            bar=bar(first_bar),
            steps=(LifecycleStep("idle", "candidate", "start", START, first_bar),),
            context=context,
        )
        assert changed.state == "candidate"
        assert changed.revision == 1
        assert changed.context == context
        assert changed.phase_times["candidate"] == first_bar
        with pytest.raises(TypeError):
            changed.context["qualifying_count"] = 5  # type: ignore[index]
        with pytest.raises(TypeError):
            changed.phase_times["candidate"] = START  # type: ignore[index]
        assert load_pattern_instance(connection, instance.instance_id, selected) == changed
    with engine.begin() as connection:
        restarted = load_pattern_instance(connection, instance.instance_id, selected)
        assert restarted is not None
        assert restarted.context["frozen_event"] == EVENT
        next_bar = first_bar + timedelta(minutes=1)
        resumed = advance_pattern_instance(
            connection,
            restarted.instance_id,
            selected,
            expected_revision=restarted.revision,
            bar=bar(next_bar),
            steps=(LifecycleStep("candidate", "active", "confirm", first_bar, next_bar),),
            context={**restarted.context, "qualifying_count": 4},
        )
        assert resumed.state == "active"
        assert resumed.context["qualifying_count"] == 4
        assert resumed.context["frozen_event"] == EVENT
    engine.dispose()


def test_same_bar_chain_is_one_revision_and_terminal_is_final() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition()
    with engine.begin() as connection:
        instance = create(connection, selected, run(connection, selected))
        first_bar = START + timedelta(minutes=1)
        chained = advance_pattern_instance(
            connection,
            instance.instance_id,
            selected,
            expected_revision=0,
            bar=bar(first_bar),
            steps=(
                LifecycleStep("idle", "candidate", "start", START, first_bar),
                LifecycleStep("candidate", "active", "confirm", first_bar, first_bar),
            ),
            context={},
        )
        assert chained.revision == 1
        assert [item.sequence for item in chained.transitions] == [0, 1]
        assert len(set(chained.emitted_event_refs)) == 2
        assert chained.instance_id == instance.instance_id
        next_bar = first_bar + timedelta(minutes=1)
        completed = advance_pattern_instance(
            connection,
            instance.instance_id,
            selected,
            expected_revision=1,
            bar=bar(next_bar),
            steps=(LifecycleStep("active", "completed", "finish", next_bar, next_bar),),
            context={},
        )
        assert completed.state == "completed"
        assert completed.revision == 2
        with pytest.raises(PatternInstanceError, match="terminal"):
            advance_pattern_instance(
                connection,
                instance.instance_id,
                selected,
                expected_revision=2,
                bar=bar(next_bar + timedelta(minutes=1)),
                steps=(),
                context={},
            )
    engine.dispose()


def test_restarted_next_transition_matches_uninterrupted_execution() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition()
    first_bar = START + timedelta(minutes=1)
    next_bar = first_bar + timedelta(minutes=1)
    with engine.begin() as connection:
        uninterrupted = create(connection, selected, run(connection, selected))
        restarted = create(connection, selected, run(connection, selected))
        for occurrence in (uninterrupted, restarted):
            advance_pattern_instance(
                connection,
                occurrence.instance_id,
                selected,
                expected_revision=0,
                bar=bar(first_bar),
                steps=(LifecycleStep("idle", "candidate", "start", START, first_bar),),
                context={"qualifying_count": 2, "frozen_event": EVENT},
            )
        direct = advance_pattern_instance(
            connection,
            uninterrupted.instance_id,
            selected,
            expected_revision=1,
            bar=bar(next_bar),
            steps=(LifecycleStep("candidate", "active", "confirm", START, next_bar),),
            context={"qualifying_count": 3, "frozen_event": EVENT},
        )
    with engine.begin() as connection:
        loaded = load_pattern_instance(connection, restarted.instance_id, selected)
        assert loaded is not None
        resumed = advance_pattern_instance(
            connection,
            loaded.instance_id,
            selected,
            expected_revision=loaded.revision,
            bar=bar(next_bar),
            steps=(LifecycleStep("candidate", "active", "confirm", START, next_bar),),
            context={**loaded.context, "qualifying_count": 3},
        )
        assert resumed.instance_semantic_key == direct.instance_semantic_key
        assert resumed.emitted_event_refs == direct.emitted_event_refs
        assert resumed.context == direct.context
        assert resumed.phase_times == direct.phase_times
    engine.dispose()


def test_stale_revision_invalid_chain_and_definition_drift_are_rejected() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition()
    with engine.begin() as connection:
        instance = create(connection, selected, run(connection, selected))
        first_bar = START + timedelta(minutes=1)
        with pytest.raises(PatternInstanceError, match="stale"):
            advance_pattern_instance(
                connection,
                instance.instance_id,
                selected,
                expected_revision=1,
                bar=bar(first_bar),
                steps=(),
                context={},
            )
        with pytest.raises(PatternInstanceError, match="chain differs"):
            advance_pattern_instance(
                connection,
                instance.instance_id,
                selected,
                expected_revision=0,
                bar=bar(first_bar),
                steps=(LifecycleStep("idle", "active", "confirm", START, first_bar),),
                context={},
            )
        assert load_pattern_instance(connection, instance.instance_id, selected) == instance
        drifted = replace(selected, context_schema=selected.context_schema[:-1])
        with pytest.raises(PatternInstanceError, match="incompatible"):
            load_pattern_instance(connection, instance.instance_id, drifted)
    engine.dispose()


def test_context_type_schema_and_digest_validation() -> None:
    selected = definition()
    context = {"anchor_price": Decimal("1.5"), "candidate_at": START, "frozen_event": EVENT}
    encoded = encode_context(selected, context)
    assert decode_context(selected, encoded) == context
    with pytest.raises(PatternInstanceError, match="undeclared"):
        encode_context(selected, {"debug": "not persisted"})
    with pytest.raises(PatternInstanceError, match="integer"):
        encode_context(selected, {"qualifying_count": True})
    with pytest.raises(PatternInstanceError, match="digest"):
        encode_context(selected, {"frozen_event": "not-a-digest"})
    with pytest.raises(PatternInstanceError, match="schema/version"):
        decode_context(replace(selected, context_schema=selected.context_schema[:-1]), encoded)


def test_frozen_market_event_reference_is_cross_run_stable_and_not_invented() -> None:
    detection_time = START + timedelta(minutes=1)
    evidence = SwingPoint(
        swing_index=0,
        swing_type=SwingType.SWING_HIGH,
        definition_id="swing-v1",
        event_time=START,
        event_price=Decimal("102"),
        detection_time=detection_time,
        confirmation_close=Decimal("100"),
        candidate_bar_index=0,
        confirmation_bar_index=1,
        atr_period=1,
        atr_at_extreme=Decimal("1"),
        reversal_atr_multiplier=Decimal("2"),
        threshold_points=Decimal("2"),
        bars_to_confirmation=1,
        previous_swing=None,
    )
    event = MarketEvent(
        MarketEventType.SWING_POINT_CONFIRMED,
        START,
        detection_time,
        0,
        "swing-point",
        evidence,
    )
    frame = MarketStateFrame(
        bar(detection_time),
        2,
        "run-a",
        "dataset-1",
        "c" * 64,
        None,
        {},
        {},
        {},
        (event,),
    )
    reference = market_event_semantic_ref(frame, event)
    assert reference == market_event_semantic_ref(replace(frame, run_id="run-b"), event)
    assert reference != market_event_semantic_ref(
        replace(frame, dataset_revision_id="dataset-2"), event
    )
    with pytest.raises(MarketStateError, match="not emitted"):
        market_event_semantic_ref(frame, replace(event, ordinal=1))
    assert (
        decode_context(definition(), encode_context(definition(), {"frozen_event": reference}))[
            "frozen_event"
        ]
        == reference
    )


def test_lookup_by_semantic_key_and_registered_extra_version() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition(version="1")
    extra = definition(version="2")
    with engine.begin() as connection:
        run_id = run(connection, selected)
        occurrence = create(connection, extra, run_id)
        assert (
            load_pattern_instance_by_key(
                connection, run_id, occurrence.instance_semantic_key, extra
            )
            == occurrence
        )
        assert (
            load_pattern_instance_by_key(
                connection, uuid4(), occurrence.instance_semantic_key, extra
            )
            is None
        )
        with pytest.raises(PatternInstanceError, match="incompatible"):
            load_pattern_instance_by_key(
                connection, run_id, occurrence.instance_semantic_key, selected
            )
        with pytest.raises(PatternInstanceError, match="not selected"):
            create(connection, definition("unselected"), run_id)
    engine.dispose()


def test_runtime_intent_rationale_round_trip_and_validation() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = definition()
    at = START + timedelta(minutes=1)
    intent = TransitionIntent(
        pattern_id=selected.pattern_id,
        pattern_version=selected.pattern_version,
        instance_id="runtime-slot",
        from_state="idle",
        to_state="candidate",
        trigger_id="start",
        event_time=START,
        detection_time=at,
        rationale={
            "condition": "start",
            "source_ordinal": 1,
            "threshold": Decimal("2.5"),
            "observed_at": START,
            "facts": ("cross", "confirmed"),
        },
    )
    step = lifecycle_step_from_intent(intent)
    assert step.rationale is not None
    assert step.rationale["threshold"] == "2.5"
    assert step.rationale["observed_at"] == "2026-09-30T09:00:00Z"
    with engine.begin() as connection:
        occurrence = create(connection, selected, run(connection, selected))
        changed = advance_pattern_instance(
            connection,
            occurrence.instance_id,
            selected,
            expected_revision=0,
            bar=bar(at),
            steps=(step,),
            context={},
        )
        evidence = changed.transitions[0]
        assert evidence.step.rationale == evidence.rationale
        assert evidence.rationale is not None
        assert evidence.rationale["condition"] == step.rationale["condition"]
        assert evidence.rationale["threshold"] == step.rationale["threshold"]
        assert evidence.rationale["facts"] == ("cross", "confirmed")
        with pytest.raises(TypeError):
            evidence.rationale["condition"] = "confirm"  # type: ignore[index]
        assert load_pattern_instance(connection, occurrence.instance_id, selected) == changed
        with pytest.raises(PatternInstanceError, match="not declared"):
            advance_pattern_instance(
                connection,
                occurrence.instance_id,
                selected,
                expected_revision=1,
                bar=bar(at + timedelta(minutes=1)),
                steps=(
                    LifecycleStep(
                        "candidate",
                        "active",
                        "confirm",
                        at,
                        at + timedelta(minutes=1),
                        {"condition": "finish"},
                    ),
                ),
                context={},
            )
        with pytest.raises(PatternInstanceError, match="plain JSON"):
            advance_pattern_instance(
                connection,
                occurrence.instance_id,
                selected,
                expected_revision=1,
                bar=bar(at + timedelta(minutes=1)),
                steps=(
                    LifecycleStep(
                        "candidate",
                        "active",
                        "confirm",
                        at,
                        at + timedelta(minutes=1),
                        {"condition": "confirm", "bad": float("nan")},
                    ),
                ),
                context={},
            )
        with pytest.raises(PatternInstanceError, match="keys must be strings"):
            advance_pattern_instance(
                connection,
                occurrence.instance_id,
                selected,
                expected_revision=1,
                bar=bar(at + timedelta(minutes=1)),
                steps=(
                    LifecycleStep(
                        "candidate",
                        "active",
                        "confirm",
                        at,
                        at + timedelta(minutes=1),
                        {"condition": "confirm", "nested": {1: "would change on read"}},
                    ),
                ),
                context={},
            )
        assert load_pattern_instance(connection, occurrence.instance_id, selected) == changed
        nested = advance_pattern_instance(
            connection,
            occurrence.instance_id,
            selected,
            expected_revision=1,
            bar=bar(at + timedelta(minutes=1)),
            steps=(
                LifecycleStep(
                    "candidate",
                    "active",
                    "confirm",
                    at,
                    at + timedelta(minutes=1),
                    {"condition": "confirm", "nested": {"threshold": "2.5"}},
                ),
            ),
            context={},
        )
        nested_rationale = nested.transitions[-1].rationale
        assert nested_rationale is not None
        with pytest.raises(TypeError):
            nested_rationale["nested"]["threshold"] = "changed"  # type: ignore[index]
    engine.dispose()


def _typed_rationale(condition: str, reference: str = EVENT) -> dict[str, object]:
    return {
        "schema": "detector-evidence-v1",
        "condition": condition,
        "source_market_event_refs": [reference],
        "items": [
            {
                "condition_id": condition,
                "status": "PASS",
                "value": {"type": "decimal", "value": "101.25"},
                "operator": ">=",
                "threshold": {"type": "decimal", "value": "100"},
                "units": "points",
                "source_refs": [reference],
                "features": {
                    "observed_at": {"type": "datetime", "value": "2026-09-30T09:00:00Z"},
                    "count": {"type": "integer", "value": 5},
                    "eligible": {"type": "boolean", "value": True},
                    "source": {"type": "event_ref", "value": reference},
                    "label": {"type": "string", "value": "source swing"},
                },
            }
        ],
    }


def test_strict_detector_events_round_trip_and_cross_run_semantic_parity() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = replace(definition(), rationale_schema_version="detector-evidence-v1")
    at = START + timedelta(minutes=1)
    with engine.begin() as connection:
        first = create(connection, selected, run(connection, selected))
        equivalent = create(connection, selected, run(connection, selected))
        changed = []
        for occurrence in (first, equivalent):
            changed.append(
                advance_pattern_instance(
                    connection,
                    occurrence.instance_id,
                    selected,
                    expected_revision=0,
                    bar=bar(at),
                    steps=(
                        LifecycleStep(
                            "idle",
                            "candidate",
                            "start",
                            START,
                            at,
                            _typed_rationale("start"),
                        ),
                    ),
                    context={},
                )
            )
        first_event = changed[0].detector_events[0]
        equivalent_event = changed[1].detector_events[0]
        assert first_event.event_id != equivalent_event.event_id
        assert first_event.event_semantic_key == equivalent_event.event_semantic_key
        assert first_event.instance_semantic_key == equivalent_event.instance_semantic_key
        assert first_event.run_id != equivalent_event.run_id
        assert first_event.event_kind == "CANDIDATE"
        assert first_event.source_market_event_refs == (EVENT,)
        assert first_event.rationale is not None
        assert first_event.rationale["items"][0]["features"]["count"]["value"] == 5
        with pytest.raises(TypeError):
            first_event.rationale["items"][0]["features"]["count"]["value"] = 6
        assert load_pattern_instance(connection, first.instance_id, selected) == changed[0]
    engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema="unknown"),
        lambda value: value.update(condition="confirm"),
        lambda value: value["items"][0].update(status="UNKNOWN"),
        lambda value: value["items"][0]["value"].update(value="NaN"),
        lambda value: value["items"][0]["features"]["count"].update(value=True),
        lambda value: value["items"][0].update(source_refs=["not-a-ref"]),
        lambda value: value["items"][0].update(condition_id="not-declared"),
    ],
)
def test_strict_event_rationale_rejects_invalid_evidence(mutation: object) -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = replace(definition(), rationale_schema_version="detector-evidence-v1")
    at = START + timedelta(minutes=1)
    rationale = _typed_rationale("start")
    mutation(rationale)
    with engine.begin() as connection:
        occurrence = create(connection, selected, run(connection, selected))
        with pytest.raises(PatternInstanceError, match="invalid detector event rationale"):
            advance_pattern_instance(
                connection,
                occurrence.instance_id,
                selected,
                expected_revision=0,
                bar=bar(at),
                steps=(LifecycleStep("idle", "candidate", "start", START, at, rationale),),
                context={},
            )
    engine.dispose()


def test_strict_event_same_bar_chain_has_distinct_ordered_events() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    selected = replace(definition(), rationale_schema_version="detector-evidence-v1")
    at = START + timedelta(minutes=1)
    with engine.begin() as connection:
        occurrence = create(connection, selected, run(connection, selected))
        changed = advance_pattern_instance(
            connection,
            occurrence.instance_id,
            selected,
            expected_revision=0,
            bar=bar(at),
            steps=(
                LifecycleStep("idle", "candidate", "start", START, at, _typed_rationale("start")),
                LifecycleStep(
                    "candidate", "active", "confirm", at, at, _typed_rationale("confirm")
                ),
            ),
            context={},
        )
        assert [event.within_bar_ordinal for event in changed.detector_events] == [0, 1]
        assert [event.event_kind for event in changed.detector_events] == ["CANDIDATE", "ACTIVE"]
        assert len({event.event_semantic_key for event in changed.detector_events}) == 2
    engine.dispose()


def test_event_id_migration_backfills_existing_transitions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "event-backfill.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "20260930_07")
    engine = create_engine(url)
    selected = definition()
    at = START + timedelta(minutes=1)
    instance_id = str(uuid4())
    with engine.begin() as connection:
        # Seed the old schema directly: current repository code writes the new
        # CodeVersion columns, which do not exist before revision 09.
        run_id = uuid4()
        config = DetectionAnalysisConfig(
            instrument_id="US30", calendar_id="demo-v1",
            patterns=(PatternSelection.from_definition(selected),),
        )
        definitions = {selected.identity: selected}
        resolved = resolve_detection_config(config, pattern_definitions=definitions)
        config_hash = detection_config_hash(resolved, pattern_definitions=definitions)
        connection.execute(text(
            "INSERT INTO run_snapshots (run_id, run_kind, dataset_revision_id, "
            "calendar_version, build_id, config_schema_version, detection_hash_algorithm, "
            "detection_hash_version, detection_canonicalization_version, "
            "detection_config_hash, detection_config_json) "
            "VALUES (:run_id, 'replay', 'dataset-1', 'demo-v1', 'test-build', "
            ":schema_version, :algorithm, :hash_version, :canonical_version, "
            ":config_hash, :config_json)"
        ), {
            "run_id": str(run_id), "schema_version": resolved.schema_version,
            "algorithm": HASH_ALGORITHM, "hash_version": DETECTION_HASH_VERSION,
            "canonical_version": CANONICALIZATION_VERSION,
            "config_hash": config_hash, "config_json": resolved.canonical_json(),
        })
        key = instance_semantic_key(
            dataset_revision_id="dataset-1", detection_config_hash=config_hash,
            instrument_id="US30", timeframe=Timeframe.M1.value, definition=selected,
            occurrence_event_time=START, occurrence_detection_time=START,
            occurrence_ordinal=0,
        )
        connection.execute(pattern_instances.insert().values(
            instance_id=instance_id, run_id=str(run_id), instance_semantic_key=key,
            dataset_revision_id="dataset-1", instrument_id="US30", timeframe="1m",
            detection_config_hash=config_hash,
            calendar_version="demo-v1", build_id="test-build", binding_fingerprint=BINDING,
            pattern_id=selected.pattern_id, pattern_version=selected.pattern_version,
            definition_fingerprint=selected.semantic_fingerprint(),
            occurrence_event_time=START, occurrence_detection_time=START,
            occurrence_ordinal=0, state="candidate", revision=1, last_bar_time=at,
            context_json=encode_context(selected, {}), created_at=START,
        ))
        step = LifecycleStep("idle", "candidate", "start", START, at)
        connection.execute(text(
            "INSERT INTO pattern_instance_transitions "
            "(instance_id, sequence, event_semantic_ref, bar_time, event_time, "
            "detection_time, from_state, to_state, trigger_id) "
            "VALUES (:instance_id, 0, :ref, :bar_time, :event_time, :detection_time, "
            "'idle', 'candidate', 'start')"
        ), {
            "instance_id": instance_id, "ref": _event_ref(key, 0, step),
            "bar_time": at, "event_time": START, "detection_time": at,
        })
    engine.dispose()
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        loaded = load_pattern_instance(connection, instance_id, selected)
        assert loaded is not None
        assert UUID(loaded.detector_events[0].event_id)
        assert loaded.detector_events[0].event_semantic_key == _event_ref(key, 0, step)
        with pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(
                "UPDATE pattern_instance_transitions SET rationale_json = '{}' "
                "WHERE instance_id = :instance_id"
            ), {"instance_id": instance_id})
        with pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(
                "DELETE FROM pattern_instance_transitions WHERE instance_id = :instance_id"
            ), {"instance_id": instance_id})
    engine.dispose()
    command.downgrade(Config("alembic.ini"), "20260930_07")
