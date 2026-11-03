# Chart workspace delivery and Jira reconciliation

## Implementation checkpoint — 2026-11-03

The owner directed merging to `master` and continuing without feature branches.
The initial chart UI and review fixes are merged and pushed. The recommended
four-task increment now has resolved candle contracts, deterministic aggregation,
independent local replay/history sessions and integrated detector/pattern panels;
see [the delivery evidence](2026-11-03-multitimeframe-session-delivery.md).

Full provider live integration, retained range fetching and API-restart replay
reconstruction remain follow-up work. The reconciliation record below preserves
the earlier backlog baseline rather than declaring all new stories Done.

Reconciled: 2026-11-03. Authorized by the owner after the UI analysis; x-high
reasoning requested for document/ticket drift resolution. The sections below record
the original pending-work baseline; current progress is stated above.

## Authority and scope

The authoritative interaction contract is [Chart Workspace](../core/chart-workspace.md).
The [discussion record](2026-11-01-chart-workspace-ui-analysis.md) preserves the
accepted decisions. The [story contracts](2026-11-03-chart-workspace-story-contracts.md)
contain the detailed acceptance criteria also entered in Jira.

The earlier one-minute research/replay MVP remains implementation history.
This next delivery includes the agreed local live and multi-timeframe workspace;
it does not mark the old MVP or new delivery complete. Production alerting,
order execution, hosted infrastructure and automated composite confirmation
rules are outside the agreed change.

## Existing versus reconciled contracts

| Existing state/wording | Reconciled next-delivery behavior |
| --- | --- |
| SCRUM-51 UI epic; SCRUM-87–94 and SCRUM-125–126 In Review | Preserve existing implementation evidence/status. New workspace/UI work is SCRUM-132–135 and SCRUM-138–139; visual and integration acceptance is SCRUM-140. |
| SCRUM-115 titled Post-MVP live analysis, To Do | Rename to Live Market Analysis — Chart Workspace; local streamed monitoring is in the agreed next-delivery scope, still unimplemented. SCRUM-137 implements its feed/session integration. |
| One active walkthrough, ephemeral API session | Tabs restore workspace identity; backend isolates analysis sessions; connected hidden live tabs process without rendering; replay pauses on loss of focus. Full application-restart replay recovery remains a contract decision. |
| All analytical computation M1; higher timeframes display-only | M1 remains canonical source resolution. Selected analytical timeframes process their own completed aggregated bars. Chart timeframe remains independent. SCRUM-127 gates exact semantics; SCRUM-129/130 implement them. |
| UTC chart/picker; seek-to-time overloaded | Default Europe/London presentation; canonical time remains UTC. Single picker separates viewport-only Move to from fresh paused Start replay from. Backend analytical seek is not silently repurposed. |
| Raw event timeline and automatic selection inspector | Occurrence projections in Developing/Active/Completed, latest transition first; preserve chronological source events. Selection focuses; info icon separately previews/pins evidence. |
| Explicit historical imports/selectable immutable revisions only | Shared range coverage and incremental fetch/read-through; retain source lineage and pinned run inputs. SCRUM-128/136 extend existing storage rather than replacing used revisions. |
| Historical versus future live feed; no persistence cadence | Save finalized source candles on close, reuse across tabs, package durable records hourly. Feed finalization/reconciliation and crash-safe publication need explicit design. |
| SCRUM-95–104,107,109,110,119,120,123 remain To Do | Reuse these backend contracts; append required timeframe/lineage/parity amendments. SCRUM-138/139 provide new workspace UI, not a duplicate evaluation engine. |
| Existing intraday same-day outcome policy | Preserved. Higher-timeframe horizon units and 1D applicability must be explicitly resolved in SCRUM-127/120, not inferred from new chart options. |

Existing Jira descriptions are preserved and receive dated superseding amendments;
status, assignee and sprint are not changed. Original implementation Stories do
not become Done merely because requirements have been reconciled. Repository
Markdown now supersedes historical Google Docs references for this scope.

Amended issues: SCRUM-51, SCRUM-52, SCRUM-115, SCRUM-87–94, SCRUM-125–126,
SCRUM-95–96, SCRUM-120, SCRUM-107, SCRUM-104, SCRUM-109–110 and SCRUM-123.
The UI and live epics are now titled Interactive Chart Workspace & Research
Interface and Live Market Analysis — Chart Workspace respectively.

## Backlog map

