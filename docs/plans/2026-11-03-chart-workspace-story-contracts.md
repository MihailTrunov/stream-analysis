# Chart workspace Jira story contracts

Reconciled 2026-11-03. These acceptance contracts were entered into Jira;
implementation is pending. See the [delivery map](2026-11-03-chart-workspace-delivery.md)
for sequencing and reconciliation of existing work.

## SCRUM-127 — Specify multi-timeframe candle, initialization and outcome contracts

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-127)

Type: Task; parent: SCRUM-49; wave: 0.
Hard prerequisites: None; layout can use existing M1 fixtures, or this is the prerequisite specification task.

Acceptance criteria:

- Resolve and version anchors for 5m/15m/30m/1h/4h/1D built from canonical M1; specify source timestamp versus completed-candle availability, session boundaries, DST and partial/gapped bucket behavior. Europe/London display must not determine analytical alignment.
- Define deterministic per-timeframe warm-up/initialization origins, cross-timeframe tie-breaking and applicable detector/timeframe combinations. Resolve the current M1-only TrendLeg qualification constraint explicitly; do not silently weaken validation or copy unvalidated defaults.
- Define outcome horizon units and trading-day policy for higher-timeframe events before extending SCRUM-120 and numeric outcomes. Adding 1D does not automatically permit overnight intraday outcomes.
- Specify provider completed-minute finalization and live/history conflict policy, calendar-version coverage extension, historical result identity and run input pinning. Record unanswered owner decisions explicitly before affected implementation.
- Publish hand-reviewed boundary examples and golden expectations suitable for deterministic fixtures; preserve the existing M1 semantics/version compatibility.

Verification: Review examples covering candle close availability, DST, market breaks, missing minutes, same-time transitions and higher-timeframe horizons. This is a specification prerequisite, not an implementation claim.

## SCRUM-128 — Load and retain shared instrument history by requested chart range

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-128)

Type: Story; parent: SCRUM-47; wave: 1.
Hard prerequisites: SCRUM-56, SCRUM-57, SCRUM-59, SCRUM-60, SCRUM-124

Acceptance criteria:

- Provide bounded range/coverage APIs for US30/DAX history, loading local validated source minutes first and requesting only missing provider ranges through background acquisition. Share retention and equivalent fetches across tabs; reuse history after restart.
- Support continuous leftward navigation up to real provider availability. Distinguish pending, unavailable, failed and unexpected-gap states; never synthesize missing source minutes or require all history in browser memory.
- Preserve provider/account/calendar/normalization provenance, checksums and immutable revision identity. New fetches or provider corrections cannot silently replace inputs pinned by existing replay/evaluation runs.
- Define source-range publication/read behavior using existing staging and Parquet storage; partial unsafe imports must not become research-grade run inputs. Validate current date coverage with versioned calendars; reject unsupported dates explicitly.

Verification: Range overlap/deduplication, local reuse, restart reuse, provider failure, unavailable history, gap reporting, immutable-input preservation and bounded read/fetch tests.

## SCRUM-129 — Build deterministic analytical timeframes from canonical one-minute history

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-129)

Type: Story; parent: SCRUM-49; wave: 1.
Hard prerequisites: SCRUM-127

Acceptance criteria:

- Implement the approved aggregation contract for 1m/5m/15m/30m/1h/4h/1D using shared M1 history, not independent provider candles.
- Produce analytical bars only after the corresponding bucket closes; keep any forming display candle separate from completed detector input.
- Apply the versioned alignment/calendar and partial/gap rules identically in live, exploration, replay and evaluation. Include aggregation/timeframe/calendar versions in reproducible identity.
- Preserve deterministic OHLC precision and source provenance. UK display timezone changes must not change bars or analytical boundaries.

Verification: Hand-calculated OHLC fixtures, all supported intervals, DST/trading-day/session boundaries, gap/partial buckets, incremental-versus-historical parity and no premature higher-timeframe close.

## SCRUM-130 — Orchestrate independent multi-timeframe detector sessions for chart tabs

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-130)

Type: Story; parent: SCRUM-50; wave: 2.
Hard prerequisites: SCRUM-129

Acceptance criteria:

