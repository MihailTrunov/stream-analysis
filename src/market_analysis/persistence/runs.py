from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
)

from market_analysis.application.logging import research_logger
from market_analysis.config import (
    DetectionAnalysisConfig,
    EvaluationPlan,
    detection_config_hash,
    evaluation_plan_hash,
    resolve_detection_config,
    resolve_evaluation_plan,
)
from market_analysis.config.hashing import (
    DETECTION_HASH_VERSION,
    EVALUATION_HASH_VERSION,
    HASH_ALGORITHM,
)
from market_analysis.patterns import ParameterSpec, PatternDefinition

metadata = MetaData()
run_snapshots = Table(
    "run_snapshots",
    metadata,
    Column("run_id", String(36), primary_key=True),
    Column("run_kind", String(10), nullable=False),
    Column("dataset_revision_id", String(200), nullable=False),
    Column("preset_id", String(200)),
    Column("preset_revision", Integer),
    Column("calendar_version", String(200), nullable=False),
    Column("build_id", String(200), nullable=False),
    Column("config_schema_version", String(100), nullable=False),
    Column("detection_hash_algorithm", String(20), nullable=False),
    Column("detection_hash_version", String(100), nullable=False),
    Column("detection_config_hash", String(64), nullable=False),
    Column("detection_config_json", Text, nullable=False),
    Column("evaluation_hash_algorithm", String(20)),
    Column("evaluation_hash_version", String(100)),
    Column("evaluation_plan_hash", String(64)),
    Column("evaluation_plan_json", Text),
    CheckConstraint("run_kind IN ('replay', 'evaluation')", name="ck_run_kind"),
    CheckConstraint(
        "(run_kind = 'replay' AND evaluation_plan_json IS NULL AND evaluation_plan_hash IS NULL "
        "AND evaluation_hash_algorithm IS NULL AND evaluation_hash_version IS NULL) "
        "OR (run_kind = 'evaluation' AND evaluation_plan_json IS NOT NULL "
        "AND evaluation_plan_hash IS NOT NULL AND evaluation_hash_algorithm IS NOT NULL "
        "AND evaluation_hash_version IS NOT NULL)",
        name="ck_run_plan",
    ),
    CheckConstraint(
        "(preset_id IS NULL AND preset_revision IS NULL) OR "
        "(preset_id IS NOT NULL AND preset_revision > 0)",
        name="ck_run_preset",
    ),
)


@dataclass(frozen=True, slots=True)
class RunSnapshotRecord:
    run_id: str
    run_kind: str
    dataset_revision_id: str
    preset_id: str | None
    preset_revision: int | None
    calendar_version: str
    build_id: str
    config_schema_version: str
    detection_hash_algorithm: str
    detection_hash_version: str
    detection_config_hash: str
    detection_config_json: str
    evaluation_hash_algorithm: str | None
    evaluation_hash_version: str | None
    evaluation_plan_hash: str | None
    evaluation_plan_json: str | None


def create_run_snapshot(
    connection: Connection,
    *,
    run_id: UUID,
    dataset_revision_id: str,
    calendar_version: str,
    build_id: str,
    detection_config: DetectionAnalysisConfig,
    evaluation_plan: EvaluationPlan | None = None,
    preset_id: str | None = None,
    preset_revision: int | None = None,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    outcome_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    segment_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
) -> RunSnapshotRecord:
    for name, value in (
        ("dataset_revision_id", dataset_revision_id),
        ("calendar_version", calendar_version),
        ("build_id", build_id),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be non-empty")
    if (preset_id is None) != (preset_revision is None):
        raise ValueError("preset ID and revision must be supplied together")
    if preset_revision is not None and preset_revision < 1:
        raise ValueError("preset revision must be positive")
    detection_config = resolve_detection_config(
        detection_config,
        component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    if evaluation_plan is not None:
        evaluation_plan = resolve_evaluation_plan(
            evaluation_plan,
            outcome_parameters=outcome_parameters,
            segment_parameters=segment_parameters,
        )
    logger = research_logger(
        run_id=str(run_id),
        dataset_id=dataset_revision_id,
        instrument=detection_config.instrument_id,
        component="run-snapshot",
        build_id=build_id,
    )
    config_hash = detection_config_hash(
        detection_config,
        component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    if evaluation_plan is not None and evaluation_plan.detection_config_hash != config_hash:
        logger.error("evaluation plan references a different detection config")
        raise ValueError("evaluation plan references a different detection config")
    record = RunSnapshotRecord(
        run_id=str(run_id),
        run_kind="evaluation" if evaluation_plan is not None else "replay",
        dataset_revision_id=dataset_revision_id,
        preset_id=preset_id,
        preset_revision=preset_revision,
        calendar_version=calendar_version,
        build_id=build_id,
        config_schema_version=detection_config.schema_version,
        detection_hash_algorithm=HASH_ALGORITHM,
        detection_hash_version=DETECTION_HASH_VERSION,
        detection_config_hash=config_hash,
        detection_config_json=detection_config.canonical_json(),
        evaluation_hash_algorithm=(HASH_ALGORITHM if evaluation_plan is not None else None),
        evaluation_hash_version=(EVALUATION_HASH_VERSION if evaluation_plan is not None else None),
        evaluation_plan_hash=(
            evaluation_plan_hash(
                evaluation_plan,
                outcome_parameters=outcome_parameters,
                segment_parameters=segment_parameters,
            ) if evaluation_plan is not None else None
        ),
        evaluation_plan_json=(
            evaluation_plan.canonical_json() if evaluation_plan is not None else None
        ),
    )
    connection.execute(run_snapshots.insert().values(**asdict(record)))
    logger.info("run snapshot inserted", extra={"run_kind": record.run_kind})
    return record


def load_run_snapshot(connection: Connection, run_id: UUID) -> RunSnapshotRecord | None:
    row = connection.execute(
        select(run_snapshots).where(run_snapshots.c.run_id == str(run_id))
    ).mappings().one_or_none()
    return RunSnapshotRecord(**row) if row is not None else None
