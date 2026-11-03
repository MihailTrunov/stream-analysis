"""Immutable orchestration lineage, separate from legacy per-stream run snapshots."""

from sqlalchemy import Column, String, Table, Text

from .runs import metadata

analysis_session_revisions = Table(
    "analysis_session_revisions",
    metadata,
    Column("session_id", String(36), primary_key=True),
    Column("manifest_json", Text, nullable=False),
    Column("manifest_hash", String(64), nullable=False),
)
