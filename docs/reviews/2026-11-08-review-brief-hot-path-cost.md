# Review brief: evaluation hot-path re-verification cost

Status: reviewed
Reviewed by: [Claude hot-path-cost review](2026-11-08-claude-hot-path-cost-review.md) — no findings; amortization deferred, gate query change recommended with SCRUM-102
Verdict: `2026-11-08-claude-hot-path-cost-review.md`
Priority: medium — correctness is not in question; the concern is quadratic
per-run overhead that SCRUM-102's larger populations will amplify.

## Scope

Horizontal review of current master. Primary sites:

- `src/market_analysis/application/evaluation_runs.py:310-369` —
  `record_event_membership` calls `read_evaluation_run` per emitted event,
  which reloads every stream's run snapshot and re-hashes both canonical JSONs
  (`:280-307`).
- `src/market_analysis/application/evaluation_runs.py:372-431` —
  `complete_evaluation_if_ready` loads observations one-by-one per eligible
  member per selection.
- `src/market_analysis/application/evaluation_scheduler.py:509-518` — the
  scheduler's `stopped()` opens a fresh connection and reads the job row per
  call; the executor invokes `should_stop` per candle in dispatch
  (`autonomous_evaluation.py:223`) and per chunk.
- `src/market_analysis/application/evaluation_outcomes.py:47-124` — `_tasks`
  loads the full pattern instance per member per pass.

## Contracts to review against

- [SCRUM-95](../plans/2026-11-06-scrum-95-evaluation-models.md) — membership
  requires "the actual persisted source event and its matching snapshot";
  owner checks gate progress writes.
- [SCRUM-96](../plans/2026-11-06-scrum-96-autonomous-execution.md) — the
  durable active slot and token guards.
- `docs/core/technical-architecture.md` — "Do not add infrastructure solely
  for anticipated future scale without an observed MVP need."

## Areas to probe (hypotheses, possibly unfounded)

1. **Quantify.** For a representative run (e.g., 5 days M1, two streams,
   several hundred events): count `read_evaluation_run` invocations,
   SHA-256 hashes over config/plan JSON, per-event instance loads, and
   stop-poll connection opens. Report measured or tightly estimated overhead
   per phase.
2. **Amortization proposals that preserve semantics.** Candidates: cache the
   verified evaluation record per run with ownership re-check on each write
   (the token check already re-reads status — verify what the per-event
   re-verification adds beyond it); time-based stop-poll caching with an
   immediate check after each commit; set-based completion-gate queries
   (COUNT/EXISTS joins instead of per-member loads).
3. **Ownership/fencing invariants first.** For each proposal, state which
   invariant the repeated verification defends (defense in depth against
   snapshot mutation vs pure cost) and confirm the amortized version still
   fails loudly on ownership loss, snapshot tampering and concurrent claim.
4. **Recommendation.** Rank changes by measured benefit; recommend only
   changes with a demonstrated hotspot. Explicitly acceptable outcome: "costs
   are negligible at MVP scale; defer all amortization."

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
  Disposable SQLite; PostgreSQL only if dialect-specific query behavior is
  probed.
- Any proposed caching must not weaken the immutability verification story
  that previous Astra reviews relied on.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_evaluation_runs.py tests/unit/test_autonomous_evaluation.py \
  tests/unit/test_evaluation_scheduler.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict-format document with measurements, the invariant table, and a ranked
recommendation (possibly "defer"). Link back to this brief.
