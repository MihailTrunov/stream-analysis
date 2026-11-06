# SCRUM-110 — semantic parity suite

Current increment: [SCRUM-96](2026-11-06-scrum-96-autonomous-execution.md) supplies the real autonomous executor. The matrix now covers replay, historical exploration, captured-live and autonomous execution, with persisted event-time snapshots from SCRUM-119. See the completion amendment below; earlier delivery statements are retained as foundation history.

SCRUM-110 compares completed-candle analytical state, ordered market/detector events and event-time context across equivalent source/config/calendar/aggregation/initialization inputs. The foundation originally covered replay, requested historical calculation and captured-live drivers while autonomous execution and snapshot capture were outstanding. Historical calculation remains a distinct driver.

## Foundation increment

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

## Autonomous completion amendment — 2026-11-06

The existing 13-case real-driver matrix now includes production `execute_evaluation`, rather than an evaluation-shaped test harness. It compares every frame/event/context and final SQL occurrence against the separate causal reference and directly against replay/history for their common immutable dataset identity. Live source identities remain bound to their own verified capture, so opaque semantic references are preserved exactly.

All four modes receive the same fully resolved canonical configuration, including canonical component/parameter ordering and Decimal representation. Canonical config storage does not retain cosmetic decimal scale. Actual analytical values, evidence references, component versions and source identity are not removed or relaxed. Persistence UUIDs and execution run lineage remain the existing declared exclusions. Exact equality remains the default.

An additional production execution test runs M1/M5 three times with fresh run identities, outcome horizons 2/3/2 and chunk sizes 1/17/128. Plan hashes differ for different horizons while analytical config identity, all X-side frames/events/snapshots and final occurrence semantic keys remain equal. Eligible pending outcomes leave these runs DETECTION_COMPLETED, confirming they were not silently calculated or used as detector inputs.

Scope clarification: original SCRUM-110 explicitly excludes OutcomeObservations from runtime parity, and SCRUM-96 explicitly excludes outcome calculation. Earlier references to requiring autonomous **outcome execution** overstated these stories. The required gate is actual autonomous detector execution with outcome-only plan differences. Production formulas, segment execution and statistics remain their own stories; no acceptance criterion is satisfied by pretending they ran.

The same normal pytest/CI discovery includes this adapter and invariance test. Existing deliberate-mismatch and exact/path-specific tolerance probes still provide first-divergence diagnostics. Final verification is recorded in the [implementation review](../reviews/2026-11-06-scrum-110-parity-review.md).
