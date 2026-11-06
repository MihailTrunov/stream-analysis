# SCRUM-123 scheduler implementation review

This is implementation/self-review evidence, not an independent Astra review. The two preceding Astra findings were fixed and pushed separately in `0d13e71`; [their verification](2026-11-06-astra-autonomous-fixes.md) remains distinct.

Independent follow-up: [Astra high review of ee66bc7](2026-11-06-astra-scheduler-review.md) found three P2 issues requiring fixes: standalone loading heartbeat expiry, a PostgreSQL orphan-recovery heartbeat race, and invalid chunk-size claims. The separate full Python run on the final commit passed **1,123 tests (20 PostgreSQL-dependent skips)**, closing the earlier final-UUID verification gap. The original implementation evidence below is retained; it does not supersede those findings.

## Delivered behavior

The default evaluation service now consumes a durable serial queue rather than only writing a heartbeat. Jobs pin the existing immutable EvaluationRun inputs, can be reordered or cancelled before starting, and expose durable status/progress/failure summaries through backend APIs. The queue starts paused and requires explicit start. Active cancellation pauses progression and preserves completed chunks; stale-worker recovery fences old ownership and leaves the next job waiting. No detector checkpoint automatically resumes.

Job and analytical-run leases are claimed in one transaction, with queue-row serialization and unique active slots. A standalone explicit worker cannot bypass ownership of a queued evaluation. Heartbeats run independently of candle commits. PostgreSQL recovery locks and reevaluates its stale-heartbeat predicate after waiting, avoiding false expiry when a concurrent heartbeat commits. Completed analytical output with a lost final job receipt is reconciled without rerunning it, while progression remains paused.

SQLite write transactions use BEGIN IMMEDIATE to avoid read-to-write upgrade races between the executor, controls and heartbeat thread. PostgreSQL uses ordinary row/transaction locking. Migration `20261106_19` retains job history, forbids changing job/input links and refuses destructive downgrade when jobs exist. Existing latest-head assertions now track that migration.

## Acceptance evidence

The focused suite passed **15 tests with 2 PostgreSQL-dependent skips**. It exercises real detector output, durable queue ordering/reorder/remove, frozen inputs, token fencing, cancellation with partial results, independently progressing heartbeats, orphan recovery, completed-receipt recovery, atomic API preflight, readable failure status after manifest corruption, and responsive interactive chart stepping during queued execution.

Subprocess checks exercise the actual default worker with both successful output and corrupt-source failure summaries/structured job-run logs. A separate crash test exits the worker after its ninth committed source minute, then verifies that restart retains the candidate/context/population evidence and cursor, marks the interrupted run/job FAILED, and does not start the next waiting job. Subprocess startup allowance is bounded at 60 seconds; diagnostic execution ruled out a persistent queue-lock stall after shorter local startup timeouts.

Both PostgreSQL scheduler tests passed independently. They cover competing worker claims, actual queued execution, immutable links/retention and a coordinated heartbeat-versus-recovery row-lock race. The combined PostgreSQL-targeted CI selection passed **21 checks** against a fresh owned database, with scheduler fixtures isolated into disposable schemas.

Full Python verification passed **1,119 tests (20 PostgreSQL-dependent skips)**. Final inspection then found a textual-UUID ownership edge case: uppercase/compact IDs could miss the queued-run lookup. Two reproducing cases failed before canonicalizing evaluation IDs at executor/enqueue entry. After that narrow correction, the complete affected executor/scheduler selection passed **32 tests (4 PostgreSQL-dependent skips)**, including both guarded queue IDs and successful nonqueued explicit runs using equivalent UUID formats. The full suite was not repeated after that final normalization; its broader evidence is supplemented by this focused run.

Browser verification passed **51 cases initially**, and all **5 failed cases passed on isolated rerun**. Initial failures involved page-creation/assertion timeouts and a timing-sensitive live viewport assertion; no browser implementation was changed to force passing results. The combined PostgreSQL-targeted selection passed 21 checks as recorded above. Ruff, mypy (108 source files) and diff checks passed. These are author verification results; a new independent Astra verdict is not implied.

## Delivery boundary

Current queued execution supports detector-only plans; selecting unavailable downstream execution is rejected explicitly. Production outcome algorithms remain SCRUM-97–100. Workspace Runs and results-tab presentation remain SCRUM-138/139; the new APIs supply their queue operations. Existing standalone nonqueued runs remain supported. Implementation tests use offline immutable datasets and scratch storage; no installed database was migrated and no provider was contacted.
