# Jira acceptance ownership reconciliation — 2026-09-27

## Scope and decision

This follow-up to [the five-Story implementation review](2026-09-26-five-story-implementation-review.md) reconciled the acceptance boundaries of SCRUM-54, SCRUM-78, SCRUM-105, SCRUM-112 and SCRUM-122 with the implementation sequence. It did not remove an MVP behavior simply because its owning runtime did not exist yet. Jira descriptions received dated superseding sections while retaining their original text and history; receiving Stories received explicit integration checks and explanatory comments.

| Foundation Story | Accepted within the Story | Later integration owner | Jira status after review |
| --- | --- | --- | --- |
| [SCRUM-54](https://mihailtrunov.atlassian.net/browse/SCRUM-54) | Canonical provider-independent Instrument/Bar value contracts, UTC normalization, validation and deterministic round-trips | SCRUM-56 provider conversion; SCRUM-124 duplicate/conflict and immutable publication; SCRUM-71 calendar resolution | Done |
| [SCRUM-78](https://mihailtrunov.atlassian.net/browse/SCRUM-78) | PatternDefinition schema/version contract, validation, coexistence and expressive representative fixtures | SCRUM-79/80 lifecycle and multi-version execution; SCRUM-95 event lineage; SCRUM-83/84/85 final detector definitions | Done |
| [SCRUM-105](https://mihailtrunov.atlassian.net/browse/SCRUM-105) | Immutable detection/evaluation config schemas, detection default resolution and snapshot storage contract | SCRUM-61/87 replay binding; SCRUM-95/123 evaluation binding; SCRUM-119/120 outcome/segment defaults; SCRUM-106 hashes | Done |
| [SCRUM-112](https://mihailtrunov.atlassian.net/browse/SCRUM-112) | Secure structured logging, private rotation, current entrypoint/snapshot integration and output independence | SCRUM-61/95 real run logs; SCRUM-123 worker log context and persisted failure summaries | Done |
| [SCRUM-122](https://mihailtrunov.atlassian.net/browse/SCRUM-122) | Local platform, diagnostics, developer workflow, CI and non-research installation smoke | SCRUM-61/87/88 interactive analytical smoke; SCRUM-80 detector runtime; SCRUM-95/123 autonomous evaluation smoke; SCRUM-124 backup/restore | In Progress |

SCRUM-61, SCRUM-80, SCRUM-87, SCRUM-95 and SCRUM-123 now explicitly carry the transferred run/runtime checks. SCRUM-56 and SCRUM-124 already had provider-conversion and dataset-policy criteria, so those were not rewritten. A working heartbeat process is not a job scheduler, and viewing demo bars is not a detector/evaluation run.

## Evidence and remaining limits

The local `pnpm run ci` check passed after the final scoped fixes: 58 Python tests passed, one PostgreSQL integration test was skipped without its database URL, and backend lint/typecheck plus frontend lint/typecheck/test/build passed. Focused domain, PatternDefinition and snapshot tests also passed. Three small implementation-evidence commits were kept separate:

- `5b04d64` — SCRUM-78 representative lifecycle/schema fixture shapes.
- `e69e7d3` — SCRUM-54 canonical Instrument round-trip and precision validation.
- `b4a6a91` — SCRUM-112 logging-level/output-independence fixture.

These commits and the earlier Story-gap commits were local and not pushed when the Jira transitions were made. No new remote GitHub Actions run was observed. SCRUM-122 therefore remains open; it must not be closed on the strength of local CI or installation smoke alone. The actual replay, detector-event, evaluation-job and full backup/restore paths remain work for their named Stories.
