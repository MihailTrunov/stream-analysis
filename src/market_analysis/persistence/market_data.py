"""Immutable PostgreSQL metadata for instruments and dataset revisions.

Bulk bars and their manifests are published by the dataset store (SCRUM-124).
These records describe identity, provenance, and coverage only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError

from market_analysis.domain.dataset_lineage import DatasetLineage
from market_analysis.domain.dataset_validation import ValidationStatus
from market_analysis.domain.market_data import Instrument, ProviderSymbolMapping, Timeframe
from market_analysis.persistence.runs import metadata


class MetadataConflictError(ValueError):
    """An existing identity has different immutable metadata."""


instruments = Table(
    "instruments",
    metadata,
    Column("instrument_id", String(200), primary_key=True),
    Column("display_name", String(200), nullable=False),
    Column("calendar_id", String(200), nullable=False),
    Column("price_precision", Integer, nullable=False),
    Column("point_size", Text, nullable=False),
    CheckConstraint("price_precision >= 0", name="ck_instrument_price_precision"),
    CheckConstraint("length(point_size) > 0", name="ck_instrument_point_size"),
)
provider_symbol_mappings = Table(
    "provider_symbol_mappings",
    metadata,
    Column("instrument_id", String(200), ForeignKey("instruments.instrument_id"), nullable=False),
    Column("provider", String(200), nullable=False),
    Column("provider_key", String(200), nullable=False),
    Column("environment", String(200)),
    Column("environment_key", String(200), nullable=False),
    Column("symbol", String(200), nullable=False),
    Column("symbol_key", String(200), nullable=False),
    UniqueConstraint(
        "instrument_id", "provider_key", "environment_key", name="uq_instrument_provider_env"
    ),
    UniqueConstraint(
        "provider_key", "environment_key", "symbol_key", name="uq_provider_symbol_identity"
    ),
    CheckConstraint("length(provider_key) > 0", name="ck_provider_key"),
    CheckConstraint("length(symbol_key) > 0", name="ck_symbol_key"),
)
Index("ix_provider_mapping_instrument", provider_symbol_mappings.c.instrument_id)

dataset_revisions = Table(
    "dataset_revisions",
    metadata,
    Column("dataset_revision_id", String(200), primary_key=True),
    Column("dataset_id", String(200), nullable=False),
    Column("source_id", String(200), nullable=False),
    Column("provider", String(200), nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("normalization_version", String(100), nullable=False),
    Column("calendar_version", String(200), nullable=False),
    Column("manifest_format_version", String(100), nullable=False),
    Column("manifest_ref", Text, nullable=False),
    CheckConstraint("length(dataset_id) > 0", name="ck_dataset_id"),
    CheckConstraint("length(source_id) > 0", name="ck_dataset_source"),
)
Index("ix_dataset_revisions_dataset", dataset_revisions.c.dataset_id)
dataset_memberships = Table(
    "dataset_memberships",
    metadata,
    Column(
        "dataset_revision_id",
        String(200),
        ForeignKey("dataset_revisions.dataset_revision_id"),
        nullable=False,
    ),
    Column("instrument_id", String(200), ForeignKey("instruments.instrument_id"), nullable=False),
    Column("timeframe", String(10), nullable=False),
    Column("range_start", DateTime(timezone=True), nullable=False),
    Column("range_end", DateTime(timezone=True), nullable=False),
    Column("bar_count", Integer, nullable=False),
    UniqueConstraint(
        "dataset_revision_id", "instrument_id", "timeframe", name="uq_dataset_membership"
    ),
    CheckConstraint("range_end > range_start", name="ck_dataset_membership_range"),
    CheckConstraint("bar_count >= 0", name="ck_dataset_membership_count"),
    CheckConstraint("timeframe IN ('1m', '5m', '15m', '1h', '1d')", name="ck_dataset_timeframe"),
)
Index(
    "ix_dataset_membership_lookup",
    dataset_memberships.c.instrument_id,
    dataset_memberships.c.timeframe,
    dataset_memberships.c.range_start,
    dataset_memberships.c.range_end,
)
dataset_content_lineage = Table(
    "dataset_content_lineage",
    metadata,
    Column("dataset_revision_id", String(200), primary_key=True),
    Column("instrument_id", String(200), primary_key=True),
    Column("timeframe", String(10), primary_key=True),
    Column("source_dataset_id", String(200), nullable=False),
    Column("requested_start", DateTime(timezone=True), nullable=False),
    Column("requested_end", DateTime(timezone=True), nullable=False),
    Column("actual_start", DateTime(timezone=True)),
    Column("actual_end", DateTime(timezone=True)),
    Column("bar_count", Integer, nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("validation_status", String(20), nullable=False),
    Column("provider_request_json", Text, nullable=False),
    Column("source_checksum", String(64), nullable=False),
    Column("canonical_checksum", String(64), nullable=False),
    Column("checksum_version", String(100), nullable=False),
    Column("dataset_format_version", String(100), nullable=False),
    ForeignKeyConstraint(
        ["dataset_revision_id", "instrument_id", "timeframe"],
        [
            "dataset_memberships.dataset_revision_id",
            "dataset_memberships.instrument_id",
            "dataset_memberships.timeframe",
        ],
        name="fk_lineage_membership",
    ),
    CheckConstraint("requested_end >= requested_start", name="ck_lineage_requested_range"),
    CheckConstraint("bar_count >= 0", name="ck_lineage_bar_count"),
    CheckConstraint(
        "(bar_count = 0 AND actual_start IS NULL AND actual_end IS NULL) OR "
        "(bar_count > 0 AND actual_start IS NOT NULL AND actual_end IS NOT NULL "
        "AND actual_start < actual_end)",
        name="ck_lineage_actual_range",
    ),
    CheckConstraint(
        "validation_status IN ('pass', 'warning', 'fail')",
        name="ck_lineage_validation_status",
    ),
)
Index("ix_lineage_source_dataset", dataset_content_lineage.c.source_dataset_id)


@dataclass(frozen=True, slots=True)
class DatasetMembership:
    instrument_id: str
    timeframe: Timeframe
    range_start: datetime
    range_end: datetime
    bar_count: int

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id must be non-empty")
        if not isinstance(self.timeframe, Timeframe):
            raise ValueError("timeframe must be canonical")
        for name in ("range_start", "range_end"):
            value = getattr(self, name)
            object.__setattr__(self, name, _utc(value, name))
        if self.range_end <= self.range_start:
            raise ValueError("range_end must be after range_start")
        if (
            isinstance(self.bar_count, bool)
            or not isinstance(self.bar_count, int)
            or self.bar_count < 0
        ):
            raise ValueError("bar_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DatasetRevision:
    dataset_revision_id: str
    dataset_id: str
    source_id: str
    provider: str
    retrieved_at: datetime
    created_at: datetime
    normalization_version: str
    calendar_version: str
    manifest_format_version: str
    memberships: tuple[DatasetMembership, ...]
    manifest_ref: str

    def __post_init__(self) -> None:
        for name in (
            "dataset_revision_id", "dataset_id", "source_id", "provider",
            "normalization_version", "calendar_version", "manifest_format_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.manifest_ref, str) or not self.manifest_ref.strip():
            raise ValueError("manifest_ref must be non-empty")
        for name in ("retrieved_at", "created_at"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        members = tuple(self.memberships)
        if not members or any(not isinstance(member, DatasetMembership) for member in members):
            raise ValueError("memberships must contain at least one DatasetMembership")
        keys = [(member.instrument_id, member.timeframe) for member in members]
        if len(keys) != len(set(keys)):
            raise ValueError("dataset memberships must have unique instrument/timeframe pairs")
        object.__setattr__(
            self,
            "memberships",
            tuple(sorted(members, key=lambda m: (m.instrument_id, m.timeframe))),
        )


def register_instrument(connection: Connection, instrument: Instrument) -> Instrument:
    """Insert once; an identical retry succeeds and a changed identity fails."""
    existing = load_instrument(connection, instrument.instrument_id)
    if existing is not None:
        if existing != instrument:
            raise MetadataConflictError(f"instrument {instrument.instrument_id!r} already differs")
        return existing
    try:
        with connection.begin_nested():
            connection.execute(instruments.insert().values(
                instrument_id=instrument.instrument_id,
                display_name=instrument.display_name,
                calendar_id=instrument.calendar_id,
                price_precision=instrument.price_precision,
                point_size=_decimal_text(instrument.point_size),
            ))
            for mapping in instrument.provider_symbols:
                connection.execute(provider_symbol_mappings.insert().values(
                    instrument_id=instrument.instrument_id,
                    provider=mapping.provider,
                    provider_key=mapping.provider.casefold(),
                    environment=mapping.environment,
                    environment_key=(mapping.environment or "").casefold(),
                    symbol=mapping.symbol,
                    symbol_key=mapping.symbol.casefold(),
                ))
    except IntegrityError as exc:
        existing = load_instrument(connection, instrument.instrument_id)
        if existing == instrument:
            return existing
        if existing is not None:
            raise MetadataConflictError(
                f"instrument {instrument.instrument_id!r} already differs"
            ) from None
        raise MetadataConflictError("provider symbol mapping is already assigned") from exc
    return instrument


def load_instrument(connection: Connection, instrument_id: str) -> Instrument | None:
    row = connection.execute(
        select(instruments).where(instruments.c.instrument_id == instrument_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    mappings = connection.execute(
        select(provider_symbol_mappings)
        .where(provider_symbol_mappings.c.instrument_id == instrument_id)
        .order_by(
            provider_symbol_mappings.c.provider_key,
            provider_symbol_mappings.c.environment_key,
        )
    ).mappings()
    return Instrument(
        instrument_id=row["instrument_id"],
        display_name=row["display_name"],
        calendar_id=row["calendar_id"],
        price_precision=row["price_precision"],
        point_size=Decimal(row["point_size"]),
        provider_symbols=tuple(
            ProviderSymbolMapping(m["provider"], m["symbol"], m["environment"]) for m in mappings
        ),
    )


def register_dataset_revision(connection: Connection, revision: DatasetRevision) -> DatasetRevision:
    """Persist revision lineage and membership atomically, without bar content."""
    existing = load_dataset_revision(connection, revision.dataset_revision_id)
    if existing is not None:
        if existing != revision:
            raise MetadataConflictError(
                f"dataset revision {revision.dataset_revision_id!r} already differs"
            )
        return existing
    try:
        with connection.begin_nested():
            connection.execute(dataset_revisions.insert().values(
                dataset_revision_id=revision.dataset_revision_id,
                dataset_id=revision.dataset_id,
                source_id=revision.source_id,
                provider=revision.provider,
                retrieved_at=revision.retrieved_at,
                created_at=revision.created_at,
                normalization_version=revision.normalization_version,
                calendar_version=revision.calendar_version,
                manifest_format_version=revision.manifest_format_version,
                manifest_ref=revision.manifest_ref,
            ))
            for member in revision.memberships:
                connection.execute(dataset_memberships.insert().values(
                    dataset_revision_id=revision.dataset_revision_id,
                    instrument_id=member.instrument_id,
                    timeframe=member.timeframe.value,
                    range_start=member.range_start,
                    range_end=member.range_end,
                    bar_count=member.bar_count,
                ))
    except IntegrityError:
        existing = load_dataset_revision(connection, revision.dataset_revision_id)
        if existing == revision:
            return existing
        if existing is not None:
            raise MetadataConflictError(
                f"dataset revision {revision.dataset_revision_id!r} already differs"
            ) from None
        raise
    return revision


def load_dataset_revision(connection: Connection, revision_id: str) -> DatasetRevision | None:
    row = connection.execute(
        select(dataset_revisions).where(dataset_revisions.c.dataset_revision_id == revision_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    members = connection.execute(
        select(dataset_memberships)
        .where(dataset_memberships.c.dataset_revision_id == revision_id)
        .order_by(dataset_memberships.c.instrument_id, dataset_memberships.c.timeframe)
    ).mappings()
    return DatasetRevision(
        dataset_revision_id=row["dataset_revision_id"],
        dataset_id=row["dataset_id"],
        source_id=row["source_id"],
        provider=row["provider"],
        retrieved_at=_stored_utc(row["retrieved_at"]),
        created_at=_stored_utc(row["created_at"]),
        normalization_version=row["normalization_version"],
        calendar_version=row["calendar_version"],
        manifest_format_version=row["manifest_format_version"],
        manifest_ref=row["manifest_ref"],
        memberships=tuple(DatasetMembership(
            instrument_id=m["instrument_id"],
            timeframe=Timeframe(m["timeframe"]),
            range_start=_stored_utc(m["range_start"]),
            range_end=_stored_utc(m["range_end"]),
            bar_count=m["bar_count"],
        ) for m in members),
    )


def list_dataset_revisions(connection: Connection, dataset_id: str) -> tuple[DatasetRevision, ...]:
    """Return revisions in stable identity order."""
    ids = tuple(connection.execute(
        select(dataset_revisions.c.dataset_revision_id)
        .where(dataset_revisions.c.dataset_id == dataset_id)
        .order_by(dataset_revisions.c.dataset_revision_id)
    ).scalars())
    return tuple(
        revision for revision_id in ids
        if (revision := load_dataset_revision(connection, revision_id)) is not None
    )


def register_dataset_lineage(connection: Connection, lineage: DatasetLineage) -> DatasetLineage:
    """Insert immutable content and acquisition lineage for one revision membership."""
    existing = load_dataset_lineage(
        connection, lineage.dataset_revision_id, lineage.instrument_id, lineage.timeframe
    )
    if existing is not None:
        if existing != lineage:
            raise MetadataConflictError("dataset lineage already differs")
        return existing
    member = connection.execute(
        select(dataset_memberships).where(
            dataset_memberships.c.dataset_revision_id == lineage.dataset_revision_id,
            dataset_memberships.c.instrument_id == lineage.instrument_id,
            dataset_memberships.c.timeframe == lineage.timeframe.value,
        )
    ).mappings().one_or_none()
    if member is None:
        raise MetadataConflictError("dataset lineage has no revision membership")
    if (
        lineage.bar_count != member["bar_count"]
        or lineage.requested_start != _stored_utc(member["range_start"])
        or lineage.requested_end != _stored_utc(member["range_end"])
    ):
        raise MetadataConflictError("dataset lineage differs from revision membership")
    try:
        with connection.begin_nested():
            connection.execute(dataset_content_lineage.insert().values(
                dataset_revision_id=lineage.dataset_revision_id,
                instrument_id=lineage.instrument_id,
                timeframe=lineage.timeframe.value,
                source_dataset_id=lineage.source_dataset_id,
                requested_start=lineage.requested_start,
                requested_end=lineage.requested_end,
                actual_start=lineage.actual_start,
                actual_end=lineage.actual_end,
                bar_count=lineage.bar_count,
                acquired_at=lineage.acquired_at,
                validation_status=lineage.validation_status.value,
                provider_request_json=lineage.provider_request_json,
                source_checksum=lineage.source_checksum,
                canonical_checksum=lineage.canonical_checksum,
                checksum_version=lineage.checksum_version,
                dataset_format_version=lineage.dataset_format_version,
            ))
    except IntegrityError:
        existing = load_dataset_lineage(
            connection, lineage.dataset_revision_id, lineage.instrument_id, lineage.timeframe
        )
        if existing == lineage:
            return existing
        raise MetadataConflictError("dataset lineage already differs") from None
    return lineage


def load_dataset_lineage(
    connection: Connection, revision_id: str, instrument_id: str, timeframe: Timeframe
) -> DatasetLineage | None:
    row = connection.execute(
        select(dataset_content_lineage).where(
            dataset_content_lineage.c.dataset_revision_id == revision_id,
            dataset_content_lineage.c.instrument_id == instrument_id,
            dataset_content_lineage.c.timeframe == timeframe.value,
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    return DatasetLineage(
        dataset_revision_id=row["dataset_revision_id"],
        source_dataset_id=row["source_dataset_id"],
        instrument_id=row["instrument_id"],
        timeframe=Timeframe(row["timeframe"]),
        requested_start=_stored_utc(row["requested_start"]),
        requested_end=_stored_utc(row["requested_end"]),
        actual_start=(None if row["actual_start"] is None else _stored_utc(row["actual_start"])),
        actual_end=(None if row["actual_end"] is None else _stored_utc(row["actual_end"])),
        bar_count=row["bar_count"],
        acquired_at=_stored_utc(row["acquired_at"]),
        validation_status=ValidationStatus(row["validation_status"]),
        provider_request_json=row["provider_request_json"],
        source_checksum=row["source_checksum"],
        canonical_checksum=row["canonical_checksum"],
        checksum_version=row["checksum_version"],
        dataset_format_version=row["dataset_format_version"],
    )


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops timezone information on round-trip; writes are always normalized to UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
