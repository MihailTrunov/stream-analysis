# Claude review: outcome-phase durability and progress model

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-outcome-phase-durability.md).

Reviewed state: committed HEAD `c4e65bd` (the outcome phase spans SCRUM-97..101;
no commit-range audit applies). Unrelated concurrent live/provider edits in the
working tree were neither audited nor touched.

Process note: an Opus-tier subagent authored the crash-point probe scaffold
before being interrupted by a provider usage limit; the main-session reviewer
corrected two scenario-design errors in it (batch-size arithmetic and a
misspelled status), completed the run, and performed the code tracing.

## Verdict

**No findings.** Every traced crash point lands in an honest state; batch
atomicity, ownership fencing and restart semantics behave per the SCRUM-95/96/97/123
contracts. The probe-5 decision request is answered below: defer durable
outcome-stage progress; document the restart model instead.

## Probe results (disposable SQLite, migrated schemas, real executor)

Probe: `probe1.py` (scratch; retained under the reviewer's session temp
directory), driving `execute_evaluation` with production outcome definitions
over the seeded multi-hour dataset. Batch size forced to 8 for the
mid-phase-cancellation scenario (56 tasks otherwise fit one 64-task batch — the
scaffold's original scenarios silently tested the wrong thing).

- **A — cancel mid-outcome phase:** `should_stop` flips after the first
  committed outcome batch. Result: CANCELLED; exactly 8 of 56 observations
  retained (one whole batch); 48 mid-flight tasks rolled back; cursor and
  availability pinned at final detection values. Cancellation granularity is a
  whole batch, tasks within a batch are atomic, and the retained prefix is
  attributable.
- **B — algorithm failure mid-batch:** injected failure at task 10 of the
  first batch. Result: FAILED with `RuntimeError: forced outcome failure`
  recorded; **zero** observations persisted (nine successful task writes inside
  the failing batch rolled back with it).
- **C — crash after the last batch, before `finish_detection`:** FAILED with
  all 56 observations persisted; re-execution of the failed run rejected
  ("not available for a fresh execution"); a fresh run of the same request
  recomputes and completes, adding its own 56 observations (UUIDv5 observation
  identity keeps them distinct per run/event lineage).
- **D — DETECTION_COMPLETED with a pending unregistered selection:** run stays
  DETECTION_COMPLETED, `complete_evaluation_if_ready` returns False without
  mutating state, re-execution rejected, ownership released (`owner=None`,
  `active_slot=None`). Honest pending-outcome status per contract.
- **E — lease fence during outcome batches:** per-commit rows show
  `cursor_index` pinned at `execution_bar_count − 1` and `available_at` pinned
  at `selected_end` across the detection-final and outcome-batch commits, while
  `revision` and `heartbeat_at` advance monotonically. `advance_evaluation`
  (`evaluation_outcomes.py:168-175`) is confirmed to act purely as an
  ownership/heartbeat fence — it cannot be misread as outcome progress. (The
  probe's final assertion compared a tz-naive SQLite value against an aware
  manifest value; verified by inspection of the emitted tuples, which show the
  pinned values directly.)

## Trace summary against the brief's crash points

Source load and structural inventory precede outcome batches and hold no
ownership writes; a stop or failure there leaves the committed detection prefix
and marks CANCELLED/FAILED as in detection-phase handling
(`autonomous_evaluation.py:305-322`, `330-373`). `_tasks` pagination is keyed on
monotonic `emission_order` from immutable EventSet rows — committed membership
rows cannot change, so no member can be skipped or duplicated across batches;
unregistered selections yield no task and block completion by design. Scheduler
recovery interaction (stale leases fencing a mid-outcome worker) was verified
on PostgreSQL by the prior Astra reviews of `3a0400d` and unchanged since.

## Probe 5 — recommendation

**Defer durable outcome-stage progress.** Restart-with-idempotence is correct
(today: a failed run is terminal and a fresh run recomputes; observation
identity makes re-derivation safe), and the re-derivation cost is bounded by
membership reads plus per-event instance loads that are trivially fast at MVP
population sizes (the probe's 56-task phase completes in seconds). Adding a
persisted outcome cursor/stage field buys crash-resume the contract explicitly
does not promise ("no automatic detector checkpoint resume"), and every state
it would introduce needs its own fencing rules. Revisit only if SCRUM-102 makes
single-run outcome populations large enough that whole-phase re-execution after
a late crash becomes expensive; the trigger to watch is wall-time of the outcome
phase relative to the detection phase, not correctness. Record this decision in
the SCRUM-123 scheduler contract when convenient.

## Independent verification

```sh
.venv/bin/pytest -q tests/unit/test_autonomous_evaluation.py \
  tests/unit/test_evaluation_scheduler.py tests/unit/test_outcomes.py \
  tests/unit/test_forward_return.py tests/unit/test_excursions.py \
  tests/unit/test_time_to_threshold.py tests/unit/test_structural_outcomes.py
```

Passed within the combined evaluation baseline run executed for this review
batch (exit 0; PostgreSQL-dependent cases skipped).

## Verification limits

- Probes ran on disposable SQLite; PostgreSQL-specific recovery/fencing races
  were not re-executed here (previously verified independently for the
  scheduler fixes; code paths unchanged since).
- The scheduler-queue combination (worker death mid-outcome under real
  `recover_stale`) was traced, not timed live; scenario C models the
  equivalent state transition at the executor boundary.
