# Review brief: evaluation documentation structure

Status: reviewed
Reviewed by: [Claude doc-structure review](2026-11-08-claude-doc-structure-review.md) — split proposal produced; two isolation-exposed ambiguities flagged for owner decision
Verdict: `2026-11-08-claude-doc-structure-review.md`
Priority: low — pure reorganization review; no contract change is proposed.

## Scope

Three documents on current master:

- `docs/core/technical-architecture.md` — primarily the SCRUM-95 paragraph at
  `:329` ("Replay and Batch Orchestration"), which has accreted inline
  follow-ups for SCRUM-96, 123, 97, 98, 99, 100, 101 and the deferred
  102/138/139 scope into a single ~700-word block; secondarily the workspace
  amendment section's increment list (`:15-94`).
- `README.md` — the evaluation/queue/outcomes paragraphs (`:60-64`), dense
  run-on prose.
- `docs/core/README.md` — canonical-doc index.

## Contracts to review against

- `docs/core/README.md` source-of-truth policy: material changes are committed
  here; this brief must not change any statement's normative content.
- The linked plan docs themselves (`docs/plans/2026-11-06-*.md`,
  `2026-11-07-*.md`) — the per-story details the architecture section
  summarizes.

## Areas to probe (tasks, not defect hypotheses)

1. **Restructure the SCRUM-95 paragraph** into the document's existing
   amendment style: one short normative paragraph per story with its plan-doc
   link, preserving every current statement's meaning verbatim. No mergers,
   no deletions, no rewording that could change an interpretation — splits
   and links only.
2. **Restructure the README evaluation paragraphs** into bullet groups
   (queue controls, supported outcome families, segmentation, standalone
   execution), again content-preserving.
3. **Verify link integrity** after restructuring: every plan-doc reference in
   the affected sections resolves, and no orphaned statement loses its
   qualifying context (the risk of splitting is dropping a qualifier that
   scoped a neighboring sentence).
4. **Produce a proposed diff for approval**, not direct edits to the canonical
   documents. The diff should be reviewable statement-by-statement against
   the current text.

## Constraints for the reviewer

- Produce the proposal as a review artifact; make no tracked-file changes.
- If any statement appears ambiguous or contradictory when isolated — which
  restructuring tends to expose — flag it as a finding rather than silently
  choosing an interpretation.

## Verification baseline

```sh
# link check on the proposed diff is sufficient; no test gates apply
```

## Expected output

A verdict-format document containing the proposed restructuring (before/after
per section), the link-integrity check, and any ambiguity findings exposed by
isolation. Link back to this brief.
