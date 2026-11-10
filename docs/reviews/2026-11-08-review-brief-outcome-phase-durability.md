# Review brief: outcome-phase durability and progress model

Status: reviewed
Reviewed by: [Claude outcome-phase-durability review](2026-11-08-claude-outcome-phase-durability-review.md) — no findings; durable outcome progress deferred
Verdict: `2026-11-08-claude-outcome-phase-durability-review.md`
Priority: high — the outcome phase is the subtlest crash/recovery surface in
the evaluation stage, and SCRUM-102 will make large outcome populations
attractive.

## Scope

Horizontal review of current master (no single commit; the phase accreted
across SCRUM-97..101). Primary files:

- `src/market_analysis/application/evaluation_outcomes.py` — task derivation,
  batch loop, lease fence.
- `src/market_analysis/application/autonomous_evaluation.py:305-322` —
  outcome-phase invocation and completion ordering.
- `src/market_analysis/persistence/evaluations.py` — `advance_evaluation`
  semantics and lifecycle row updates.
- `src/market_analysis/application/evaluation_scheduler.py` — stale recovery
  and queue interplay with a mid-outcome worker.

## Contracts to review against

- [SCRUM-96 executor](../plans/2026-11-06-scrum-96-autonomous-execution.md)
  and [SCRUM-97](../plans/2026-11-06-scrum-97-forward-return.md) — lease held
  through bounded outcome batches, owner checks at commit, honest statuses.
- [SCRUM-123 scheduler](../plans/2026-11-06-scrum-123-evaluation-scheduler.md)
  — recovery never resumes silently; progression stays paused.
- [Sequence note](../plans/2026-11-06-autonomous-evaluation-sequence.md) —
  detection completion and all-stage completion are distinct.

## Areas to probe (hypotheses, possibly unfounded)

1. **No durable outcome-stage progress.** The detection phase commits progress
   per chunk; the outcome phase derives pending work from EventSet rows on
   every start (`_tasks`, `evaluation_outcomes.py:47-124`) and has no persisted
   stage cursor. Trace every crash point (source load, inventory pass, each
   batch, final `finish_detection`/`complete_evaluation_if_ready` ordering at
   `autonomous_evaluation.py:319-322`) and verify the resulting state is always
   honest: FAILED/CANCELLED with partial output, or restart-from-scratch on a
   fresh run only. Confirm no path re-runs outcomes on a DETECTION_COMPLETED
   run or resurrects a failed one.
2. **The `advance_evaluation` lease fence.** During outcome batches the fence
   reuses `advance_evaluation` with the pinned final cursor and
   `selected_end` (`evaluation_outcomes.py:168-175`). Verify it is purely an
   ownership/heartbeat fence: no progress reporter or API view may present it
   as outcome progress. Check the fence's interaction with
   `recover_stale`'s stale-predicate locking on both dialects.
3. **Batch atomicity and ownership loss.** OUTCOME_BATCH_SIZE=64 with
   `check_stop` per task. Verify: ownership loss or cancellation mid-batch
   rolls back the whole batch with zero partial observations (an earlier Astra
   probe verified one such path — re-verify current code, including the
   structural-inventory paths).
4. **Task-derivation correctness.** `_tasks` paginates by `emission_order`
   with limit(1) per member and loads the full pattern instance per event.
   Verify `after` monotonicity, that eligible members can never be skipped or
   duplicated across batches, and that unregistered selections remain pending
   (blocking completion) rather than silently skipped.
5. **Decision request (not a defect).** Given SCRUM-102, should outcome-stage
   progress become durable (a persisted batch cursor and stage field), or is
   restart-with-idempotence acceptable at MVP scale? The verdict should
   recommend, with costs, not implement.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
- Disposable SQLite, and disposable PostgreSQL via
  `STREAM_ANALYSIS_TEST_DATABASE_URL` for recovery/fencing races. No installed
  database, provider credentials or live services.
- Hypotheses may be unfounded; a no-finding verdict with traced reasoning is
  valid and useful.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_autonomous_evaluation.py \
  tests/unit/test_evaluation_scheduler.py tests/unit/test_outcomes.py \
  tests/unit/test_forward_return.py tests/unit/test_excursions.py \
  tests/unit/test_time_to_threshold.py tests/unit/test_structural_outcomes.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict in the existing Astra format plus an explicit answer to probe 5.
Findings carry `path:line` locations, reproduction evidence and verification
limits. Link back to this brief.
