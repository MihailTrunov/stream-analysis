"""Canonical structural references, ordering and real downstream execution."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
import test_evaluation_scheduler as queue_tests
from sqlalchemy import create_engine
from test_evaluation_runs import clean_build, request
from test_forward_return import source
from test_market_state import config as full_config
from test_outcomes import setup_source

from market_analysis.application.evaluation_runs import create_evaluation_run, evaluation_events
from market_analysis.application.evaluation_scheduler import enqueue_evaluation, run_scheduler_once
from market_analysis.application.outcome_evaluation import evaluate_outcome
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS
from market_analysis.application.structural_event_inventory import (
    SUPPORTED,
    canonical_reference,
    structural_event_inventory,
)
from market_analysis.application.structural_outcomes import (
    ALGORITHMS,
    DEFINITIONS,
    FAMILIES,
    TYPES,
    structural_outcome,
)
from market_analysis.config import ConfigParameter, OutcomeSelection, detection_config_hash
from market_analysis.detection.records import freeze_mapping
from market_analysis.domain.outcomes import (
    CanonicalMarketEvent,
    OutcomeEventReference,
    ResultStatus,
)
from market_analysis.indicators.market_state import MarketStateAggregator, market_event_semantic_ref
from market_analysis.persistence.evaluation_jobs import JobStatus
from market_analysis.persistence.outcomes import load_outcome_observation
from market_analysis.persistence.runs import load_run_snapshot, metadata
from market_analysis.persistence.transactions import atomic_transaction

storage = queue_tests.storage
BY_KEY = {
    (family, reference): DEFINITIONS[i * 2 + int(reference)]
    for i, family in enumerate(FAMILIES)
    for reference in (False, True)
}


def selection(family="SWING", reference=False, **parameters):
    definition = BY_KEY[(family, reference)]
    return OutcomeSelection(
        outcome_id=definition.outcome_id,
        outcome_version="1",
        parameters=tuple(
            ConfigParameter(name=name, value=value) for name, value in parameters.items()
        ),
    )


def structural_source(family="SWING", reference=False, **parameters):
    value = source()
    context = dict(value.context)
    context["components"] = {
        "swing_point": {"component_id": "swing_point"},
        "swing_structure": {"definition_id": "SWING_STRUCTURE_V1"},
        "trend_leg": {"component_id": "trend_leg"},
        "trend_leg_qualification": {"component_id": "trend_leg_qualification"},
    }
    return replace(
        value,
        context=freeze_mapping(context),
        configuration=BY_KEY[(family, reference)].resolve(
            selection(family, reference, **parameters)
        ),
    )


def event(
    value, family="SWING", *, index=1, ordinal=0, category=None, event_type=None, instance=None
):
    kind = event_type or TYPES[family][0]
    return OutcomeEventReference(
        semantic_ref=f"{index + 1:064x}",
        kind="market",
        dataset_revision_id="fixture",
        instrument_id="US30",
        timeframe=value.bars[0].timeframe,
        detection_config_hash="a" * 64,
        event_time=value.window.reference_time - timedelta(minutes=10),
        detection_time=value.bars[index].timestamp,
        available_at=value.bars[index].timestamp + timedelta(minutes=1),
        within_bar_ordinal=ordinal,
        canonical_market=CanonicalMarketEvent(
            event_type=kind,
            source_instance_id=instance
            or {"SWING": "swing_point", "BREAK": "swing_structure", "TREND_LEG": "trend_leg"}[
                family
            ],
            category=category
            or {"SWING": "HIGH", "BREAK": "UP", "TREND_LEG": f"{kind}_UP"}[family],
            evidence_json='{"frozen":true}',
        ),
    )


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("reference", [False, True])
def test_first_canonical_event_and_exact_original_reference(family, reference):
    value = structural_source(family, reference)
    first = event(value, family)
    later = event(value, family, index=3)
    result = structural_outcome(
        replace(value, events=(later, first)), family=family, reference=reference
    )
    assert result.status is ResultStatus.AVAILABLE
    assert result.value == (first if reference else first.canonical_market.category)
    assert first.event_time < first.detection_time < first.available_at


@pytest.mark.parametrize("family", FAMILIES)
def test_complete_no_event_distinct_from_unconfigured_component(family):
    value = structural_source(family)
    assert structural_outcome(value, family=family).value == "NO_EVENT"
    changed = dict(value.context)
    changed["components"] = {}
    assert (
        structural_outcome(replace(value, context=freeze_mapping(changed)), family=family).status
        is ResultStatus.NOT_APPLICABLE
    )
    value = structural_source(family, True)
    result = structural_outcome(value, family=family, reference=True)
    assert (
        result.status is ResultStatus.NOT_APPLICABLE
        and result.value is None
        and "NO_EVENT" in result.reason
    )


def test_instance_transition_filter_and_untyped_or_detector_references_are_not_structural():
    value = structural_source("TREND_LEG", transition="TREND_LEG_ENDED")
    started = event(value, "TREND_LEG")
    ended = event(value, "TREND_LEG", index=2, event_type="TREND_LEG_ENDED")
    assert (
        structural_outcome(replace(value, events=(started, ended)), family="TREND_LEG").value
        == "TREND_LEG_ENDED_UP"
    )
    plain = started.model_copy(update={"canonical_market": None})
    detector = plain.model_copy(update={"kind": "detector"})
    assert (
        structural_outcome(replace(value, events=(plain, detector)), family="TREND_LEG").value
        == "NO_EVENT"
    )
    value = structural_source(source_instance_id="missing")
    assert structural_outcome(value, family="SWING").status is ResultStatus.NOT_APPLICABLE


def test_legacy_reference_bytes_unchanged_and_metadata_is_immutable():
    value = structural_source()
    reference = event(value)
    legacy = reference.model_copy(update={"canonical_market": None})
    payload = legacy.model_dump(mode="json")
    assert "canonical_market" not in payload
    assert OutcomeEventReference.model_validate(payload) == legacy
    assert OutcomeEventReference.model_validate_json(reference.model_dump_json()) == reference
    with pytest.raises(ValueError):
        reference.canonical_market.category = "LOW"


@pytest.mark.parametrize("family", FAMILIES)
def test_actual_shared_producer_and_sql_roundtrip(family):
    from market_analysis.config import DetectionAnalysisConfig

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    definition = BY_KEY[(family, True)]
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(connection, definition, future=60)
            run = load_run_snapshot(connection, UUID(arguments["event"].run_id))
            config = DetectionAnalysisConfig.from_canonical_json(run.detection_config_json)
            producer = MarketStateAggregator(
                config,
                run_id=str(run.run_id),
                dataset_revision_id="context-fixture",
                pattern_definitions=PATTERN_DEFINITIONS,
            )
            refs = []
            for bar in arguments["bar_sequence"].bars:
                frame = producer.update(bar)
                refs.extend(
                    canonical_reference(frame, item, bar.timestamp + timedelta(minutes=1))
                    for item in frame.market_events_this_bar
                    if item.event_type.value in SUPPORTED
                )
            observation = evaluate_outcome(
                connection,
                **arguments,
                events=tuple(refs),
                algorithm=ALGORITHMS[definition.identity],
            )
            candidates = [
                item
                for item in refs
                if item.canonical_market.event_type in TYPES[family]
                and snapshot.available_at < item.available_at <= observation.window.end
            ]
            assert (
                observation.status is ResultStatus.AVAILABLE
                if candidates
                else observation.status is ResultStatus.NOT_APPLICABLE
            )
            if candidates:
                assert observation.value == candidates[0]
                assert observation.value.semantic_ref in {item.semantic_ref for item in refs}
            assert load_outcome_observation(connection, observation.observation_id) == observation
            assert (
                evaluate_outcome(
                    connection,
                    **arguments,
                    events=tuple(refs),
                    algorithm=ALGORITHMS[definition.identity],
                )
                == observation
            )
    finally:
        engine.dispose()


def configured_request(outcomes):
    chosen = request(multihour=True, outcomes=outcomes)
    stream = chosen.streams[0]
    range_parameters = {
        item.name: item.value
        for component in stream.config.components
        if component.component_id == "range_state"
        for item in component.parameters
    }
    config = stream.config.model_copy(
        update={"components": full_config(range_parameters=range_parameters).components}
    )
    plan = stream.plan.model_copy(
        update={
            "detection_config_hash": detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            )
        }
    )
    return chosen.model_copy(
        update={"streams": (stream.model_copy(update={"config": config, "plan": plan}),)}
    )


def run_structural(engine, store):
    selections = tuple(
        selection(family, reference) for family in FAMILIES for reference in (False, True)
    )
    with atomic_transaction(engine) as connection:
        record = create_evaluation_run(
            connection,
            configured_request(selections),
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        enqueue_evaluation(connection, str(record.inputs.evaluation_id), queue_tests.now())
    queue_tests.resume(engine)
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )
    with engine.connect() as connection:
        rows = evaluation_events(connection, str(record.inputs.evaluation_id))["events"]
        assert rows and all(len(row["outcomes"]) == 6 for row in rows)
        references = [
            item
            for row in rows
            for item in row["outcomes"]
            if item["output_type"] == "EVENT_REFERENCE" and item["status"] == "AVAILABLE"
        ]
        assert references
        assert all(item["value"]["canonical_market"]["evidence_json"] for item in references)
        from market_analysis.application.replay_catalog import resolve_replay_calendar

        sequence = store.load_sequence(
            connection,
            record.inputs.dataset_revision_id,
            "US30",
            record.inputs.streams[0].timeframe,
        )
        calendar = resolve_replay_calendar(
            "US30", record.inputs.calendar_id, record.inputs.calendar_version
        )
    inventory = structural_event_inventory(record, sequence, calendar, check_stop=lambda: None)
    assert references[0]["value"]["semantic_ref"] in {
        item.semantic_ref for item in inventory[str(record.inputs.streams[0].run_id)]
    }
    # A different run UUID and evaluation plan cannot alter the detector truth.
    with atomic_transaction(engine) as connection:
        other = create_evaluation_run(
            connection,
            configured_request(()),
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        enqueue_evaluation(connection, str(other.inputs.evaluation_id), queue_tests.now())
    assert (
        run_scheduler_once(engine, store, revision_provider=clean_build).status
        is JobStatus.COMPLETED
    )
    with engine.connect() as connection:
        other_rows = evaluation_events(connection, str(other.inputs.evaluation_id))["events"]
        assert [row["event_semantic_ref"] for row in rows] == [
            row["event_semantic_ref"] for row in other_rows
        ]
    return record


def test_real_queued_structural_outcomes(storage):
    run_structural(*storage)


def test_postgres_real_queued_structural_outcomes(isolated_postgres_url, tmp_path, monkeypatch):
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
        run_structural(engine, store)
    finally:
        engine.dispose()


def test_requested_qualification_without_component_is_not_applicable():
    value = structural_source("TREND_LEG", transition="TREND_LEG_QUALIFIED")
    context = dict(value.context)
    context["components"] = {"trend_leg": {"component_id": "trend_leg"}}
    assert (
        structural_outcome(
            replace(value, context=freeze_mapping(context)), family="TREND_LEG"
        ).status
        is ResultStatus.NOT_APPLICABLE
    )


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("missing", [False, True])
def test_structural_window_insufficiency_precedes_event_result(family, missing):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    definition = BY_KEY[(family, False)]
    try:
        with engine.begin() as connection:
            arguments, _ = setup_source(
                connection,
                definition,
                future=60 if missing else 4,
                missing=(18,) if missing else (),
            )
            result = evaluate_outcome(
                connection, **arguments, algorithm=ALGORITHMS[definition.identity]
            )
            assert result.status is ResultStatus.INSUFFICIENT_DATA and result.value is None
    finally:
        engine.dispose()


def test_inventory_reads_future_but_stops_at_missing_open_minute_and_honors_cancellation(storage):
    from market_analysis.application.replay_catalog import resolve_replay_calendar
    from market_analysis.domain import Timeframe
    from market_analysis.domain.dataset_lineage import canonical_bar_checksum

    engine, store = storage
    chosen = configured_request((selection(),))
    chosen = chosen.model_copy(update={"selected_end": chosen.selected_end - timedelta(minutes=90)})
    with engine.begin() as connection:
        record = create_evaluation_run(
            connection,
            chosen,
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        sequence = store.load_sequence(
            connection, record.inputs.dataset_revision_id, "US30", Timeframe.M1
        )
    calendar = resolve_replay_calendar(
        "US30", record.inputs.calendar_id, record.inputs.calendar_version
    )
    full = structural_event_inventory(record, sequence, calendar, check_stop=lambda: None)
    key = str(record.inputs.streams[0].run_id)
    assert full[key] and max(item.available_at for item in full[key]) > record.inputs.selected_end
    missing_at = record.inputs.selected_end + timedelta(minutes=3)
    bars = tuple(bar for bar in sequence.bars if bar.timestamp != missing_at)
    assert len(bars) == len(sequence.bars) - 1
    gapped = replace(
        sequence,
        bars=bars,
        lineage=replace(
            sequence.lineage, bar_count=len(bars), canonical_checksum=canonical_bar_checksum(bars)
        ),
    )
    truncated = structural_event_inventory(record, gapped, calendar, check_stop=lambda: None)
    assert all(item.available_at <= missing_at for item in truncated[key])
    assert truncated[key] == tuple(item for item in full[key] if item.available_at <= missing_at)
    count = 0

    def stop():
        nonlocal count
        count += 1
        if count == 5:
            raise RuntimeError("fixture cancelled inventory")

    with pytest.raises(RuntimeError, match="cancelled inventory"):
        structural_event_inventory(record, sequence, calendar, check_stop=stop)


def test_catalog_exposes_structural_contracts_without_database(monkeypatch):
    from fastapi.testclient import TestClient

    from market_analysis.api.app import app

    monkeypatch.delenv("STREAM_ANALYSIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("STREAM_ANALYSIS_SEED_REPLAY_DEMO", "0")
    with TestClient(app) as client:
        response = client.get("/outcome-definitions")
        assert response.status_code == 200
        catalog = {item["outcome_id"]: item for item in response.json()["definitions"]}
        assert all(catalog[item.outcome_id] == item.model_dump(mode="json") for item in DEFINITIONS)


def test_real_canonical_identity_is_run_independent_and_rejects_non_emitted_objects():
    from uuid import uuid4

    from test_detector_runtime import chain_series

    config = full_config()
    producers = [
        MarketStateAggregator(config, run_id=str(uuid4()), dataset_revision_id="fixture")
        for _ in range(2)
    ]
    count = 0
    for bar in chain_series():
        frames = [producer.update(bar) for producer in producers]
        refs = [
            tuple(
                canonical_reference(frame, item, bar.timestamp + timedelta(minutes=1))
                for item in frame.market_events_this_bar
                if item.event_type.value in SUPPORTED
            )
            for frame in frames
        ]
        assert [item.semantic_ref for item in refs[0]] == [item.semantic_ref for item in refs[1]]
        count += len(refs[0])
        original = next(
            (
                item
                for item in frames[0].market_events_this_bar
                if item.event_type.value in SUPPORTED
            ),
            None,
        )
        if original is not None:
            with pytest.raises(ValueError, match="not emitted"):
                canonical_reference(
                    frames[0], replace(original), bar.timestamp + timedelta(minutes=1)
                )
    assert count > 0


def test_framework_preserves_same_close_phase_and_later_detector_ordinal():
    from market_analysis.application.outcome_evaluation import OutcomeResult

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    definition = BY_KEY[("SWING", True)]
    try:
        with engine.begin() as connection:
            arguments, snapshot = setup_source(connection, definition, future=5)
            source = arguments["event"]
            base = dict(
                dataset_revision_id=source.dataset_revision_id,
                instrument_id=source.instrument_id,
                timeframe=source.timeframe,
                detection_config_hash=source.detection_config_hash,
                event_time=source.detection_time,
                detection_time=source.detection_time,
                available_at=snapshot.available_at,
            )
            equal = OutcomeEventReference(
                **base,
                semantic_ref="1" * 64,
                kind="detector",
                within_bar_ordinal=source.within_bar_ordinal,
            )
            later = equal.model_copy(
                update={
                    "semantic_ref": "2" * 64,
                    "within_bar_ordinal": source.within_bar_ordinal + 1,
                }
            )
            earlier_market = equal.model_copy(
                update={"semantic_ref": "3" * 64, "kind": "market", "within_bar_ordinal": 999}
            )

            def algorithm(value):
                assert value.events == (later,)
                return OutcomeResult(ResultStatus.AVAILABLE, later)

            result = evaluate_outcome(
                connection, **arguments, events=(earlier_market, equal, later), algorithm=algorithm
            )
            assert result.value == later
            assert result.value.detection_time == source.detection_time
    finally:
        engine.dispose()


def test_m5_inventory_matches_full_detector_runtime_canonical_feed(storage):
    from market_analysis.application.replay_catalog import (
        detector_bindings,
        resolve_replay_calendar,
    )
    from market_analysis.config import DetectionAnalysisConfig
    from market_analysis.detection import DetectorRuntime
    from market_analysis.domain import Timeframe
    from market_analysis.domain.aggregation import CandleAggregator

    engine, store = storage
    chosen = configured_request((selection(),))
    chosen = chosen.model_copy(
        update={"selected_start": chosen.selected_start + timedelta(minutes=30)}
    )
    stream = chosen.streams[0]
    config = stream.config.model_copy(
        update={
            "timeframe": Timeframe.M5,
            "components": tuple(
                item
                for item in stream.config.components
                if item.component_id != "trend_leg_qualification"
            ),
        }
    )
    plan = stream.plan.model_copy(
        update={
            "detection_config_hash": detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            )
        }
    )
    chosen = chosen.model_copy(
        update={"streams": (stream.model_copy(update={"config": config, "plan": plan}),)}
    )
    with engine.begin() as connection:
        record = create_evaluation_run(
            connection,
            chosen,
            store,
            build_id="fixture",
            created_at=queue_tests.now(),
            revision_provider=clean_build,
        )
        sequence = store.load_sequence(
            connection, record.inputs.dataset_revision_id, "US30", Timeframe.M1
        )
    calendar = resolve_replay_calendar(
        "US30", record.inputs.calendar_id, record.inputs.calendar_version
    )
    expected = []
    stream = record.inputs.streams[0]
    pinned_config = DetectionAnalysisConfig.from_canonical_json(stream.config_json)
    runtime = DetectorRuntime(
        pinned_config,
        detector_bindings(pinned_config),
        run_id=str(stream.run_id),
        dataset_revision_id=record.inputs.dataset_revision_id,
        calendar=calendar,
        pinned_calendar_version=calendar.version,
    )
    aggregator = CandleAggregator(Timeframe.M5, calendar)
    for bar in sequence.bars:
        for candle in (*aggregator.advance(bar.timestamp), *aggregator.push(bar)):
            frame = runtime.process_bar(candle.bar).frame
            if candle.available_at >= record.inputs.selected_start:
                expected.extend(
                    market_event_semantic_ref(frame, item)
                    for item in frame.market_events_this_bar
                    if item.event_type.value in SUPPORTED
                )
    actual = structural_event_inventory(record, sequence, calendar, check_stop=lambda: None)[
        str(stream.run_id)
    ]
    assert actual and [item.semantic_ref for item in actual] == expected
    assert all(
        item.timeframe is Timeframe.M5 and item.available_at > item.detection_time
        for item in actual
    )
