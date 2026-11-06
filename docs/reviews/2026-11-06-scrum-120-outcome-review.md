# SCRUM-120 implementation review

This is an implementation/self-review, not an independent Astra review.

The review traced source-event/context prerequisites, pinned plan resolution, normalized parameter identity, output type/status validation, bounded future windows, source-close reference prices, same-day calendar limits, canonical event order and immutable SQL loading. The four result kinds are exercised through the same common evaluator. Production metric formulas are not represented as delivered.

Boundary fixtures cover missing and gapped source minutes, scheduled breaks, same-bar threshold ambiguity, higher-timeframe pre-availability exclusion, explicit retrospective labelling, undeclared categories/event references, immutable recalculation and unchanged detection-time snapshots after outcomes. A horizon-only plan change preserves actual detector semantic identities and X-side content.

A confirmed SQLite downgrade-chain defect was reproduced and fixed in the migration runner: an older retention refusal now rolls back newer DDL as well as version metadata. The regression checks both table survival and schema version. SQLite and disposable PostgreSQL enforce immutable outcome rows and refuse destructive downgrade after capture.

See the [versioned contract](../plans/2026-11-06-scrum-120-outcome-framework.md). Verification: **1,062 Python passed, 14 skipped** in the full increment run; the final outcome suite passed **27 tests (1 PostgreSQL skip)** after the horizon/build-lineage additions. A separate real PostgreSQL outcome test passed. Ruff, mypy (102 source files) and diff checks passed. Existing skipped PostgreSQL tests are not claimed executed. Browser code was unchanged; no browser rerun is claimed for this increment.

Outcome execution also verifies that the current build/revision/dirty/capture identity matches its pinned source run before claiming that lineage. No installed database or provider service was changed.
