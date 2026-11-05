# SCRUM-136 — durable completed minutes and hourly history publication

## Storage decision

Use a PostgreSQL journal for completed live source minutes, alongside the existing immutable Parquet dataset store. `live_feeds` pins the exact provider/account fingerprint/symbol/price/calendar/normalization identity. `live_candles` stores canonical UTC M1 records, checksums and acknowledgment timestamps. `live_segments` tracks pending/sealed hours and scheduling attempts. These tables are introduced by migration `20261105_14`.

An append-only file per minute would reuse staging files but require a second commit/recovery boundary and many tiny files. Keeping only an in-memory hour would lose acknowledged data on restart. A transactional journal uses the existing PostgreSQL durability and backup boundary; immutable Parquet remains the published historical input format. The journal is shared by feed, never per tab. Its rows are retained after packaging in this initial delivery; pruning requires a separately verified read-through and retention policy.

## Acknowledgment and validation

`retain_completed_minute` owns its transaction and returns only after commit. Its caller must not acknowledge ingestion or dispatch acknowledged downstream work before it returns. It accepts already finalized canonical M1 bars for an exact supported feed, registered instrument, aligned timestamp, verified open calendar minute and elapsed close. Forming candle updates and provider completion detection belong to SCRUM-137. No public ingestion endpoint or provider connection is introduced here.

Feed rows serialize append and publication using PostgreSQL row locks. Exact redelivery returns an idempotent receipt. A different completed value for the same feed/minute raises a conflict and preserves the first acknowledged record. Different feed/account identities remain independent. Missing minutes stay explicit; a caller must repair catch-up gaps rather than treating missing data as uninterrupted analytical state.

## Recent reads and hourly packaging

`GET /history/live/{feed_key}/bars` reads the durable journal before or after packaging. Bounds are completed aligned instants spanning at most 31 days; responses contain at most 500 M1 candles and report missing open minutes for that returned slice. It does not synthesize prices, fetch provider data or mutate an active/pinned run. Existing chart tabs will consume this live read boundary during SCRUM-137; this increment does not enable the Live UI.

The existing import worker attempts up to four elapsed UTC hours per scheduling pass, including when provider credentials are absent and between import pages. Persistent attempt ordering rotates blocked hours so they do not starve later work. Each hour is bounded to at most sixty canonical source minutes. Capture may begin mid-hour: the published range starts at its first retained minute and ends at the hour boundary. Every verified open minute inside that range must be present before sealing. After sealing, exact redeliveries remain valid; new earlier minutes or changed values require a separate reviewed correction revision.

Publication uses `DatasetStore.publish` with canonical/source checksums, source provenance, calendar gap report and immutable lineage. Revision identity includes feed, UTC hour and content hash. File publication precedes the database publication/segment-marker commit, as in historical imports. A failure can leave an unreferenced immutable directory, but cannot remove acknowledged journal rows. Identical retry authenticates/reuses that directory; changed pre-seal content receives another identity. A pinned revision is never overwritten. Packaged segments are ordinary authenticated parents for shared range coverage and composite inputs.

## Backup and recovery

Existing stopped-application backups include all three journal tables in `postgres.dump` and published Parquet/manifests under the shared data root. No new archive folder is needed. Restore verification now checks completed journal content, feed identity, recorded close times, calendar validity and sealed-hour checksums in addition to dataset and import-batch verification. The SQL journal and data-root files must be backed up together; copying `./data` alone omits unpackaged candles.

## Verification

Focused tests cover acknowledgment/commit failure, engine restart, invalid/forming/future/closed minutes, feed isolation, duplicate/conflicting delivery, read-before-packaging, bounded API reads, gaps/repair, first partial hour, sealed immutability, failure after file publication, changed-content orphan recovery, scheduling fairness, migration upgrade/downgrade, packaging without credentials and archive restoration. PostgreSQL integration covers concurrent duplicate writers and packagers using separate engines.

Final verification: **948 Python tests passed, 11 skipped** in the ordinary suite. **The dedicated PostgreSQL 16 integration passed** against an isolated temporary container: four duplicate writers produced one retained minute, two packagers returned the same revision, conflicting redelivery was rejected, and an actual `pg_dump`/`pg_restore` recovered sixty packaged minutes plus one unpackaged minute. A subsequent PostgreSQL rerun passed after restore verification was tightened. **36 focused retention/archive/import-worker tests passed** on the final recovery path. Ruff, full backend mypy and `git diff --check` passed. Three old migration-head assertions were updated from `20261104_13` to `20261105_14` after the first full run identified them.

The ordinary suite's eleven skips include external integration cases; they are not all verified by the single dedicated PostgreSQL scenario. Browser code is unchanged and browser suites were not rerun for this backend increment. No real provider was contacted. The temporary PostgreSQL container was separate from the application's database and volumes and was removed after testing. SCRUM-136 is ready for In Review; apply migration `20261105_14` through the normal startup/migration process before using the new retention boundary. The user's existing installation was not migrated by this task.

Provider streaming, reconnect/catch-up orchestration, tab connections/rendering, live detector dispatch and live-follow UI remain SCRUM-137. This delivery does not claim real-provider acceptance, correction resolution, automatic journal pruning or extension of verified calendar dates.
