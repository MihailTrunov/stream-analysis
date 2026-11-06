# Fixes for the independent Astra scheduler review

This is implementation verification for the three P2 findings in the [Astra high review](2026-11-06-astra-scheduler-review.md), not a new independent review.

Standalone execution now maintains an ownership-aware heartbeat throughout source authentication/loading and candle processing. It refreshes only the matching RUNNING owner, cannot revive a fenced run, stops on exit, and updates the explicit worker's service heartbeat. Progress and periodic heartbeat timestamps cannot move backwards when concurrent writes wait for one another. Queued execution keeps its existing job heartbeat owner.

PostgreSQL orphan recovery now locks and rechecks the stale-heartbeat predicate before deciding to fail a run, matching queued recovery. A coordinated row-lock regression verifies that a freshly committed explicit progress heartbeat prevents expiry. Genuine stale explicit executions still fail and pause the queue.

One shared chunk-size validator now runs before scheduler recovery/claim and before worker startup mutations, as well as at direct executor entry. Invalid integer ranges and non-integer values leave the queued job, fresh run and queue state untouched.

All seven new reproducing cases failed before the fixes and passed afterward. The source-loading regression uses a short real lease with a gated live loader and a shorter heartbeat interval; it does not rewrite heartbeat timestamps. An additional ownership/monotonicity case covers delayed updates and fenced-owner rejection.

Verification: **1,130 Python passed (21 PostgreSQL-dependent skips)**; separate affected scheduler/executor selection **44 passed with PostgreSQL enabled**, including the real orphan heartbeat race and existing queue claim/recovery checks. Ruff, mypy (109 source files) and diff checks passed. No browser implementation changed, and browser checks were not repeated for this backend-only correction. No installed database or provider was used. The authorized sequence continues with SCRUM-97.
