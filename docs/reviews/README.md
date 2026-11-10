# Review artifacts and conventions

This directory holds the review-process record for repository work: scoped
review briefs, independent verdicts, implementation self-reviews, and fix
follow-ups. Approved product and architecture contracts live in
[../core](../core/README.md); delivery designs live in [../plans](../plans).

## Artifact kinds

| Kind | Name pattern | Author | Role |
| --- | --- | --- | --- |
| Review brief | `YYYY-MM-DD-review-brief-<topic>.md` | story owner / root agent | **Input** for an independent reviewer: scope, contracts, probe hypotheses, constraints |
| Independent verdict | `YYYY-MM-DD-astra-<topic>-review.md` | independent reviewer (GPT-6 Astra) | **Output**: verdict, confirmed findings, independent verification |
| Implementation review | `YYYY-MM-DD-scrum-XX-<topic>-review.md` | implementing author | Self-review evidence; must label itself as not an independent review |
| Fix record | `YYYY-MM-DD-<topic>-fix(es).md` | implementing author | Verification that review findings were resolved; not a new verdict |

## Rules

1. A brief is an input, never findings. Its hypotheses are probes that may be
   unfounded; the reviewer verifies independently and reports honestly when
   nothing reproduces. A no-finding verdict is a valid outcome and is not a
   failed review.
2. Every verdict is self-contained. It restates its reviewed commit range or
   files whether or not a brief exists, so verdicts remain auditable if a
   brief is later removed.
3. Cross-link both ways. A brief's `Reviewed by:` line links its verdict once
   it exists; the verdict's opening references its brief.
4. Briefs carry `Status:` (`unassigned` → `in review` → `reviewed`) and name
   the verdict file they expect.
5. Independent reviewers make no tracked-file, commit, Jira or
   installed-database changes. They use disposable SQLite/PostgreSQL only and
   never access installed providers. Reproduction scripts stay outside the
   repository (for example under `/tmp`) unless the implementation promotes
   them to committed regressions.
6. Findings are rated P1/P2/P3 as in existing Astra reviews; verdicts name
   locations as `path:line` and record the verification commands used.

## Open briefs

| Brief | Priority | Focus | Status |
| --- | --- | --- | --- |
| [SCRUM-101 segmentation](2026-11-08-review-brief-scrum-101-segmentation.md) | high | solution soundness | [reviewed (Claude)](2026-11-08-claude-segmentation-review.md) |
| [Threshold opening-order fix](2026-11-08-review-brief-threshold-opening-fix.md) | high | solution soundness | [reviewed (Claude)](2026-11-08-claude-threshold-opening-fix-review.md) |
| [Outcome-phase durability](2026-11-08-review-brief-outcome-phase-durability.md) | high | architecture, soundness | [reviewed (Claude)](2026-11-08-claude-outcome-phase-durability-review.md) |
| [Structural inventory re-derivation](2026-11-08-review-brief-structural-inventory.md) | medium | architecture, soundness | [reviewed (Claude)](2026-11-08-claude-structural-inventory-review.md) |
| [Transaction boundaries](2026-11-08-review-brief-transaction-boundaries.md) | high | soundness | [reviewed (Claude)](2026-11-08-claude-transaction-boundaries-review.md) — one P2 |
| [Scale ceiling](2026-11-08-review-brief-scale-ceiling.md) | medium | architecture | unassigned |
| [Outcome evaluator structure](2026-11-08-review-brief-outcome-evaluator-structure.md) | low | code structure quality | unassigned |
| [Hot-path re-verification cost](2026-11-08-review-brief-hot-path-cost.md) | medium | code structure quality | unassigned |
| [Context field-table drift](2026-11-08-review-brief-field-table-drift.md) | medium | code structure quality | [reviewed (Claude)](2026-11-08-claude-field-table-drift-review.md) |
| [Evaluation doc structure](2026-11-08-review-brief-doc-structure.md) | low | code structure quality | unassigned |

Priorities reflect the 2026-11-08 stage assessment: unreviewed deliveries
first, then cross-cutting soundness risks, then structure quality. Reassess
after each verdict.
