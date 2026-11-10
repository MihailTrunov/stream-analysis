# Claude review: SQLite transaction-boundary audit

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-transaction-boundaries.md).

Reviewed state: committed HEAD `c4e65bd`. Site lists were re-derived with
`git grep` at HEAD, not trusted from the brief. Note: the working tree contains
**unrelated concurrent live/provider work** (11 modified files plus a new
`provider_analysis.py`, timestamps before this review); those uncommitted
changes were neither audited nor touched, and all line references below are
HEAD (`git show HEAD:` for modified files).

Implementation follow-up: the
[boundary migration fix](2026-11-10-transaction-boundaries-fix.md) migrates all
classified sites (including the sites the concurrent provider work added when
committed) to `atomic_transaction`, adds the architecture rule and per-family
regressions. This review's findings are retained as history.

Process note: an Opus-tier subagent nearly finished the static classification
before being interrupted by a provider usage limit; the main-session reviewer
re-derived the classification independently and performed the reproduction.

## Verdict

**One confirmed P2.** The legacy-SQLite SAVEPOINT-leak mechanism fixed for the
evaluation paths in `0d13e71` is still present on numerous other write
boundaries: plain `engine.begin()` transactions whose first SQL write flows
through a repository `connection.begin_nested()` savepoint. A production code
path was reproduced leaking on a migrated scratch SQLite database. SQLite
serves development, tests and the browser smoke runs (production Compose runs
PostgreSQL, which is unaffected), so the rating is P2 per the brief's
deployment-reality rule — with the note that smoke and chart-session paths do
run on SQLite.

## Confirmed mechanism

`register_instrument` under plain `engine.begin()` on a database migrated to
head via Alembic: after the savepoint block releases and the outer transaction
is rolled back by a later exception, the inserted instrument row **persists**.
Control case: a plain `INSERT` (DML starts the real transaction) under the same
shape rolls back cleanly. The leak is therefore specific to the
savepoint-as-first-write pattern, matching the prior Astra P2's diagnosis
exactly.

Probe: `probe2_partial_commit.py` (scratch, not committed; retained under the
reviewer's session temp directory). Output:

```text
1: instruments rows after outer rollback: 1 -> LEAK (partial commit)
3: control rows after outer rollback: 0 -> clean rollback (expected)
```

Case 2 (annotation create) was not separately reproduced because the annotation
target validator requires a real persisted run/event; its callee has the same
first-write-savepoint shape as the confirmed case and is classified
vulnerable-class below.

## Classification (HEAD sites)

| Boundary | First write reaches nested savepoint? | Class |
| --- | --- | --- |
| `api/app.py:874` POST /imports | `register_instrument` (`market_data.py:235`) | **Confirmed** (callee reproduced verbatim) |
| `api/app.py:910` history request | `register_instrument`, then `history_ranges.py:101` | Confirmed-class |
| `api/app.py:979` history navigation plan | `register_instrument`, then `history_ranges.py:101` | Confirmed-class |
| `api/app.py:318` lifespan seeding | dataset publish (`dataset_store.py:305`) | Vulnerable-class |
| `api/app.py:702` annotation create | `validation_annotations.py:277` | Vulnerable-class |
| `api/app.py:810` annotation edit | `validation_annotations.py:539` | Vulnerable-class |
| `api/app.py:994` publish history input | dataset publish (`dataset_store.py:305`) | Vulnerable-class |
| `api/app.py:1050` import resume | `resume_import_job` (`import_jobs.py:402`) | Vulnerable-class |
| `analysis_sessions.py:379` session launch | `_lock_feed` (`live_retention.py:55`) | Vulnerable-class |
| `analysis_sessions.py:605` session step | `process_detector_candle` → `pattern_instances.py:417/688` | Vulnerable-class (chart-step leak) |
| `browser_replay.py:271` walkthrough launch | `create_replay_run` (`replay_run.py:126`) | Vulnerable-class |
| `browser_replay.py:367` step_visible | `replay_pipeline.py:499` step savepoint (incl. warm-up loop) | Vulnerable-class |
| `browser_replay.py:417/435/463/510` other actions | pipeline/event-store savepoints (not individually traced) | Likely-class |
| `historical_import.py:215` `_finalize` | dataset publish (`dataset_store.py:305`) | Vulnerable-class |
| `live_analysis.py:112` bootstrap plan | `history_ranges.py:101` via navigation | Vulnerable-class |
| `live_analysis.py:277` reconcile/advance | not individually traced | Likely-class |
| `live_analysis.py:507` publish captured input | dataset publish | Vulnerable-class |
| `live_ingress.py:131` feed register | `register_instrument` only; nothing after it in the transaction | Mechanism present, no failure window (benign in practice) |
| `live_retention.py:95` retain minute | `_lock_feed` (`live_retention.py:55`) first | Vulnerable-class |
| `live_retention.py:175` package hour | `_lock_feed` first | Vulnerable-class |
| `worker.py:158` schedule + claim | `history_ranges.py:101`, then `import_jobs.py:292` | Vulnerable-class |
| `worker.py:67/124/128/132/146` | direct UPDATE as first statement (real transaction begins) | Safe |
| `browser_replay.py:342` stop | direct lifecycle UPDATE first | Safe |
| `historical_import.py:287` page commit | `checkpoint_import_page` direct UPDATE first | Safe |
| `live_retention.py:252` fairness pre-update | direct UPDATE first (deliberate separate transaction) | Safe |
| Evaluation claim/chunk/dispatch/termination + evaluation API | wrapped in `atomic_transaction` (`transactions.py:10-18`) since `0d13e71` | Safe (prior fix verified) |

## Recommended correction

Smallest change preserving PostgreSQL behavior unchanged: adopt
`atomic_transaction` as the **only** sanctioned engine-level write boundary and
migrate the vulnerable-class and likely-class sites above to it. The helper
already no-ops on PostgreSQL and issues `BEGIN IMMEDIATE` on SQLite only when
legacy control has not begun a transaction. Two supporting hardenings:

1. Add an architecture/lint test forbidding `engine.begin()` outside
   `persistence/transactions.py`, mirroring the existing import-boundary
   architecture tests.
2. Add one regression per family (API metadata/annotations/imports, chart
   session step, live retention) in the shape of the committed evaluation
   first-event rollback regressions: first write succeeds, injected failure
   after the savepoint, assert zero partial state. The probe in this review
   supplies the pattern.

## Independent verification

```sh
.venv/bin/pytest -q   # combined evaluation baseline for this review batch: exit 0
```

Audit itself is static plus the scratch SQLite reproduction above (Alembic
migrated to head). No installed database, provider or provider credentials were
accessed; no tracked files were modified by the reviewer.

## Verification limits

- "Vulnerable-class" rows are established by the confirmed dialect mechanism
  plus matching call structure (first SQL write inside a savepoint, no prior
  DML); they were not each reproduced end-to-end.
- "Likely-class" rows were not individually traced and need confirmation during
  the fix.
- The uncommitted concurrent live/provider work in the working tree was out of
  scope; the fix should re-audit those files at their committed state.
