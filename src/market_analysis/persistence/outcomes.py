"""Append-only typed outcome records, separate from detector truth and X."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy import Column, Connection, ForeignKey, String, Table, Text, UniqueConstraint, select

from market_analysis.domain.outcomes import OutcomeDefinition, OutcomeError, OutcomeObservation
from market_analysis.persistence.runs import metadata

outcome_observations = Table(
    "outcome_observations",
    metadata,
    Column("observation_id", String(36), primary_key=True),
    Column(
        "event_id", String(36), ForeignKey("pattern_instance_transitions.event_id"), nullable=False
    ),
    Column("run_id", String(36), ForeignKey("run_snapshots.run_id"), nullable=False),
    Column(
        "context_snapshot_id",
        String(36),
        ForeignKey("detection_context_snapshots.snapshot_id"),
        nullable=False,
    ),
    Column("semantic_key", String(64), nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    UniqueConstraint("event_id", "semantic_key", name="uq_outcome_event_semantic"),
)


def save_outcome_observation(
    connection: Connection,
    observation: OutcomeObservation,
    definition: OutcomeDefinition,
) -> None:
    if definition.fingerprint() != observation.lineage.definition_fingerprint or (
        definition.resolve(observation.configuration) != observation.configuration
    ):
        raise OutcomeError("outcome definition/configuration mismatch")
    payload = observation.canonical_json()
    connection.execute(
        outcome_observations.insert().values(
            observation_id=observation.observation_id,
            event_id=observation.lineage.source_event_id,
            run_id=observation.lineage.source_run_id,
            context_snapshot_id=observation.lineage.context_snapshot_id,
            semantic_key=observation.semantic_key,
            definition_json=definition.model_dump_json(),
            payload_json=payload,
            content_hash=sha256(payload.encode()).hexdigest(),
        )
    )


def load_outcome_observation(
    connection: Connection,
    observation_id: str,
) -> OutcomeObservation | None:
    row = (
        connection.execute(
            select(outcome_observations).where(
                outcome_observations.c.observation_id == str(UUID(observation_id)),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    try:
        observation = OutcomeObservation.model_validate_json(row["payload_json"])
        definition = OutcomeDefinition.model_validate_json(row["definition_json"])
        if (
            sha256(observation.canonical_json().encode()).hexdigest() != row["content_hash"]
            or definition.fingerprint() != observation.lineage.definition_fingerprint
            or observation.observation_id != row["observation_id"]
            or observation.semantic_key != row["semantic_key"]
            or observation.lineage.source_event_id != row["event_id"]
            or observation.lineage.source_run_id != row["run_id"]
            or observation.lineage.context_snapshot_id != row["context_snapshot_id"]
        ):
            raise OutcomeError("stored outcome checksum or lineage mismatch")
        return observation
    except (ValueError, TypeError, KeyError) as exc:
        raise OutcomeError("invalid stored outcome observation") from exc
