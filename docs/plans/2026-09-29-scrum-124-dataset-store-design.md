# SCRUM-124 — immutable local dataset revisions

Decision date: 2026-09-29. Scope: the Parquet content store, offline resolution,
and manual local backup/restore. Existing SCRUM-54/58/59/60 domain and
PostgreSQL lineage tables are reused; no new mutable shared-bars table is added.

## Publication and reading

Publish one canonical instrument/timeframe membership per revision for MVP.
The producer supplies completed UTC Bars in timestamp order, a versioned
`DatasetRevision`, and `DatasetLineage`. Identical adjacent duplicates collapse;
conflicting duplicates or out-of-order input fail before publication. The
store verifies identity, requested range, count, canonical checksum and
validation status. It writes a bounded-row-group Parquet file with exact
decimal text and UTC timestamps, plus a canonical JSON manifest recording
format version, provider/source/request provenance, retrieval and normalization
versions, source/canonical checksums, validation state, file checksum and
content range. The manifest is the authoritative on-disk description; existing
PostgreSQL metadata records its relative path and the independently checked
lineage. A staged directory is atomically renamed into a safe revision path
before the database transaction registers metadata. If the database write or
outer commit fails, an unreferenced immutable directory may remain, but no
database-visible revision points to partial content. Never overwrite a
published revision; corrected bars require a new revision ID.

Offline reads resolve the database revision and manifest, reject unknown
format versions, verify file and canonical checksums, and reconstruct Bars
without provider access. The first supported format is `parquet-v1`; older
format compatibility is added through explicit versioned readers when an
older format actually exists, rather than guessing how to parse unknown data.

## Backup and restore

Owner update on 2026-09-29: the local macOS disposable test confirmed that
PostgreSQL 16 refused the host bind-mounted data directory because of its
ownership. The owner approved a Docker-managed named PostgreSQL volume for
MVP. This supersedes the earlier single-root requirement for the live database
files only; datasets, artifacts, exports and logs remain in the configured
data root. The named volume is scoped by the Compose project name. Manual
backup remains the portable, complete transfer unit. An existing bind-mounted
`data/postgres` cluster is preserved but not automatically migrated.

Two alternatives were rejected: copying PostgreSQL's live volume (not a
portable consistent database backup), and restoring into an existing data
root (could overwrite or mingle immutable research evidence). The manual Nx
backup target uses a PostgreSQL custom-format dump and includes published
dataset files plus local artifacts/exports in a portable archive with a
versioned checksum manifest. Backup requires application workers to be idle
and stopped; this gives a stable metadata/file snapshot without interrupting
an active import or evaluation. No `.env`, credentials, raw PostgreSQL volume,
source checkout, or previous backups enter the archive.

The owner approved an empty-target restore. It validates archive members and
checksums before extraction, refuses a nonempty target data root or database,
restores the dump, then verifies every database-referenced manifest and
Parquet revision. Failed restore leaves the new target for inspection rather
than deleting or replacing existing data. Tests cover golden manifests and
checksums, duplicate/conflict and immutability behavior, Parquet round trips,
format refusal, offline reads, malicious archive paths, and backup/restore
verification against a disposable PostgreSQL database where available.
