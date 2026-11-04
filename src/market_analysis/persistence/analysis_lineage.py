"""Resolve immutable source/analytical attribution independently of runtime caches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select

from market_analysis.config import DetectionAnalysisConfig

from .analysis_sessions import analysis_session_revisions
from .runs import RunSnapshotRecord, load_run_snapshot


@dataclass(frozen=True)
class AnalysisLineage:
    session_id: str
    manifest_hash: str
    manifest: dict[str, Any]
    snapshot: RunSnapshotRecord
    config: DetectionAnalysisConfig


def load_analysis_lineage(connection: Connection, run_id: str) -> AnalysisLineage | None:
    snapshot = load_run_snapshot(connection, UUID(run_id))
    if snapshot is None:
        return None
    rows = connection.execute(
        select(analysis_session_revisions).where(
            analysis_session_revisions.c.manifest_json.contains(run_id, autoescape=True)
        )
    ).mappings()
    for row in rows:
        source = str(row["manifest_json"])
        if sha256(source.encode()).hexdigest() != row["manifest_hash"]:
            raise ValueError("analysis manifest checksum mismatch")
        manifest = json.loads(source)
        if any(stream["run_id"] == run_id for stream in manifest["streams"]):
            return AnalysisLineage(
                str(row["session_id"]),
                str(row["manifest_hash"]),
                manifest,
                snapshot,
                DetectionAnalysisConfig.from_canonical_json(snapshot.detection_config_json),
            )
    return None