- Represent tab-local analysis sessions independently of immutable runs; explicitly configure analytical timeframes with their own enabled detectors, supported parameters and Live/Manual/Always on policies.
- Live processes completed live candles and permits manual history; Manual performs requested historical calculation without automatic live processing; Always on also automatically analyses browsed history. Replay runs every selected enabled detector on candle advance regardless of policy.
- Keep display timeframe and result filters separate from analytical enablement; process all intervening M1 bars and each analytical close without skipped events. Define deterministic cross-stream ordering per the prerequisite contract.
- Use the same existing market-state/detector pipeline in all modes. Backend orchestration survives inactive-tab rendering suspension while historical replay pauses on focus loss.
- Explicit Apply reinitializes affected live analysis with sufficient prior history, preserving old detection attribution. Stop replay before editing. Parameter revisions do not require detector version changes; rule/formula changes do.
- Pin per-timeframe resolved configuration/source/aggregation lineage; isolate differing tab settings and reject unsupported detector/timeframe configurations.

Verification: Tab isolation, policy matrix, display-only/filter-only invariance, multi-timeframe close scheduling, Apply/version attribution, initialization and processing without mounted chart tests.

## SCRUM-131 — Calculate historical-window detections manually or automatically with causal state

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-131)

Type: Story; parent: SCRUM-50; wave: 3.
Hard prerequisites: SCRUM-128, SCRUM-130

Acceptance criteria:

- Provide Calculate current view for every enabled detector/timeframe in all operation modes, without creating an autonomous evaluation or requiring progressive playback.
- Always on loads historical detections when the displayed window changes; Live and Manual require explicit calculation. Bound scheduling and reuse compatible results; obsolete requests cannot overwrite the current viewport.
- Process sufficient earlier history from the approved deterministic initialization anchor. Reuse identity includes source revision, analytical timeframe, aggregation/calendar, detector/configuration/build and initialization semantics.
- Return coverage/progress/errors distinctly from an analysed window with no patterns. Derive Developing/Active state as of the viewed right edge, including earlier-started occurrences still ongoing there; future terminal transitions cannot leak backward.
- Keep historical detections distinct from evaluation OutcomeObservations. Source/data/config changes invalidate reuse appropriately, without modifying pinned results.

Verification: Overlapping-window equivalence under a common origin, scrolling request races, manual/auto matrix, no-results versus not-loaded, pre-window active occurrence, future-state projection and reuse invalidation.

## SCRUM-132 — Restructure the chart workspace with tabs and a compact right panel

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-132)

Type: Story; parent: SCRUM-51; wave: 1.
Hard prerequisites: None; layout can use existing M1 fixtures, or this is the prerequisite specification task.

Acceptance criteria:

- Give the TradingView chart most of the screen and show it before replay. Provide readable price/time axes, crosshair, panning and explicit zoom in/out; retain attribution and useful existing overlays/review flows.
- Overlay instrument and chart-timeframe selectors at the chart top left. Keep mode, shared date/time navigation and playback controls in the right panel above switchable Patterns and Detector settings views; Patterns is default.
- Support multiple named/identified instrument/mode analysis tabs; restore previously open tabs and active selection. Reconnect only the focused restored live tab, connecting other restored live tabs on first focus.
- Keep connected hidden live sessions processing while suspending their chart/DOM rendering. Pause historical replay on focus loss and leave paused on return.
- Use clearly labelled shared per-tab Europe/London display time for chart/picker, observing GMT/BST; keep canonical UTC and analytical alignment unchanged.
- Retain diagnostics as accessible support UI without consuming the main chart workspace. Preserve audited annotations and honest synthetic dataset labelling.

Verification: Chrome/Mac layout and axis visibility, pre-replay chart, zoom, tab restore/focus, no inactive rendering, continued background processing, timezone and existing annotation accessibility smoke checks.

## SCRUM-133 — Unify history navigation and causal replay entry in the chart workspace

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-133)

Type: Story; parent: SCRUM-51; wave: 3.
Hard prerequisites: SCRUM-128, SCRUM-130, SCRUM-132

Acceptance criteria:

- Use one date/time picker with Move to and Start replay from. Move to navigates/fetches history; during active replay it changes only the viewport within revealed history, never detector state or analytical cursor.
- Start replay from creates a fresh run and opens paused with useful preceding candles and initialized indicators. Default end is latest completed source candle available at launch, fixed for the run; advanced controls allow earlier end.
- Step completes the next displayed chart candle while processing every intervening M1 bar and analytical close. Preserve speed and next-event controls without future-event pre-scanning.
- Pause retains cursor/run; Stop ends replay and returns to exploration at the same chart position/configuration. Endpoint stops progression without switching to live.
- Live panning/older-pattern selection stops viewport following while data/detection continue. Return to latest resumes following. Respect replay cursor even if another tab/store contains later data.
- Resolve active-run replacement, reset, speed units and ambiguous/nonexistent UK datetime input semantics explicitly before implementing those interactions.

