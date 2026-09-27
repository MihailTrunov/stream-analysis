# Sprint 1 foundation acceptance closure — 2026-09-27

Scope: close the remaining acceptance-evidence gaps in SCRUM-55, SCRUM-68,
SCRUM-79 and SCRUM-106. SCRUM-122 stays In Progress pending the user's fresh
checkout of the local Compose stack.

## Boundary

Three approaches were considered: implement the later replay/indicator/swing
features immediately; change Jira acceptance wording without new executable
proof; or add focused contract proof now and explicitly assign real integration
to the later implementation Stories. Use the third approach. It preserves the
foundation-first sequencing without silently dropping MVP integration checks.

SCRUM-55 gets a provider-independent, typed analytical consumer fixture that
walks paginated `HistoricalDataSource` results with the in-memory adapter. Real
historical replay binding and provider conversion remain with SCRUM-61 and
SCRUM-56 respectively. SCRUM-68 gets representative EMA-like, ATR-like and
structural component fixtures using the one-completed-bar interface, including
reset and causal ordering. Actual formulas and shared-pipeline integration
remain with SCRUM-69, SCRUM-70 and SCRUM-74.

SCRUM-79 gets the missing same-bar `CANDIDATE -> CONFIRMED -> ACTIVE` release
fixture without adding pattern-specific price rules. SCRUM-106 gets direct hash
mutation and invariance tests for detection parameters, outcome identity and
configuration, reordered selections/parameters, and rejected presentation
settings; golden byte fixtures remain unchanged unless a real defect is found.

## Jira and verification

Append dated, superseding acceptance-ownership notes to SCRUM-55 and SCRUM-68
and explicit receiving checks to SCRUM-61, SCRUM-69, SCRUM-70 and SCRUM-74.
Preserve all earlier Jira text. Do not change SCRUM-122 or any detector formula.
Run focused and full tests, Ruff, mypy, and the remote CI workflow. Commit each
Story's change with a body describing implementation, review and fixes, then
push. Mark a Story Done only after its evidence and Jira handoff are verified.
