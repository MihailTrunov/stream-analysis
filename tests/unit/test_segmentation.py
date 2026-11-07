"""X-only instrument/session partitions, frozen plans and durable evaluation reads."""

from collections import Counter
from dataclasses import replace
from datetime import time

import pytest
import test_evaluation_scheduler as queue_tests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from test_detection_contexts import drive
from test_evaluation_runs import clean_build, request
from test_outcomes import CALENDAR

from market_analysis.application.evaluation_runs import (
    create_evaluation_run,
    evaluation_events,
    read_evaluation_run,
)
from market_analysis.application.evaluation_scheduler import enqueue_evaluation, run_scheduler_once
from market_analysis.application.forward_return import default_forward_return_selections
from market_analysis.application.segment_registry import SEGMENT_DEFINITIONS
from market_analysis.application.segmentation import (
    assign_segment,
    evaluation_segments,
    pinned_segments,
    segment_memberships,
)
from market_analysis.config import (
    ConfigParameter,
    SegmentSelection,
    detection_config_hash,
)
from market_analysis.domain import SessionWindow
from market_analysis.domain.evaluation import EvaluationInput
from market_analysis.domain.segments import SegmentDefinition, SegmentValue
from market_analysis.persistence.evaluation_jobs import JobStatus
from market_analysis.persistence.evaluations import evaluation_inputs
from market_analysis.persistence.runs import metadata
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage
DEFINITIONS = tuple(SEGMENT_DEFINITIONS.values())


@pytest.fixture
def snapshot():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _, _, _, snapshots = drive(connection)
            return snapshots[-1]
    finally:
        engine.dispose()


def captured(snapshot, instrument, session):
    values = dict(snapshot.values)
    components = dict(values["components"])
    components["session"] = {"session_name": session}
    values["components"] = components
    return replace(snapshot, instrument_id=instrument, values=values)


@pytest.mark.parametrize(
    "definition,expected",
    [("BY_INSTRUMENT", [2, 2]), ("BY_SESSION", [1, 1, 2]), ("BY_INSTRUMENT_SESSION", [1, 1, 1, 1])],
)
def test_hand_calculated_partitions_and_no_duplicate_members(snapshot, definition, expected):
    contract = SEGMENT_DEFINITIONS[(definition, "1")]
    rows = [
        captured(snapshot, "US30", "AM"),
        captured(snapshot, "US30", "PM"),
        captured(snapshot, "DAX", "AM"),
        captured(snapshot, "DAX", None),
    ]
    buckets = [assign_segment(contract, item.instrument_id, item) for item in rows]
    counts = Counter(item.semantic_key for item in buckets)
    assert sorted(counts.values()) == expected
    assert sum(counts.values()) == len(rows)
    assert all(item.definition_fingerprint == contract.fingerprint() for item in buckets)
    assert contract.overlap_policy == "NON_OVERLAPPING"


def test_captured_values_ignore_outcomes_future_state_and_display_timezone(snapshot):
    contract = SEGMENT_DEFINITIONS[("BY_SESSION", "1")]
    original = captured(snapshot, "US30", "AM")
    baseline = assign_segment(contract, "US30", original)
    values = dict(original.values)
    # These are unrelated captured facts; segmentation reads only the selected dimensions.
    detector = dict(values["detector"])
    context = dict(detector["context"])
    context["future_return"] = "999"
    context["display_timezone"] = "America/New_York"
    detector["context"] = context
    values["detector"] = detector
    changed = replace(original, values=values)
    assert assign_segment(contract, "US30", changed) == baseline
    assert baseline.values[0].value == "AM"


