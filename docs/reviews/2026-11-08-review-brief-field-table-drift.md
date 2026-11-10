# Review brief: context field-table drift risk

Status: reviewed
Reviewed by: [Claude field-table-drift review](2026-11-08-claude-field-table-drift-review.md) — consistent today; drift guard proposed (P3-grade latent risk)
Verdict: `2026-11-08-claude-field-table-drift-review.md`
Priority: medium — a latent maintainability risk: two parallel declarations of
"which detection-time component fields exist" will drift when component
versions change.

## Scope

Horizontal review of current master. The two sources of truth:

- `src/market_analysis/application/event_sets.py:15-55` — `NATIVE_FIELDS`,
  `COMPONENT_FIELDS` and `field_types()`: the predicate-approval table that
  decides which event/context paths an EventSet may filter on.
- `src/market_analysis/detection/context_snapshots.py` — the capture-side v1
  inclusion list (`COMPONENT_FIELDS`) and serialization rules that decide what
  X actually contains.
- `src/market_analysis/config/component_registry.py` — registered component
  identities feeding both.

## Contracts to review against

- [SCRUM-119](../plans/2026-11-06-scrum-119-detection-context-design.md) —
  `COMPONENT_FIELDS` is the explicit v1 inclusion list; new component versions
  or changed field meaning require an explicit context-schema decision.
- [SCRUM-95](../plans/2026-11-06-scrum-95-evaluation-models.md) — predicates
  read "only approved event/X fields"; per-detector context paths validate
  against the full copied detector set.

## Areas to probe (hypotheses, possibly unfounded)

1. **Consistency today.** Enumerate both tables and verify: every
  predicate-approved component field is actually captured in X with a
  compatible type; every approved `optional_components.<family>` availability
  path corresponds to a capture-side optional family (including the
  `swing_point` special case in `field_types`). Report any current mismatch.
2. **Drift scenario.** Walk through what happens when a new component version
  adds or renames a captured field: does any existing check fail loudly
   (context-schema version guard, field-types construction, test), or does the
  predicate table silently lag the capture schema — making a legitimately
  captured field unfilterable, or worse, an approved path resolve to
  MISSING at runtime and quietly exclude events?
3. **Proposed contract test.** Specify a regression that derives the
  predicate-approval table's component paths from the capture schema (or
  asserts the subset relation and type agreement) and fails when either side
  changes without the other. Keep it independent of any one detector's config.
4. **Documentation pointer.** Recommend where the single authoritative list
  should live (capture module, with `event_sets` consuming it) if consolidation
  is warranted.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
  Disposable SQLite; the analysis is primarily static.
- A "tables are consistent and a test is unnecessary" conclusion with
  reasoning is a valid outcome.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_evaluation_runs.py tests/unit/test_outcomes.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict-format document: current consistency result, the drift scenario
walkthrough, and the proposed contract test (or explicit reasoning that none is
needed). Link back to this brief.
