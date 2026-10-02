from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DatabaseError

from alembic import command
from alembic.config import Config
from market_analysis.config import DetectionAnalysisConfig, PatternSelection
from market_analysis.domain import Bar, Timeframe
from market_analysis.domain.market_data import Instrument
from market_analysis.patterns import ConditionGroup, PatternDefinition, TransitionSpec
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_dataset_revision,
    register_instrument,
)
from market_analysis.persistence.pattern_instances import (
    LifecycleStep,
    advance_pattern_instance,
    create_pattern_instance,
    pattern_instance_transitions,
)
from market_analysis.persistence.runs import create_run_snapshot, metadata
from market_analysis.persistence.validation_annotations import (
    AnnotationError,
    create_event_annotation,
    create_instance_annotation,
    create_missed_pattern_annotation,
    export_annotation,
    load_annotation,
    revise_annotation,
    validation_annotations,
)

START = datetime(2026, 10, 2, 9, tzinfo=UTC)


def _definition() -> PatternDefinition:
    return PatternDefinition(
        "reversal",
        "1",
        "Reversal",
        "test",
        (),
        (),
        (),
        ("idle", "candidate"),
        (TransitionSpec("idle", "candidate", "start"),),
        (ConditionGroup("evidence", ("start",)),),
        (),
        (),
        ("start",),
    )


def _event(connection):  # type: ignore[no-untyped-def]
    definition = _definition()
    run_id = uuid4()
    create_run_snapshot(
        connection,
        run_id=run_id,
        dataset_revision_id="revision-1",
        calendar_version="demo-v1",
        build_id="build-1",
        detection_config=DetectionAnalysisConfig(
            instrument_id="US30",
            calendar_id="demo-v1",
            patterns=(PatternSelection.from_definition(definition),),
        ),
        pattern_definitions={definition.identity: definition},
    )
    instance = create_pattern_instance(
        connection,
        run_id=run_id,
        definition=definition,
        binding_fingerprint="a" * 64,
        occurrence_event_time=START,
        occurrence_detection_time=START,
        occurrence_ordinal=0,
        at=START,
    )
    at = START + timedelta(minutes=1)
    changed = advance_pattern_instance(
        connection,
        instance.instance_id,
        definition,
        expected_revision=0,
        bar=Bar("US30", Timeframe.M1, at, Decimal(1), Decimal(2), Decimal(1), Decimal(2)),
        steps=(LifecycleStep("idle", "candidate", "start", START, at),),
        context={},
    )
    return changed.detector_events[0]


def _dataset(connection):  # type: ignore[no-untyped-def]
    register_instrument(
        connection,
        Instrument(
            "US30",
            "US 30",
            "demo-v1",
            2,
            Decimal("0.01"),
            (),
        ),
    )
    register_dataset_revision(
        connection,
        DatasetRevision(
            dataset_revision_id="revision-1",
            dataset_id="research",
            source_id="fixture",
            provider="fixture",
            retrieved_at=START,
            created_at=START,
            normalization_version="1",
            calendar_version="demo-v1",
            manifest_format_version="1",
            manifest_ref="datasets/revision-1/manifest.json",
            memberships=(
                DatasetMembership(
                    "US30",
                    Timeframe.M1,
                    START,
                    START + timedelta(hours=1),
                    60,
                ),
            ),
        ),
    )


