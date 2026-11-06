# Astra review: durable evaluation scheduler

Reviewer: GPT-6 Astra, **high reasoning**, independent review agent.

Reviewed commit: `ee66bc76ffde76843ba7e28fc504f095ac723bdd` on master; primary range `0d13e71..ee66bc7`. The review also verified integration of the preceding canonical-settings and SQLite transaction fixes in `0d13e71`. No implementation fixes are included here.

Implementation follow-up: [fixes and regression evidence for all three findings](2026-11-06-astra-scheduler-fixes.md). The original independent verdict below is preserved; follow-up verification is not a new Astra review.

## Verdict

**Three confirmed P2 findings. Address them before building SCRUM-97/138 on this scheduler.** The existing focused tests and final-commit full suite pass, but additional probes reproduce lifecycle and recovery defects outside their coverage.

## P2 — Healthy standalone executions expire during long source loading

Locations: `src/market_analysis/application/evaluation_scheduler.py:362`, `src/market_analysis/application/worker.py:199`, and `src/market_analysis/application/autonomous_evaluation.py:130`.

New orphan recovery expires standalone RUNNING executions after 60 seconds. The explicit `--run-id` worker supplies an initial service heartbeat and per-commit callback, but no periodic run heartbeat covers source loading or a long chunk. `DatasetStore.load_sequence` authenticates and materializes the complete source before processing begins, so sufficiently slow source IO can exceed the lease timeout while the worker remains alive.

The reviewer held a live explicit executor inside source loading for **61 real seconds**, without changing stored timestamps or stopping its process. Its heartbeat remained unchanged. Ordinary `recover_stale(now())` then marked it FAILED and paused the queue; releasing the still-live executor caused subsequent ownership checks to fail. The probe passed in **62.67 seconds**, asserting this incorrect behavior.

This is introduced by applying the new expiry mechanism to the existing per-commit-only explicit path. It is separate from the PostgreSQL race below: this reproduction uses SQLite and a fresh row read. Explicit execution needs an ownership-aware periodic heartbeat throughout loading and processing.

## P2 — PostgreSQL orphan recovery fails a run after a fresh heartbeat commits

Locations: `src/market_analysis/application/evaluation_scheduler.py:362` and `:376`.

Queued-job recovery uses FOR UPDATE and rechecks heartbeat freshness after waiting. Orphan explicit-run selection does not lock its rows. It can select an old committed heartbeat while an explicit progress transaction owns the lifecycle row. Its subsequent `fail_evaluation` waits for that transaction, then updates using status/token predicates without rechecking whether the heartbeat is still stale.

A real PostgreSQL16 probe coordinated this sequence:

1. A healthy explicit progress update holds a new heartbeat uncommitted.
2. Recovery selects the previously committed stale row and starts its blocked failure update.
3. The healthy transaction commits its fresh heartbeat and cursor.
4. Recovery still returns `1` and marks the run FAILED, retaining cursor `0`.

Locking and rechecking the orphan stale predicate is required independently of adding periodic explicit heartbeats. Fresh heartbeat updates cannot otherwise protect a valid owner from the stale selection.

## P2 — Invalid chunk size consumes a queued job and strands ownership

Locations: `src/market_analysis/application/evaluation_scheduler.py:477` and `:533`, with validation at `src/market_analysis/application/autonomous_evaluation.py:92`.

CLI integer parsing accepts chunk sizes such as 0, negative integers and values above 2,000. The scheduler commits ownership of the job and analytical run before passing the value to the executor. The executor rejects it before entering its lifecycle termination handling. The scheduler catches the error, then finalization raises `active analytical execution cannot be finalized as a job` because the run is still RUNNING.

The reviewer reproduced `run_scheduler_once(..., chunk_size=0)` leaving **both job and run RUNNING with no failure summary**. Later lease recovery permanently fails that unused evaluation. Validate worker arguments before claiming a job, and retain explicit lifecycle handling for failures after ownership is committed.

## Independent verification

**11 existing focused tests passed**, including both PostgreSQL scheduler cases, both PostgreSQL executor cases, SQLite first-event rollback failure/cancellation, three queued UUID bypass guards and two equivalent nonqueued UUID formats:

```sh
STREAM_ANALYSIS_TEST_DATABASE_URL='postgresql+psycopg://review_test:review_test@127.0.0.1:15439/review_test' \
  .venv/bin/pytest -q tests/unit/test_evaluation_scheduler.py \
  tests/unit/test_autonomous_evaluation.py \
  -k 'postgres or first_event_chunk or equivalent_uuid or bypass'
```

**Two curated raw/default-settings four-mode parity tests passed:**

```sh
.venv/bin/pytest -q tests/regression/test_cross_mode_parity.py -k curated
```

Five scratch probes were executed separately. Three assert the observed defects above, so their passing reproduces bugs rather than establishes correct behavior. Two positive PostgreSQL probes verify both enqueue-versus-explicit-claim orderings: explicit-first correctly rejects enqueue, and enqueue-first correctly rejects manual execution.

Reproduction scripts, commands and environment notes are retained temporarily in `/private/tmp/astra-scheduler-independent/README.md` and `test_review.py`. From the repository directory, with a disposable PostgreSQL service configured:

```sh
STREAM_ANALYSIS_TEST_DATABASE_URL='postgresql+psycopg://review_test:review_test@127.0.0.1:15439/review_test' \
  .venv/bin/pytest --rootdir=/private/tmp/astra-scheduler-independent \
  --confcutdir=/private/tmp/astra-scheduler-independent -c /dev/null -q -s \
  /private/tmp/astra-scheduler-independent/test_review.py
```

The database URL above uses synthetic credentials for the owned review container. The probes use isolated schemas and scratch Parquet/SQLite. No installed database or provider was used. The reviewer made no tracked edits, commits or Jira changes.

## Final-commit full-suite verification and limits

The root agent separately ran the complete Python suite on the clean final implementation commit `ee66bc7`, including its last UUID normalization: **1,123 passed, 20 PostgreSQL-dependent skipped**. This closes the earlier full-suite verification gap. PostgreSQL cases listed above were independently exercised by Astra against the disposable PostgreSQL16 service. The commit-range diff check passed.

The prior Decimal/canonical-configuration and SQLite SAVEPOINT findings remain fixed. No additional confirmed issue was found in reviewed token omission, immutable input links/history, migration retention, queue bounds, cancellation or completion paths. Browser checks were not repeated during this independent review; earlier browser evidence remains separate implementation verification. Production outcomes and Runs/results UI remain deferred scope, not findings.