def test_actual_session_boundary_uses_source_frame_not_later_calendar():
    calendar = replace(
        CALENDAR,
        windows=(
            SessionWindow("BEFORE", time(0), time(12, 15)),
            SessionWindow("AFTER", time(12, 15), time(23, 59)),
        ),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _, _, _, snapshots = drive(connection, calendar=calendar)
            before = next(item for item in snapshots if item.detection_time.minute == 14)
            after = next(item for item in snapshots if item.detection_time.minute == 15)
            contract = SEGMENT_DEFINITIONS[("BY_SESSION", "1")]
            assert assign_segment(contract, "US30", before).values[0].value == "BEFORE"
            assert assign_segment(contract, "US30", after).values[0].value == "AFTER"
            assert before.available_at == after.detection_time
    finally:
        engine.dispose()


def test_missing_session_is_explicit_and_cannot_collide_with_a_named_bucket(snapshot):
    contract = SEGMENT_DEFINITIONS[("BY_SESSION", "1")]
    unknown = assign_segment(contract, "US30", snapshot)
    named = assign_segment(contract, "US30", captured(snapshot, "US30", "UNAVAILABLE"))
    assert unknown.values[0].availability == "UNAVAILABLE" and unknown.values[0].value is None
    assert (
        named.values[0].availability == "AVAILABLE" and unknown.semantic_key != named.semantic_key
    )
    with pytest.raises(ValueError, match="instrument"):
        assign_segment(contract, "DAX", snapshot)


@pytest.mark.parametrize(
    "changes",
    [
        {"fields": ("outcomes.forward_return",)},
        {"fields": ()},
        {"fields": ("instrument_id", "instrument_id")},
        {"overlap_policy": "OVERLAPPING"},
    ],
)
def test_unapproved_fields_empty_duplicate_and_overlapping_rules_are_rejected(changes):
    values = DEFINITIONS[0].model_dump(mode="python")
    values.update(changes)
    with pytest.raises(ValueError):
        SegmentDefinition.model_validate(values)


def test_availability_and_definition_roundtrips():
    for definition in DEFINITIONS:
        assert SegmentDefinition.model_validate_json(definition.model_dump_json()) == definition
    with pytest.raises(ValueError, match="availability"):
        SegmentValue(field="session_name", value=None, availability="AVAILABLE")


def selected_request(*, outcomes=(), segments=True):
    chosen = request(multihour=True, outcomes=outcomes)
    stream = chosen.streams[0]
    plan = stream.plan.model_copy(
        update={
            "segments": tuple(
                SegmentSelection(segment_id=item.segment_id, segment_version="1")
                for item in DEFINITIONS
            )
            if segments
            else ()
        }
    )
    return chosen.model_copy(update={"streams": (stream.model_copy(update={"plan": plan}),)})


def add(engine, store, chosen):
    with atomic_transaction(engine) as connection:
        record = create_evaluation_run(
            connection,
            chosen,
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        enqueue_evaluation(connection, str(record.inputs.evaluation_id), queue_tests.now())
    return record


def finish(engine, store):
    queue_tests.resume(engine)
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )


def run_segments(engine, store):
    record = add(engine, store, selected_request(outcomes=default_forward_return_selections()))
    with engine.connect() as connection:
        partial = evaluation_segments(connection, str(record.inputs.evaluation_id))
        assert not partial["complete"] and partial["groups"] == []
        assert len(partial["definitions"]) == 3 and all(
            item["eligible_event_count"] == 0 for item in partial["definitions"]
        )
    finish(engine, store)
    with engine.connect() as connection:
        final = evaluation_segments(connection, str(record.inputs.evaluation_id))
        rows = evaluation_events(connection, str(record.inputs.evaluation_id))["events"]
        assert final["complete"] and rows
        assert final["eligible_event_count"] == len(rows)
        for definition in DEFINITIONS:
            groups = [
                item for item in final["groups"] if item["segment_id"] == definition.segment_id
            ]
            assert len(groups) == 1 and groups[0]["event_count"] == len(rows)
            assert groups[0]["definition_fingerprint"] == definition.fingerprint()
        assert all(group["event_count"] > 0 for group in final["groups"])
        memberships = list(segment_memberships(connection, record))
        assert len(memberships) == 3 * len(rows)
        assert all(
            item.bucket.values[-1].value == "provider"
            for item in memberships
            if item.bucket.segment_id == "BY_SESSION"
        )
        assert all(
            item.evaluation_plan_hash == record.inputs.streams[0].evaluation_plan_hash
            for item in memberships
        )
        stored = read_evaluation_run(connection, str(record.inputs.evaluation_id))
        assert stored.inputs == record.inputs
        assert EvaluationInput.model_validate_json(stored.inputs.canonical_json()) == stored.inputs
    other = add(engine, store, selected_request())
    finish(engine, store)
    with engine.connect() as connection:
        other_rows = evaluation_events(connection, str(other.inputs.evaluation_id))["events"]
        other_memberships = list(segment_memberships(connection, other))
        assert [item["event_semantic_ref"] for item in rows] == [
            item["event_semantic_ref"] for item in other_rows
        ]
        assert [(item.event_semantic_ref, item.bucket.semantic_key) for item in memberships] == [
            (item.event_semantic_ref, item.bucket.semantic_key) for item in other_memberships
        ]
        assert record.inputs.event_set_id == other.inputs.event_set_id
        assert (
            record.inputs.streams[0].detection_config_hash
            == other.inputs.streams[0].detection_config_hash
        )
        assert (
            record.inputs.streams[0].evaluation_plan_hash
            != other.inputs.streams[0].evaluation_plan_hash
        )
    return record, final


def test_real_queued_segments_and_outcome_plan_invariance(storage):
    run_segments(*storage)


def test_unsegmented_legacy_manifest_and_event_set_identity_preserved(storage):
    engine, store = storage
    record = add(engine, store, selected_request(segments=False))
    assert all(
        "segment_definitions" not in stream
        for stream in record.inputs.model_dump(mode="json")["streams"]
    )
    assert (
        EvaluationInput.model_validate_json(record.inputs.canonical_json()).canonical_json()
        == record.inputs.canonical_json()
    )
    segmented = add(engine, store, selected_request())
    assert record.inputs.event_set_id == segmented.inputs.event_set_id
    assert (
        record.inputs.streams[0].detection_config_hash
        == segmented.inputs.streams[0].detection_config_hash
    )
    assert (
        record.inputs.streams[0].evaluation_plan_hash
        != segmented.inputs.streams[0].evaluation_plan_hash
    )
    with engine.connect() as connection:
        assert (
            evaluation_segments(connection, str(record.inputs.evaluation_id))["definitions"] == []
        )
        assert evaluation_segments(connection, str(record.inputs.evaluation_id))["groups"] == []


@pytest.mark.parametrize(
    "segment",
    [
        SegmentSelection(segment_id="unknown", segment_version="1"),
        SegmentSelection(
            segment_id="BY_SESSION",
            segment_version="1",
            parameters=(ConfigParameter(name="outcomes.return", value="x"),),
        ),
    ],
)
def test_invalid_segment_preflight_creates_no_run(storage, segment):
    engine, store = storage
    chosen = selected_request()
    stream = chosen.streams[0]
    chosen = chosen.model_copy(
        update={
            "streams": (
                stream.model_copy(
                    update={"plan": stream.plan.model_copy(update={"segments": (segment,)})}
                ),
            )
        }
    )
    with engine.begin() as connection:
        before = connection.scalar(select(func.count()).select_from(evaluation_inputs))
        with pytest.raises(ValueError):
            create_evaluation_run(
                connection,
                chosen,
                store,
                build_id="fixture",
                created_at=queue_tests.now(),
                revision_provider=clean_build,
            )
        assert connection.scalar(select(func.count()).select_from(evaluation_inputs)) == before


def test_group_api_and_definition_catalog(storage):
    from market_analysis.api.app import app

    engine, store = storage
    record = add(engine, store, selected_request())
    finish(engine, store)
    with TestClient(app) as client:
        response = client.get(f"/evaluation-runs/{record.inputs.evaluation_id}/segments")
        assert response.status_code == 200 and response.json()["complete"]
        assert len(response.json()["groups"]) == 3
        definitions = client.get("/segment-definitions")
        assert definitions.status_code == 200 and {
            item["segment_id"] for item in definitions.json()["definitions"]
        } == {item.segment_id for item in DEFINITIONS}
        assert (
            client.get("/evaluation-runs/00000000-0000-0000-0000-000000000000/segments").status_code
            == 404
        )


def test_descriptor_changes_rejected_for_execution_but_archived_read_uses_pinned_contract(storage):
    engine, store = storage
    record = add(engine, store, selected_request())
    stream = record.inputs.streams[0]
    definition = DEFINITIONS[0].model_copy(update={"fields": ("session_name",)})
    changed = stream.model_copy(
        update={
            "segment_definitions": (definition.model_dump_json(), *stream.segment_definitions[1:])
        }
    )
    tampered = replace(record, inputs=record.inputs.model_copy(update={"streams": (changed,)}))
    with pytest.raises(ValueError, match="pinned contract"):
        pinned_segments(tampered, for_execution=True)
    assert pinned_segments(tampered)[str(stream.run_id)][0].fields == ("session_name",)


def test_postgres_queued_segments_and_group_counts(isolated_postgres_url, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from market_analysis.demo.replay_seed import seed_replay_datasets
    from market_analysis.persistence.dataset_store import DatasetStore

    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", isolated_postgres_url)
    monkeypatch.setenv("STREAM_ANALYSIS_BUILD_ID", "fixture")
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("STREAM_ANALYSIS_GIT_DIRTY", "false")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(isolated_postgres_url)
    store = DatasetStore(tmp_path / "data")
    try:
        with atomic_transaction(engine) as connection:
            seed_replay_datasets(connection, store)
        run_segments(engine, store)
    finally:
        engine.dispose()


def test_selected_stream_scope_has_per_definition_denominators(storage):
    from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS
    from market_analysis.domain import Timeframe

    engine, store = storage
    chosen = selected_request()
    base = chosen.streams[0]
    config = base.config.model_copy(update={"timeframe": Timeframe.M5})
    plan = base.plan.model_copy(
        update={
            "detection_config_hash": detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            ),
            "segments": (SegmentSelection(segment_id="BY_SESSION", segment_version="1"),),
        }
    )
    chosen = chosen.model_copy(
        update={"streams": (base, base.model_copy(update={"config": config, "plan": plan}))}
    )
    record = add(engine, store, chosen)
    finish(engine, store)
    with engine.connect() as connection:
        groups = evaluation_segments(connection, str(record.inputs.evaluation_id))
        membership = list(segment_memberships(connection, record))
        by_definition = Counter(item.bucket.segment_id for item in membership)
        by_run = Counter(
            item.source_run_id for item in membership if item.bucket.segment_id == "BY_SESSION"
        )
        assert len(by_run) == 2 and all(count > 0 for count in by_run.values())
        assert by_definition["BY_SESSION"] > by_definition["BY_INSTRUMENT"]
        for descriptor in groups["definitions"]:
            segment_id = descriptor["definition"]["segment_id"]
            assert descriptor["eligible_event_count"] == by_definition[segment_id]


def test_partial_groups_and_explicit_watermark_do_not_include_later_events(storage):
    from market_analysis.persistence.evaluations import evaluation_event_sets

    engine, store = storage
    record = add(engine, store, selected_request())
    queue_tests.resume(engine)
    captured_prefix = []

    def committed():
        if captured_prefix:
            return
        with engine.connect() as connection:
            view = evaluation_segments(connection, str(record.inputs.evaluation_id))
            if view["eligible_event_count"]:
                assert not view["complete"]
                captured_prefix.append(view)

    assert (
        run_scheduler_once(
            engine, store, chunk_size=20, revision_provider=clean_build, on_commit=committed
        ).status
        is JobStatus.COMPLETED
    )
    assert captured_prefix
    prefix = captured_prefix[0]
    with engine.connect() as connection:
        old = list(
            segment_memberships(connection, record, through=prefix["through_emission_order"])
        )
        assert len(old) == prefix["eligible_event_count"] * 3
        total = connection.scalar(
            select(func.count())
            .select_from(evaluation_event_sets)
            .where(
                evaluation_event_sets.c.evaluation_id == str(record.inputs.evaluation_id),
                evaluation_event_sets.c.eligible == 1,
            )
        )
        assert total > prefix["eligible_event_count"]
        assert all(item.emission_order <= prefix["through_emission_order"] for item in old)


def test_selection_order_does_not_change_pinned_input_semantics(storage):
    engine, store = storage
    chosen = selected_request()
    first = add(engine, store, chosen)
    stream = chosen.streams[0]
    reversed_plan = stream.plan.model_copy(
        update={"segments": tuple(reversed(stream.plan.segments))}
    )
    reordered = chosen.model_copy(
        update={"streams": (stream.model_copy(update={"plan": reversed_plan}),)}
    )
    second = add(engine, store, reordered)
    assert (
        first.inputs.streams[0].evaluation_plan_hash
        == second.inputs.streams[0].evaluation_plan_hash
    )
    assert (
        first.inputs.streams[0].segment_definitions == second.inputs.streams[0].segment_definitions
    )
    assert first.inputs.semantic_key == second.inputs.semantic_key
