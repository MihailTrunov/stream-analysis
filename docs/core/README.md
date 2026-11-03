# Core project knowledge

This directory is the canonical repository source for the project's core product and architecture knowledge.

## Canonical documents

- [Product Scope](./product-scope.md)
- [Core Domain Model](./core-domain-model.md)
- [MVP Scope & Boundaries](./mvp-scope.md)
- [Technical Architecture & Engineering Principles](./technical-architecture.md)
- [Chart Workspace](./chart-workspace.md) — approved next-delivery workspace,
  live/history modes, analytical timeframes and results-review requirements.

## Source-of-truth policy

As of 2026-09-27, these Markdown files supersede the corresponding Google Docs as the canonical editable specifications.

The Google Docs remain historical/migration copies and should not be treated as authoritative after this migration unless a later repository commit explicitly says otherwise.

Material changes to product scope, domain semantics, MVP boundaries, or technical architecture should be committed here and reconciled with Jira when they affect implementation contracts.

`chart-workspace.md` records the approved November 2026 next-delivery decisions
and takes precedence over conflicting original MVP-only wording for that scope.
Its [delivery map](../plans/2026-11-03-chart-workspace-delivery.md) records Jira
ownership, prerequisites and outstanding implementation contracts.

`CONTEXT.md` remains the concise implementation-context record. These core documents provide the fuller specification behind it.
