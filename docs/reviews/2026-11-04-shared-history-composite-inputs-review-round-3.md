# Shared history and composite inputs — review round 3

Date: 2026-11-04  
Reviewer: Astra, medium reasoning  
Reviewed revision: `b33ddff1244a5755883cba59d0c8dd1082bd447b` (`master`)  
Baseline: `869faf738bdc3fcdc94ff15897834b4a99d0df50`  
Change range: `869faf7..b33ddff` — `4c861b7`, `1a0a7d7`, `b4c6f99`, `b33ddff`.

## Assessment and findings

**No confirmed correctness defects requiring a change were found in this increment.** This is a scoped review of indexed M1 reads, durable shared-range scheduling, authenticated streaming/publication and immutable composite inputs. It is not acceptance of the entire chart-workspace story or live-provider operation.

The review applied the repository code-review skill and inspected the changed production files, relevant tests, chart-workspace specification, domain and architecture contracts, story contracts, all three SCRUM-128 increment plans, the round-two review and its correction record. The baseline already contains the four round-two corrections. The focused session tests still pass for scheduled-close finalization, initialized coverage, analytical review attribution and reclaimable historical runtimes.

There are no severity-ranked findings, defect locations or proposed corrective patches in this round. In particular, the following investigated cases did not establish defects:

- **Overlap policy:** exact equivalent retained revisions can share coverage; other overlapping revisions deliberately report conflict. Comparing whole-revision checksums rather than proving equality of a smaller overlap is conservative behavior explicitly documented by this increment. It must not be presented as automatic correction resolution when chart integration arrives.
- **Cold authentication:** `iter_verified_bars` performs verification eagerly before returning its iterator. Calling it without consuming that returned iterator in `_verified_range_path` does perform the promised file/content validation.
- **Failed acquisition recovery:** a failed prefix does not prevent a different missing suffix being scheduled. After the request settles, an explicit fresh import repairs the failed prefix and the existing request resolves as available without creating a new request identity.
- **Scheduler scan bound:** settling the first twenty closed requests allows the next scheduler iteration to reach later open work; this does not permanently starve the twenty-first request.
- **Composite replay:** a parent boundary inside an analytical five-minute candle does not reset aggregation or expose subsequent source candles. The additional probe described below crossed that boundary successfully.

## Architecture and code structure

**Preserve the three distinct identities.** `history_ranges.py` retains mutable acquisition intent and resolves current coverage; `history_inputs.py` turns explicitly reviewed coverage into a frozen source plan; existing analysis/replay services pin the resulting ordinary dataset revision. Acquisition corrections can change coverage without rewriting a published composite or an existing run. Keeping composites out of acquisition-source selection avoids having them compete with their own parents.

**The publication boundary is appropriate.** Composition records feed identity, request hashes, selected source spans and parent canonical/source checksums and versions. It independently checks every expected-open minute, merges overlapping selections of the same parent, permits verified closures and rejects uncovered open gaps. Two streaming passes preserve bounded price memory while verifying the staged result. The materialized revision can be read after parent price files disappear; provenance remains in its own manifest. Deterministic identity and timestamps support rollback/retry reuse of unreferenced published files.

**The storage cache retains authentication, not mutable prices.** Every range access checks database lineage against the manifest. Cached verification keys include the manifest and filesystem identity/change timestamps; selected row groups are checked for changes before successful return/exhaustion. The directory-race path authenticates an equivalent winner before registration. Tests support these boundaries, including corrupted competing output; they do not establish end-to-end PostgreSQL multi-process acceptance.

**Scheduling fits the existing worker.** Durable requests sit above the single-active-import invariant and reuse the existing import/checkpoint/publication pipeline. Feed matching includes account fingerprint, provider/environment, symbol, price basis, calendar and normalization. Pending work is reused, closed slots do not create downloads, and terminal acquisition failures require explicit recovery. API reads and composition do not require provider credentials; configured account context remains necessary to create a feed-specific request.

**Keep future changes localized.** The application modules contain orchestration while detector formulas remain untouched, and persistence remains responsible for immutable files and metadata. The current dictionary-based coverage/composition contracts are manageable, but typed internal coverage/span/manifest models would become useful when the frontend and additional consumers depend on these shapes. This is a maintainability opportunity, not a defect or a reason to rewrite the services.

Before optimizing further, measure coverage resolution over many retained segments. It authenticates every candidate source and walks calendar slots; the process-wide 32-entry verification LRU and global verification lock can make broad or concurrent cold requests expensive. The bounded candle response is not a claim of constant-time coverage resolution. No representative performance measurement was performed here, so this is not a severity-ranked performance finding.

## Executed verification

- `.venv/bin/pytest -q tests/unit/test_dataset_store.py tests/unit/test_history_ranges.py tests/unit/test_history_inputs.py tests/test_analysis_sessions.py`: **78 passed** (14 storage, 24 range, 17 composition, 23 session tests). This includes corrupted files, source-lineage changes during publication, rollback/retry, competing publication, migration roundtrip, pending/local overlap reuse, fake-provider worker integration, explicit gap repair, self-contained restart reads and causal analysis integration.
- Three additional temporary tests in `/tmp/streamanalysis-review3/test_extra.py`: **3 passed**. Command: `.venv/bin/pytest -q --confcutdir=/tmp/streamanalysis-review3 --rootdir=/tmp/streamanalysis-review3 /tmp/streamanalysis-review3/test_extra.py`.
- `git diff --check`: passed before and after writing this report.

The extra tests use existing fixture helpers with isolated temporary SQLite databases and fake ingress:

1. Fail acquisition of minutes `[0,2)`, then request `[0,4)`. The scheduler fetches only `[2,4)`, retains the failed state, and an explicit fresh `[0,2)` import makes both existing requests available.
2. Create twenty-one distinct verified Sunday-closure requests followed by an open request. The first scheduling call settles twenty requests; the next schedules the open request after settling the remaining closed request.
3. Publish parents `[14:00,15:03)` and `[15:03,15:20)` on 2026-09-28, with the second parent's close changing from 101 to 102. Launch M5 analysis at 15:00 and Step once. Exactly five source candles become visible, thirteen analytical candles have been processed including initialization, and the last visible M5 candle is 15:00 with close 102. Later candles remain outside the returned prefix.

The first attempt to collect an external `/tmp` probe together with repository tests encountered an OS permission error while pytest traversed an unrelated temporary directory. Restricting collection to the dedicated probe directory resolved it; no permissions escalation was needed. The successful runs emitted the existing Starlette/AnyIO deprecation warning.

The previously reported **891 Python passed / 10 skipped** and **25 browser passed** remain prior validation evidence, not suites rerun in this review. This round did not run browser tests, a live provider, PostgreSQL concurrency/integration tests, a year-scale workload, or process-restart cursor reconstruction. No credentials or prohibited settings files were read. Production code and existing tests were unchanged; only this report and temporary probes were written. No commit, push, Jira update or further agent was used.

## Next priority

Proceed with the bounded higher-timeframe price path and chart integration of shared range coverage and explicit composite publication under SCRUM-128/SCRUM-133. Carry unavailable, pending, failed, gap and conflict states into navigation, and retain the distinction between price coverage and initialized analysis coverage. Fetch enough earlier source history for the selected analytical warm-up before launching replay; publication alone does not supply it.

UI/chart navigation, higher-timeframe bounded reads, live-provider acceptance, verified-calendar extension, PostgreSQL concurrency acceptance and full API-restart cursor recovery remain explicit follow-up boundaries, not defects retroactively assigned to this delivered increment.
