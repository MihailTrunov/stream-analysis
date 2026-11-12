# Claude review: evaluation hot-path re-verification cost

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-hot-path-cost.md).

Reviewed state: current master (`be478a2`; evaluation-stage files unchanged
since the review batch).

## Verdict

**No findings; recommend deferring all amortization.** Measured costs are
negligible at MVP scale and stay minor at the scale-ceiling review's accepted
ceiling. One change is worth scheduling *with* SCRUM-102 (set-based
completion-gate query); the rest should wait for a demonstrated hotspot.

## Measurements

Instrumented a full direct execution (demo multihour source: 300 M1 minutes,
one stream, 11 outcome selections, chunk 128) with call counters and a
SQLAlchemy checkout listener. Wall time 3.20 s, 7 events, 7 eligible, 77
observations, status COMPLETED. Scripts retained in the reviewer's session
temp directory.

1. **Per-event re-verification is real but tiny.** `read_evaluation_run` was
   called 9 times: once at claim, once per emitted event inside
   `record_event_membership` (7), once at completion. Each call reloads every
   stream's run snapshot and re-hashes both canonical JSONs — for this run,
   ~7 × (snapshot reads + 2 SHA-256 over ~1 KB). Cost at MVP scale:
   microseconds per event; invisible in the 3.2 s wall.
2. **Observation loads are 2× the observation count.** The evaluation phase
   performs exactly one `load_outcome_observation` per task (idempotence
   pre-check — 77 measured, one per task); the completion gate performs
   another load per (eligible member × selection) through
   `complete_evaluation_if_ready` (77 more, via the un-instrumented
   `evaluation_runs` import path — verified statically). N+1 shape, 2×
   multiplier.
3. **Scheduler stop-poll churn is the largest count.** `should_stop` fired
   **1,285 times** for a 300-minute run (per candle per stream plus per
   chunk). In the direct path that is a no-op; in the queued path each call is
   `stopped()` → fresh connection checkout + job row read, versus only ~20
   connection checkouts for the entire rest of the run. At the accepted
   ceiling (a few months of M1) this reaches tens of thousands of checkouts
   per run — still seconds of overhead against minutes of execution.

## Invariant table (what each repeated check defends)

| Repeated work | Defends | Safe amortization |
| --- | --- | --- |
| Per-event `read_evaluation_run` re-hash | Snapshot tampering/rogue writes during execution; owner status/token | Re-verify hashes at claim and completion; per event keep only the cheap status/token row check |
| Per-task idempotence load | Immutable-recalculation conflict detection | Keep as is — it is the correctness mechanism |
| Gate per-member observation loads | Completion gate honesty (every required observation exists and is attributed) | One set-based query (LEFT JOIN / NOT EXISTS over required semantic keys), preserving the same failure on mismatch |
| Per-candle `stopped()` poll | Prompt cancellation, lease fencing | Cache the job-row read for ~0.5–1 s, re-check immediately after each commit and before each batch |

## Recommendation (ranked)

1. **Defer everything at current scale** — measured overheads do not justify
   touching code whose verification story prior reviews relied on.
2. **With SCRUM-102**: make the completion gate set-based (single query for
   missing/mis-attributed required observations). At 10k-event runs the
   current gate issues ~220k indexed point loads; a set-based form is a small,
   testable change with identical failure semantics.
3. **When queued runs exceed ~1 trading month**: time-based stop-poll caching
   per the invariant table.
4. Per-event re-hash amortization: last; only if profiling after the above
   still shows it.

## Verification limits

Single measurement run (variance not characterized); SQLite only (PostgreSQL
per-checkout cost is higher, strengthening only recommendation 3); gate loads
counted statically rather than instrumented. No tracked files modified by the
reviewer beyond this verdict.
