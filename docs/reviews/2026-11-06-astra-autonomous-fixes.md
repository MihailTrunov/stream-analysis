# Fixes for the Astra autonomous review

This records implementation verification for the two findings in the [independent Astra review](2026-11-06-astra-autonomous-evaluation-review.md). It is not a second independent review.

## P1: execute the frozen configuration in chart sessions

Chart sessions now reconstruct runtime configuration from the canonical RunSnapshot they just persisted, matching autonomous execution and the existing persisted replay path. They no longer execute the original settings object, whose cosmetic Decimal scales could differ from the frozen payload despite an identical DetectionConfigHash.

The four-mode test passes raw/default settings to production again. Only the independent reference consumes canonical frozen settings, reflecting the run contract; test inputs are no longer normalized before reaching the actual chart/live/history boundaries. Exact evidence references, SQL X snapshots and final occurrence context remain compared, without new tolerances or exclusions.

Existing configuration hashes, detector definitions and reference producers are unchanged. Existing persisted records are not rewritten. Direct runtime/authored primitive fixtures retain their existing contracts; this correction makes the production session execute what its snapshot says it executes.

## P2: establish an actual SQLite transaction before nested writes

The shared `atomic_transaction` helper explicitly begins SQLite's database transaction when legacy DBAPI transaction control has not begun one. Autonomous claim/chunk/final-dispatch/termination boundaries use it, as does the evaluation API transaction. PostgreSQL continues to use its ordinary transaction semantics.

The new migrated SQLite regressions fail or cancel immediately after the first detector event in a one-minute chunk. Both assert zero retained pattern instances, transitions, X snapshots and population memberships from that chunk, alongside the prior committed cursor/stream progress. They reproduce the original SAVEPOINT leak before the fix and pass afterward.

## Verification

Before the fix, both raw-setting curated detector cases and both first-event rollback cases failed (**4 failures**). After the fix, all four passed. Full Python verification passed **1,104 tests (18 PostgreSQL-dependent skips)**, and the separate combined PostgreSQL-targeted selection passed **19 checks**. Browser execution passed 55 checks on the initial run; its one page-creation timeout occurred before application execution and passed on an isolated rerun. Ruff, mypy (105 source files) and diff checks passed. No installed database or provider was used.

The next delivery is the approved SCRUM-123 queue scheduler; full outcome algorithms and the Runs/results UI remain separate work.
