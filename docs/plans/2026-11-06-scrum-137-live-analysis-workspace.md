# SCRUM-137 — live analytical sessions and chart integration

This delivery completes the software integration following the [shared-ingress increment](2026-11-05-scrum-137-shared-live-ingress.md). The subsequent [provider/calendar acceptance](2026-11-06-scrum-137-provider-acceptance.md) located the documented server configuration, verified real US30/DAX ingestion and replay parity, and added a new finite calendar version through 2026-11-06 while retaining the previous version. No installation migration or unverified future-calendar extrapolation is claimed.

## Implemented behavior

- Recent initialization history is prepared through shared retained-range acquisition, including when the tab has no selected provider revision. Only an exact-feed bootstrap with matching calendar and normalization can initialize live analysis.
- Every connection gets an independent analytical session and immutable `live-input-*` identity. The ordinary aggregation/detector runtime processes completed M1 candles and completed analytical candles; live, manual and always policies retain their agreed meanings. Forming candles are display-only.
- Feed acquisition is shared across tabs. Server-side listeners advance connected detector sessions even when every browser tab is hidden. On restoration, only the focused tab connects; other restored tabs connect on first focus. Hidden tabs suspend chart DOM and view polling.
- Pan/zoom freezes the displayed cutoff while ingestion and detectors continue. Return to latest resumes chart following. The date picker moves within retained context or prepares older provider history. Left-edge navigation pages retained candles and uses shared historical acquisition outside the initialized live context.
- Manual and automatic historical calculations use separate frozen source revisions/runtimes. Browsing or calculating history never rewinds a connected live runtime. Reapplying settings creates a new connection/input identity; explicit disconnect and tab close remove that consumer. Cancelled connection identities cannot be resurrected by a delayed request.
- Bootstrap overlap is checked against captured journal content under the acquisition feed lock through initialization and manifest publication. Later-arriving conflicting overlap freezes analysis. A failed analytical transaction exposes only the last committed prefix and requires reinitialization.
- Event and missed-pattern annotations resolve through persisted live lineage after disconnect/restart. Live missed-pattern targets retain an explicit live-input foreign key and are bounded by committed availability, rather than pretending to be historical dataset memberships.

## Persistence and recovery

Migration `20261105_15` adds immutable input manifests, per-minute consumption receipts and committed analytical availability receipts. Existing completed-candle journal and hourly Parquet retention remain unchanged. Run snapshots point to the live input; the manifest pins the authenticated bootstrap checksum and exact feed. Receipt verification checks captured bytes/checksums, chronological continuity, scheduled closures and the committed cutoff. `verify_datasets` includes live analytical input verification.

The schema also extends missed-pattern attribution to live inputs and preserves annotation immutability guards when SQLite rebuilds the annotation table. Downgrade refuses to discard used live lineage: restore a compatible pre-live backup instead. No migration was applied to the owner's installation.

A task-owned PostgreSQL 16 container verified real migration, live ingestion/analysis, attribution, stopped-session dump/archive/extraction/restore and independent verification of restored source/journal/input data. The container is separate from application data.

## Verification and review

Deterministic tests cover captured-live/replay semantic parity, independent US30/DAX feeds and settings, hidden processing, manual dispatch exclusion, forming separation, gap/failure rollback, conflicting bootstrap rejection, durable annotation APIs, current-calendar rejection, retained history initialization and migration guards. Browser tests cover lazy restoration, hidden polling suspension, settings application, separate historical calculation, live pan/follow, disconnect/close, rolling 500-candle detection visibility and earlier-history navigation.

Final verification: **986 Python tests passed, 12 skipped**; **56 browser tests passed**; **49 frontend unit tests passed**; the isolated real PostgreSQL migration and portable backup/restore test passed. Ruff, mypy (91 source files), frontend build/lint and diff checks passed. Fake live feeds select an explicit bootstrap so earlier smoke scenarios cannot change their source; the real historical-calculation assertion allows asynchronous completion without weakening its expected result.

The [Astra Python/web review](../reviews/2026-11-06-live-analysis-workspace-review.md) records findings, fixes and final verification results. Actual provider/current-calendar acceptance is separate from these deterministic tests.

## Provider acceptance follow-up

The [2026-11-06 acceptance record](2026-11-06-scrum-137-provider-acceptance.md) supersedes the earlier configuration/calendar blocker. Real account-scoped read-only checks and isolated live/replay parity passed. The schedule remains finite through analytical date 2026-11-06; repeat the approved verification/versioning procedure before sessions outside that range. The installed application was not started or migrated by this verification.
