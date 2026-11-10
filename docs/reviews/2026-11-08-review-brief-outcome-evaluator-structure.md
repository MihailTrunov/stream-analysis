# Review brief: evaluate_outcome structure and decomposition

Status: unassigned
Reviewed by: —
Expected verdict: `2026-11-XX-astra-outcome-evaluator-structure-review.md`
Priority: low — quality-scoped review of the single hottest shared function in
the outcome framework; no defect is presumed.

## Scope

Horizontal review of current master. Primary file:

- `src/market_analysis/application/outcome_evaluation.py:80-301` —
  `evaluate_outcome`: lineage verification, plan/selection resolution, window
  construction (prospective and retrospective), event bounding, algorithm
  invocation, result validation, observation persistence and idempotent
  recalculation, all inline.

Related: `OutcomeInput`/`OutcomeResult` contracts (`:51-77`), the four
algorithm families in `forward_return.py`, `excursions.py`,
`time_to_threshold.py`, `structural_outcomes.py`.

## Contracts to review against

- [SCRUM-120 framework](../plans/2026-11-06-scrum-120-outcome-framework.md) —
  the leakage boundary: callbacks receive only the declared window, eligible
  event references and a detached context copy.
- `docs/core/technical-architecture.md` — OutcomeDefinition contract and
  result-type/ambiguity semantics.
- [SCRUM-97..100 plan docs](../plans) — the family-specific semantics layered
  on the shared evaluator.

## Areas to probe (hypotheses, possibly unfounded)

1. **Decomposition proposal.** Sketch a refactor into explicit phases —
   validate lineage → resolve selection → build window → bound events →
   execute → validate result → persist — each independently callable and
   testable, with behavior provably unchanged (existing suites must pass
   unmodified). Assess whether the phases have clean data interfaces or
   share mutable intermediate state that makes splitting risky.
2. **Hidden coupling and fragility.** `reference_price` read via
   `snapshot.values["bar"]["close"]` casts; the `early` short-circuit
   interleaved with window construction; the retrospective branch's separate
   reference/index logic. Identify points where a future outcome family could
   silently misuse the shared window math.
3. **Family workarounds.** Check whether any registered family works around a
   framework limitation (for example structural outcomes pre-bounding events
   outside the evaluator) that should instead be surfaced as framework
   capability.
4. **Recommendation only.** If decomposition is warranted, produce a refactor
   plan (phases, tests to add, invariants to pin) for a separate story. Code
   changes in this review are acceptable only if trivially safe.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes
  beyond any trivially-safe edit explicitly justified. Disposable SQLite.
- The current function is well tested and contract-conformant; the question is
  maintainability as families accrete, not correctness. A "leave as is"
  recommendation with reasoning is a valid outcome.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_outcomes.py tests/unit/test_forward_return.py \
  tests/unit/test_excursions.py tests/unit/test_time_to_threshold.py \
  tests/unit/test_structural_outcomes.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict-format document with the decomposition assessment, fragility list,
and either a refactor plan or an explicit leave-as-is recommendation. Link back
to this brief.
