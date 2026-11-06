# SCRUM-110 — semantic parity suite

SCRUM-110 compares completed-candle analytical state, ordered market/detector events and event-time context across equivalent source/config/calendar/aggregation/initialization inputs. The current implementation has replay, requested historical calculation and captured-live drivers. Autonomous EvaluationRun execution (SCRUM-96) and DetectionTimeContextSnapshot capture (SCRUM-119) are not implemented; their acceptance gates remain open. Historical calculation is not relabelled autonomous evaluation, and occurrence context is not relabelled an event-time snapshot.

## Current increment

Add a test-only semantic trace/comparison helper under tests/regression. Compare source checksum, resolved per-stream configuration/version identity, calendar, aggregation and initialization origin before analytical records. Report the earliest differing completed candle, path and expected/actual value. Preserve ordering and semantic content; numeric tolerances are explicitly declared by fixture path and default to exact equality.

Drive the existing replay/history/live implementations using identical canonical source prefixes. A separate causal reference driver uses the ordinary aggregator/runtime directly, independent of session orchestration. Cryptographic market-event and occurrence references include the source input identity: each mode therefore gets a reference chain bound to its actual input identity, keeping these evidence fields comparable exactly rather than deleting or rewriting opaque hashes. Run UUIDs and persistence UUIDs are excluded only at declared adapter boundaries; component identities, source-event references, versions, rationale and typed occurrence context remain significant.

Curated M1 cases cover the real compression, reversal and continuation detectors and the full component chain. A higher-timeframe matrix covers supported analytical intervals, scheduled closures and a longer source sample crossing DST boundaries. Persisted live receipts and source checksums establish which immutable source prefix each comparison uses. Existing hand-reviewed primitive/detector fixtures remain the independent formula expectations; parity checks add orchestration/causality coverage rather than generating replacement goldens from the implementation.

Inject state/event/context/config/source/order differences to verify first-divergence diagnostics, reference preservation and numeric-tolerance behavior. Tests run in the ordinary pytest suite, so the existing CI test job runs them without a duplicate workflow invocation or provider credentials.

## Completion boundary

Current-mode coverage can be delivered now. Full SCRUM-110 cannot be declared complete until real EvaluationRun and DetectionTimeContextSnapshot paths participate, including EvaluationPlan-only change invariance on actual execution. Track these missing hard dependencies explicitly in Jira and this delivery evidence.


## Delivered foundation and discovered defect

The [implementation/review record](../reviews/2026-11-06-scrum-110-parity-review.md) details the current-mode matrix and a confirmed source-reference defect: nested structural evidence included execution run UUIDs. The versioned v2 reference producer now excludes those operational IDs while preserving semantic lineage and existing stored v1 evidence. Original authored fixture hashes remain guarded through an independently reconstructed v1 envelope; new v2 identity has a separate literal contract fixture.

Prerequisite sequencing correction: complete SCRUM-119's event-time snapshot contract, then the required EvaluationRun/EventSet modelling and execution dependencies (SCRUM-95/96, including their own plan/outcome contracts), before closing the remaining SCRUM-110 acceptance gates. This test foundation does not silently implement or rename those features.

Final verification: **1,025 Python passed (12 skipped), 56 browser passed**; Ruff, mypy and diff checks passed. The 33-test regression suite is included in normal pytest discovery. SCRUM-110 remains In Progress; the next prerequisite is SCRUM-119.