def test_event_and_instance_judgments_are_audited_without_event_mutation() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        event = _event(connection)
        before = connection.execute(select(pattern_instance_transitions)).mappings().all()
        first = create_event_annotation(
            connection,
            event_id=event.event_id,
            label="needs_review",
            note="inspect swing",
            reviewer_id="local",
            at=START + timedelta(minutes=2),
        )
        second = revise_annotation(
            connection,
            first.annotation_id,
            expected_revision=1,
            label="correct",
            note="confirmed",
            reviewer_id="local",
            at=START + timedelta(minutes=3),
        )
        assert second.event_id == event.event_id
        assert second.instance_id == event.instance_id
        assert second.run_id == event.run_id
        assert second.dataset_revision_id == event.dataset_revision_id
        assert second.pattern_version == event.pattern_version
        assert second.detection_config_hash == event.detection_config_hash
        assert [item.label for item in second.history] == ["needs_review", "correct"]
        assert load_annotation(connection, second.annotation_id) == second
        assert export_annotation(second)["history"][0]["note"] == "inspect swing"  # type: ignore[index]
        assert connection.execute(select(pattern_instance_transitions)).mappings().all() == before
        instance = create_instance_annotation(
            connection,
            instance_id=event.instance_id,
            label="incorrect",
            at=START,
        )
        assert instance.target_kind == "instance"
        assert instance.event_id is None
        assert instance.run_id == event.run_id
        with pytest.raises(AnnotationError, match="revision conflict"):
            revise_annotation(connection, first.annotation_id, expected_revision=1, label="correct")
        with pytest.raises(AnnotationError, match="unsupported"):
            create_event_annotation(connection, event_id=event.event_id, label="profitable")
        assert (
            connection.scalar(
                select(validation_annotations.c.annotation_id).where(
                    validation_annotations.c.annotation_id == first.annotation_id
                )
            )
            == first.annotation_id
        )
    engine.dispose()


def test_missing_targets_and_missed_pattern_interval_validation() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        with pytest.raises(AnnotationError, match="does not exist"):
            create_event_annotation(connection, event_id=str(uuid4()), label="correct")
        with pytest.raises(AnnotationError, match="does not exist"):
            create_instance_annotation(connection, instance_id=str(uuid4()), label="correct")
        with pytest.raises(AnnotationError, match="does not exist"):
            create_missed_pattern_annotation(
                connection,
                dataset_revision_id="missing",
                instrument_id="US30",
                timeframe=Timeframe.M1,
                interval_start=START,
                interval_end=START + timedelta(minutes=5),
                pattern_id="reversal",
                pattern_version="1",
            )
        _dataset(connection)
        missed = create_missed_pattern_annotation(
            connection,
            dataset_revision_id="revision-1",
            instrument_id="US30",
            timeframe=Timeframe.M1,
            interval_start=START + timedelta(minutes=5),
            interval_end=START + timedelta(minutes=10),
            pattern_id="reversal",
            pattern_version="1",
            note="candidate was absent",
            at=START,
        )
        assert missed.target_kind == "missed_pattern"
        assert missed.event_id is None and missed.instance_id is None and missed.run_id is None
        assert missed.current.label == "missed_pattern"
        assert (
            export_annotation(missed)["interval_start"]
            == (START + timedelta(minutes=5)).isoformat()
        )
        with pytest.raises(AnnotationError, match="outside dataset"):
            create_missed_pattern_annotation(
                connection,
                dataset_revision_id="revision-1",
                instrument_id="US30",
                timeframe=Timeframe.M1,
                interval_start=START - timedelta(minutes=1),
                interval_end=START + timedelta(minutes=5),
                pattern_id="reversal",
                pattern_version="1",
            )
        with pytest.raises(AnnotationError, match="must follow"):
            create_missed_pattern_annotation(
                connection,
                dataset_revision_id="revision-1",
                instrument_id="US30",
                timeframe=Timeframe.M1,
                interval_start=START,
                interval_end=START,
                pattern_id="reversal",
                pattern_version="1",
            )
    engine.dispose()


def test_migration_guards_audit_history_against_raw_mutation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    url = f"sqlite:///{tmp_path / 'annotations.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261002_10"
        event = _event(connection)
        annotation = create_event_annotation(
            connection,
            event_id=event.event_id,
            label="correct",
            at=START,
        )
        with pytest.raises(DatabaseError, match="immutable"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE validation_annotation_revisions SET label = 'incorrect' "
                        "WHERE annotation_id = :annotation_id"
                    ),
                    {"annotation_id": annotation.annotation_id},
                )
        with pytest.raises(DatabaseError, match="immutable"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM validation_annotations WHERE annotation_id = :annotation_id"),
                    {"annotation_id": annotation.annotation_id},
                )
        assert load_annotation(connection, annotation.annotation_id) == annotation
    engine.dispose()
