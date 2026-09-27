# Sprint 2 dataset identity and lineage design — 2026-09-27

Scope: SCRUM-60 only. Its prerequisite SCRUM-58 is Done; physical immutable
Parquet publication is SCRUM-124. SCRUM-61 consumes the resulting revision
identity and must not invent a second dataset identity.

## Decision

Three boundaries were considered: hash database rows, hash Parquet file bytes,
or hash a canonical Bar sequence before storage. Use the canonical sequence.
Database layout and Parquet encoding can change without changing analytical
content. Source bytes receive their own checksum; source/import provenance is
not smuggled into the canonical-content hash.

The canonical checksum is SHA-256 over a version-tagged, stable UTF-8 JSON
representation of unique completed Bars sorted by timestamp. Input ordering
does not affect the checksum; a published `BarSequence` remains strictly
ordered. A streaming checksum path accepts already ordered bars for large
datasets without buffering the entire history. Include all
analytical fields (instrument, timeframe, UTC timestamp, OHLC, volume,
completeness and quality flags), but exclude provider-specific source_id.
Normalize decimal text and sort quality flags; reject mixed instruments,
timeframes, duplicate timestamps and incomplete bars. A
content change changes the digest; a metadata-only change does not.

## Metadata and storage

Keep the existing immutable `dataset_revisions` and `dataset_memberships`
records. Add a one-to-one-per-membership lineage record with source dataset
identity, requested and actual half-open ranges, bar count, acquisition time,
validation status, canonicalized provider request metadata, source and canonical
checksums, checksum and dataset-format versions. Existing revision metadata
supplies provider, retrieval,
normalization, calendar and manifest provenance. The new record is immutable
and must match its revision membership. A `BarSequence` couples the ordered
bars to this lineage in memory and verifies count, bounds and checksum.
Empty sequences have no actual range.

No physical Bar storage or file format is implemented here. SCRUM-124 will
write Parquet, verify manifests and publish revisions; SCRUM-57 will acquire
and checkpoint provider data. SCRUM-60 tests deterministic checksums,
mutation, metadata round-trip and database immutability.