Verification: Single-picker flows, paused initialization and pre-start context, fixed endpoint, five-minute Step equivalence to five M1 updates, Move to cursor invariance, live follow suspension and cross-tab future-data isolation.

## SCRUM-134 — Expose per-timeframe detector policies and explicit configuration Apply

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-134)

Type: Story; parent: SCRUM-51; wave: 3.
Hard prerequisites: SCRUM-130, SCRUM-131, SCRUM-132

Acceptance criteria:

- In Detector settings, explicitly select analytical timeframes and independently configure each registered detector's enablement, supported parameters and Live/Manual/Always on mode per tab/timeframe.
- Provide Calculate current view in every mode and distinguish display visibility/filtering from actual analytical enablement.
- Validate settings against backend schema; use explicit Apply rather than applying each keystroke. Stop historical replay first, including paused replay; live Apply performs attributable reinitialization.
- Preserve immutable preset revisions, prior result configuration attribution and detector version information. Do not expose arbitrary rule/formula editing or silently enable a detector on other timeframes.
- Show loading/initialization/waiting-for-structure/errors and unsupported timeframe combinations clearly.

Verification: Policy/enablement UI-to-API mapping, per-tab/timeframe isolation, backend validation, immutable revisions, manual calculation, Apply/stop rule and no formula/version confusion.

## SCRUM-135 — Present as-of pattern groups with timeframe badges and separate evidence previews

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-135)

Type: Story; parent: SCRUM-51; wave: 3.
Hard prerequisites: SCRUM-131, SCRUM-132

Acceptance criteria:

- Stack Developing, Active and Completed sections vertically; each independently sorts by latest observable transition detection time, newest first. Rows represent occurrences with retained transition history.
- Reversal/continuation confirm directly into Completed; compression confirms into Active and releases into Completed. Distinguish confirmed/completed/invalidated/expired terminal labels without redefining detector lifecycle.
- Scope records to displayed period across configured analytical timeframes; reflect state at viewed right edge capped by replay cursor, including earlier-started ongoing occurrences. Not loaded is distinct from no results.
- Filters affect visibility only. Badge records from a different analytical timeframe; selection keeps chart timeframe without a switch prompt. Default annotations use chart timeframe plus a selected cross-timeframe occurrence.
- Selection focuses/highlights and pauses historical playback without changing cursor; live processing continues. Separate info icon previews floating evidence on hover, pins on click and supports keyboard focus without replacing lists.
- Use recorded structured rationale/timing/lineage and preserve manual validation workflows.

Verification: Detector-specific grouping, as-of versus later state, independent sorting, viewport overlap, badge/filter mapping, off-timeframe focus, no switch prompt, hover/pin/focus and existing review regression.

## SCRUM-136 — Persist completed live candles on close and publish hourly history segments

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-136)

Type: Story; parent: SCRUM-47; wave: 2.
Hard prerequisites: SCRUM-128

Acceptance criteria:

- Durably retain every completed canonical source minute promptly on close, sharing ingestion/storage across tabs using the same feed. Forming display updates do not require tick archival.
- Make recent durable records available for history reads before hourly packaging; historical fetches retain validated durable batches as they arrive.
- Package already durable records into larger immutable history segments hourly initially. Crash/retry or packaging failure cannot lose acknowledged candles, duplicate/conflict silently, or alter pinned revisions.
- Retain source/provenance, calendar, normalization, checksums and coverage; define transaction/atomic publication and recovery protocol by adapting existing durable batch/storage machinery.
- Extend backup/restore coverage to recent durable records and their metadata as well as packaged history; preserve existing used dataset revisions.

Verification: Durability-before-packaging, forced crash/retry, duplicate/conflict, recent-history read-through, hourly idempotence, pinned-input preservation and backup/restore integrity.

## SCRUM-137 — Connect live chart sessions through shared validated provider ingestion

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-137)

Type: Story; parent: SCRUM-115; wave: 3.
Hard prerequisites: SCRUM-127, SCRUM-130, SCRUM-136

Acceptance criteria:

- Implement provider-neutral live ingress for initial US30/DAX through canonical normalization, validation, verified calendar and provenance. Confirm actual provider capabilities and completed-minute finalization; no separate detector formulas.
- Update forming chart candles as supported, but dispatch detectors/persistence only for finalized completed source/analytical candles under the agreed contract.
- Share feed acquisition across tabs while preserving independent analysis settings. Restore only focused tab's connection initially; first focus reconnects other restored tabs. Connected unfocused tabs keep data/detection without DOM updates.
- Expose connection/staleness/failure status and use explicit catch-up/gap/conflict rules on reconnection; do not process incomplete history as uninterrupted valid state.
- Keep credentials server-side. Retain local single-user operation and the existing no-order-execution/no-production-alert boundary.

Verification: Fake provider stream, close-boundary behavior, deduplicated subscriptions, lazy reconnect, hidden processing, gaps/catch-up/conflicts and captured-live replay parity; provider-specific validation is separate from routine CI.

## SCRUM-138 — Manage background evaluation creation and queue from a workspace Runs view

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-138)

Type: Story; parent: SCRUM-51; wave: 4.
Hard prerequisites: SCRUM-132, SCRUM-130, SCRUM-95, SCRUM-96, SCRUM-120, SCRUM-123

Acceptance criteria:

- Provide workspace-level Runs, independent of chart tabs, to create evaluations and inspect durable queued/running/completed/cancelled/failed status and progress.
- Offer Use current tab settings or Choose saved preset, then historical period and versioned outcome selection. Copy resolved per-timeframe inputs into immutable run snapshots; later tab/preset edits cannot mutate jobs.
- Use existing scheduler preflight and serial queue; retain cancel/pause/reorder/restart contracts and explicit unavailable backend states.
- Background execution never advances or takes over the active chart; show status without browser-side detector/evaluation logic.
- Opening completed output creates a separate review tab under the results-view story.

Verification: Copied-versus-preset inputs, immutable multi-timeframe snapshots, preflight failures, serial queue operations, status polling/recovery and chart independence.

## SCRUM-139 — Explore completed evaluation events and outcomes in dedicated result tabs

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-139)

Type: Story; parent: SCRUM-51; wave: 5.
Hard prerequisites: SCRUM-138, SCRUM-135, SCRUM-102, SCRUM-103, SCRUM-107

Acceptance criteria:

- Open completed evaluation output in a new analysis tab, preserving existing live/history tabs and original run/source/configuration lineage.
- Initial right-panel views are Patterns, Results summary and read-only Run settings. Include recorded OutcomeObservations in separate pattern info panels.
- Show occurrence counts, outcome statistics, eligible sample sizes and unavailable/ambiguous status counts; do not convert missing results to zero or claim detector profitability.
- Chart can show later outcome-window bars in labelled completed-result review while original detection evidence remains causal. Do not recompute or rewrite events/results using current tab settings.
- Preserve manual annotation/audit and lineage export/drill-down contracts. Mark this initial layout for refinement after practical use.

Verification: Multiple result-tab isolation, read-only originals, aggregate-to-event/outcome mapping, status counts, labelled outcome-window access, config immutability and annotation/export regression.

## SCRUM-140 — Verify chart-workspace integration, recovery and supported-browser usability

[Jira issue](https://mihailtrunov.atlassian.net/browse/SCRUM-140)

Type: Task; parent: SCRUM-53; wave: 6.
Hard prerequisites: SCRUM-133, SCRUM-134, SCRUM-135, SCRUM-137, SCRUM-139, SCRUM-104, SCRUM-109, SCRUM-110

Acceptance criteria:

- Run deterministic end-to-end fixtures through workspace tabs, bounded historical loading, manual/automatic exploration, causal multi-timeframe replay, fake live ingress and completed results review.
- Verify shared persistence/recent read-through/restart recovery, lazy reconnection, historical focus pause and ongoing hidden live detection without hidden DOM/chart renders.
- Verify readable axes, zoom, chart prominence, UK picker/timezone, pattern filtering/grouping/badges/evidence interaction and no regressions in audited manual review.
- Measure Chrome/Mac rendering and request bounds under representative several-tab/timeframe workloads; retain replay/event determinism under speed/Step/display changes.
- Attach local supported-browser visual acceptance evidence plus relevant CI results. Keep initial synthetic demos clearly non-research-grade and do not mark earlier In Review tickets Done without acceptance.

Verification: Integration/recovery/browser evidence here complements existing SCRUM-104/109/110 analytical parity/leakage suites; do not duplicate their contracts or require real provider credentials in CI.

