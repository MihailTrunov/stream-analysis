# SCRUM-128 — indexed retained-history reads

This increment starts the shared-history story after the four round-two chart review corrections. It improves the existing canonical M1 price endpoint without changing replay inputs or merging source revisions.

## Implemented boundary

`DatasetStore.read_range` returns at most 500 candles from an ordered, timezone-aware interval inside one pinned revision. It selects the latest matching Parquet row groups through their persisted timestamp statistics, reads backward until enough matching bars exist, and returns them in chronological order. Missing statistics fall back to reading that group. It does not construct a full revision-sized tuple or retain price rows between requests.

Before trusting the row-group index, a cold revision receives the existing full file hash, canonical checksum, membership, ordering and lineage verification. A process-wide LRU stores at most 32 successful verification identities across tabs and `DatasetStore` instances. Identity includes the expected immutable manifest, absolute path, device/inode, size, and nanosecond modification/change times. Metadata is checked against the database on every request. A file change invalidates reuse, including a same-size edit with restored modification time. Concurrent changes during verification or reading fail the request.

The existing `/replay/sources/{revision_id}/bars` endpoint now uses this path. Its response and 500-candle limit remain compatible. Replay execution and analytical initialization retain their full input verification. Existing retained Parquet files supply the index; no schema migration or reimport is needed. After process restart, files are reused and verification is repeated on first access.

## Verification

The focused dataset/browser API suites pass 41 tests. New cases demonstrate indexed reads across row-group boundaries, skipping later/earlier unrelated groups, shared verification across store instances, cold verification after simulated restart, unchanged bounded result order, database-lineage mismatch rejection, and detection of a same-size corrupted file with restored modification time. Invalid limits are rejected before reading data.

Final regression: **846 Python tests passed, 10 skipped**; **25 browser cases passed**. Targeted Ruff/mypy and `git diff --check` passed. The external PostgreSQL integration tests remain skipped. The prior review-correction increment also passed 26 web unit tests, web lint, TypeScript and production build; this increment changes Python storage and API orchestration only.

## Remaining SCRUM-128 work

This is a storage/read foundation, not completion of shared range acquisition. The higher-timeframe historical-price endpoint still reads its full pinned input. A first-access checksum scan is still proportional to revision size; measure cold-start cost before introducing additional trusted publication indexes.

Next, introduce a range-coverage service that identifies a feed by provider, environment/account fingerprint, symbol, price component, calendar and normalization version. Retained revisions must remain immutable and conflicting overlaps must be explicit. Synthetic demo revisions with different prices must not be combined merely because they name the same instrument.

The service should subtract validated retained coverage and equivalent in-flight requests, use the existing durable import/checkpoint/publication machinery, and expose pending, unavailable, failed and unexpected-gap states. The current import scheduler allows only one queued/running import globally; range orchestration must respect that contract or deliberately migrate it with worker/concurrency tests. It must not enqueue several missing segments and assume they can coexist today.

Acquisition and composite source pinning precede leftward navigation beyond the retained revision and fetch-backed Move to (SCRUM-133). Unsupported verified-calendar dates must remain explicit errors. No live provider request, calendar extrapolation, multi-revision merge or replay input mutation is part of this increment.