| Issue | Work | Parent epic | Wave | Hard prerequisites |
| --- | --- | --- | --- | --- |
| [SCRUM-127](https://mihailtrunov.atlassian.net/browse/SCRUM-127) | Specify multi-timeframe candle, initialization and outcome contracts | SCRUM-49 | 0 | None |
| [SCRUM-128](https://mihailtrunov.atlassian.net/browse/SCRUM-128) | Load and retain shared instrument history by requested chart range | SCRUM-47 | 1 | SCRUM-56, SCRUM-57, SCRUM-59, SCRUM-60, SCRUM-124 |
| [SCRUM-129](https://mihailtrunov.atlassian.net/browse/SCRUM-129) | Build deterministic analytical timeframes from canonical one-minute history | SCRUM-49 | 1 | SCRUM-127 |
| [SCRUM-130](https://mihailtrunov.atlassian.net/browse/SCRUM-130) | Orchestrate independent multi-timeframe detector sessions for chart tabs | SCRUM-50 | 2 | SCRUM-129 |
| [SCRUM-131](https://mihailtrunov.atlassian.net/browse/SCRUM-131) | Calculate historical-window detections manually or automatically with causal state | SCRUM-50 | 3 | SCRUM-128, SCRUM-130 |
| [SCRUM-132](https://mihailtrunov.atlassian.net/browse/SCRUM-132) | Restructure the chart workspace with tabs and a compact right panel | SCRUM-51 | 1 | None |
| [SCRUM-133](https://mihailtrunov.atlassian.net/browse/SCRUM-133) | Unify history navigation and causal replay entry in the chart workspace | SCRUM-51 | 3 | SCRUM-128, SCRUM-130, SCRUM-132 |
| [SCRUM-134](https://mihailtrunov.atlassian.net/browse/SCRUM-134) | Expose per-timeframe detector policies and explicit configuration Apply | SCRUM-51 | 3 | SCRUM-130, SCRUM-131, SCRUM-132 |
| [SCRUM-135](https://mihailtrunov.atlassian.net/browse/SCRUM-135) | Present as-of pattern groups with timeframe badges and separate evidence previews | SCRUM-51 | 3 | SCRUM-131, SCRUM-132 |
| [SCRUM-136](https://mihailtrunov.atlassian.net/browse/SCRUM-136) | Persist completed live candles on close and publish hourly history segments | SCRUM-47 | 2 | SCRUM-128 |
| [SCRUM-137](https://mihailtrunov.atlassian.net/browse/SCRUM-137) | Connect live chart sessions through shared validated provider ingestion | SCRUM-115 | 3 | SCRUM-127, SCRUM-130, SCRUM-136 |
| [SCRUM-138](https://mihailtrunov.atlassian.net/browse/SCRUM-138) | Manage background evaluation creation and queue from a workspace Runs view | SCRUM-51 | 4 | SCRUM-132, SCRUM-130, SCRUM-95, SCRUM-96, SCRUM-120, SCRUM-123 |
| [SCRUM-139](https://mihailtrunov.atlassian.net/browse/SCRUM-139) | Explore completed evaluation events and outcomes in dedicated result tabs | SCRUM-51 | 5 | SCRUM-138, SCRUM-135, SCRUM-102, SCRUM-103, SCRUM-107 |
| [SCRUM-140](https://mihailtrunov.atlassian.net/browse/SCRUM-140) | Verify chart-workspace integration, recovery and supported-browser usability | SCRUM-53 | 6 | SCRUM-133, SCRUM-134, SCRUM-135, SCRUM-137, SCRUM-139, SCRUM-104, SCRUM-109, SCRUM-110 |

Existing analytical dependencies are retained. Wave numbers are ordering guidance,
not a promise that all items in a wave are independent or assigned to a sprint.
The actual Blocks links define hard prerequisite direction.

Additional hard prerequisites reconcile existing backend contracts:

- SCRUM-127 blocks SCRUM-120's higher-timeframe outcome contract.
- SCRUM-130 blocks SCRUM-95/96/107 for session-input modelling, execution and lineage.
- SCRUM-129 and SCRUM-131 block SCRUM-109's expanded leakage coverage.
- SCRUM-131 and SCRUM-137 block SCRUM-110's expanded cross-mode parity coverage.

Relates links connect new work to original UI/chart/manual-review/storage
implementations rather than duplicating their implementation history.

## Delivery sequence

1. **Contracts and layout foundation:** SCRUM-127 resolves aggregation, initialization,
   ordering, calendar/live finalization and outcome-unit questions. SCRUM-132 may
   start independently using existing M1 fixtures and the existing chart library.
2. **Shared data and candle construction:** SCRUM-128 range coverage/acquisition and
   SCRUM-129 versioned aggregation. No provider-specific objects enter the core.
3. **Analysis and durability:** SCRUM-130 session orchestration and SCRUM-136 durable
   live retention/read-through. Extend existing run/configuration contracts before
   consuming them from higher-timeframe features.
4. **Interactive integration:** SCRUM-131 historical calculation, SCRUM-133 navigation,
   SCRUM-134 settings, SCRUM-135 pattern projection and SCRUM-137 live connections,
   each after its listed prerequisites.
5. **Autonomous research:** complete existing SCRUM-95/96/97–103/107/119/120/123 backend
   work with amended contracts; deliver SCRUM-138 Runs and SCRUM-139 review tabs.
6. **Acceptance:** extend existing SCRUM-104/109/110 parity/leakage suites; SCRUM-140
   verifies integration, recovery, supported-browser usability and bounded work.

This is a dependency sequence for the full agreed target. It does not quietly
remove live monitoring or other approved capabilities from scope.

## Unresolved implementation contracts

Candle anchors, partial/missing buckets, availability and initialization now follow
the [implementation contract](2026-11-03-multitimeframe-session-implementation.md).
Remaining integration work includes newly verified later-date calendar coverage,
provider finalization/catch-up/conflict execution, immutable recent-history
publication, continuous indexed range fetching and bounded year-scale work.
Existing outcome v1 M1 units and same-day eligibility are preserved; alternate
units/overnight eligibility require a separately versioned outcome definition.

Full API-restart cursor recovery remains unspecified implementation work. The
local increment implements paused entry, fresh-identity Reset, source-complete
chart-candle Step, playback cadence after accepted chart reads, restored focused
tabs, explicit UK ambiguous/nonexistent-input rejection and evidence dismissal.
Provider-connected tab restoration and measured larger-history/browser budgets
remain their integration acceptance stories. None of these permits changing pinned
inputs or bypassing causal boundaries.

## Verification of this reconciliation

Verified by reading all 35 affected issues back from Jira: 14 new issues have
the expected title/type/parent, acceptance text and To Do status; 21 amended
issues retain their original description content and status plus the expected
dated amendment. Verified all 61 newly created links (48 Blocks, 13 Relates),
including their direction. The full current graph of 94 issues from SCRUM-47
onward has 225 unique Blocks edges and no cycle.

Local verification covers Markdown references, decision coverage and whitespace.
Application tests are not claimed for this documentation/backlog-only task.

