# Chart workspace UI analysis

Status: agreed decisions consolidated on 2026-11-03 into the authoritative
[Chart Workspace specification](../core/chart-workspace.md). This document is
the discussion record; use the canonical specification and
[Jira delivery map](2026-11-03-chart-workspace-delivery.md) for implementation.
Implementation has not started as part of this reconciliation.

## Confirmed requirements

- Support tabs for separate analysis sessions, including live monitoring of a
  particular instrument in one tab and historical exploration in another.
- On launch, restore the previously open analysis tabs and the active tab,
  including their instruments and modes. This replaces the earlier proposal
  to default launch to historical exploration.
- Reconnect only the focused restored tab on launch. Other restored tabs
  reconnect when first focused, rather than all connecting at startup.
- Once connected, live tabs stay connected and continue receiving data and
  evaluating detectors on completed candles when another tab is focused.
- Only the focused analysis tab updates its chart visualization and DOM.
  Inactive connected tabs continue data/detector processing without rendering;
  their displays catch up to current state when focused again. Suspending
  rendering must not interrupt detector processing or lose detected events.
- Historical replay automatically pauses when its tab loses focus, preserving
  its replay cursor. Returning to the tab leaves playback paused until the user
  explicitly presses Play.
- Pause freezes replay progression while retaining the run and cursor for
  resumption. Stop ends the replay and returns to historical exploration at
  the same chart position, preserving the tab's detector configuration. The
  chart stays visible. Starting another replay selects a starting point and
  creates a fresh run with sufficient preceding history.
- Step advances to the completion of the next displayed chart candle,
  processing every intervening canonical one-minute candle in sequence and
  evaluating enabled detectors whenever their analytical candles close.
  For example, a full five-minute chart step processes five one-minute candles;
  it must not skip intermediate detector updates or events.
- Use one date/time picker in the right panel with two actions: Move to and
  Start replay from. Move to navigates to the chosen point in history, loading
  preceding data as required; Start replay from uses the same chosen timestamp
  as the starting point for a fresh replay. This is the primary navigation and
  replay-entry control, replacing the suggested separate date inputs or
  right-click-first flow.
- During active replay, Move to only repositions the chart within already
  revealed history. It does not advance or rewind the replay cursor or change
  detector state, and it cannot expose candles after the replay cursor.
- Historical replay defaults to ending at the latest completed source candle
  available when it starts; pin that endpoint for the run. Advanced controls
  may specify an earlier end time. Replay stops progression at its endpoint
  without automatically switching to live monitoring.
- Start replay from initializes the fresh replay paused, with preceding
  context and initialized indicators visible. Play or Step explicitly begins
  advancement.
- Give the chart most of the available screen space.
- Overlay instrument selection and displayed chart timeframe selection in the
  chart's top-left corner. This supersedes placing those two controls in the
  right panel. Analytical timeframe configuration remains with detector settings.
- Chart and date/time picker share a per-tab display timezone setting,
  defaulting to UK time (Europe/London, observing GMT/BST). Label the timezone
  clearly. Changing display timezone does not change canonical UTC storage,
  analytical timestamps or candle bucket alignment. Handling of ambiguous or
  nonexistent picker times at daylight-saving transitions requires definition.
- Place historical/realtime mode selection, date/time navigation, playback
  controls, and other operational controls in the right panel.
- Display detected patterns in the right panel.
- The right panel has switchable Patterns and Detector settings views, with
  Patterns shown by default. Navigation and playback controls remain accessible
  above those views. Patterns contains the vertically stacked Developing,
  Active, and Completed sections; Detector settings contains analytical
  timeframe configuration, detector operation modes and parameters.
- Provide chart zoom in/out controls.
- Historical replay opens at its chosen start with a useful window of preceding
  candles and indicators initialized from sufficient prior data. Subsequent
  candles are revealed only as replay advances. Pre-start context does not
  expand the selected interval for research event inclusion.
- The instrument chart supports continuous leftward scrolling into the past,
  loading preceding periods from local history or fetching and displaying
  missing provider history as needed. Availability is bounded by actual source
  history; data must not be fabricated when history cannot be retrieved.
- Analytical timeframe selection determines the candles used by its detectors,
  independently of chart display grouping. Detector evaluation uses completed
  candles of the analytical timeframe.
- Detector selection/applicability is scoped to the analysis tab and timeframe;
  enabling a detector on one-minute analysis must not imply enabling it on
  five-minute or other timeframes.
- Provide a single view of detector signals from multiple timeframes so the
  researcher can inspect agreement or contradiction across detectors and
  timeframes.
- Select analytical timeframes explicitly per tab, with enabled detectors and
  parameters configured independently under each timeframe. Multiple selected
  analytical timeframes run within the same tab.
- Build higher-timeframe candles from the same retained canonical one-minute
  source history, rather than fetching independent provider candles for each
  timeframe. Use this source consistently for charting, live analysis,
  historical replay and autonomous evaluation. Bucket alignment, session
  boundaries and incomplete/gapped bucket handling remain to be specified.
- Initially support 1m, 5m, 15m, 30m, 1h, 4h and 1D independently for chart
  display and analytical timeframe configuration. Daily candles require an
  instrument trading-day boundary; exact alignment rules remain undecided.
- The displayed chart timeframe is independent of analytical timeframes;
  changing displayed candle grouping does not enable or disable analysis.
- Stack Developing, Active, and Completed pattern sections vertically in the
  right panel. These replace the earlier Current/Historical proposal.
- Developing contains candidates and other in-progress pre-confirmation states.
- Active contains confirmed ongoing states, such as compression while its
  defined active conditions persist.
- Completed contains occurrences whose detector lifecycle has ended. Reversal
  and continuation move directly from Developing to Completed at confirmation;
  confirmation does not imply an ongoing active state for these detectors.
  Compression moves from Developing to Active, then Completed on release.
- Completed is a UI section name, not a replacement for the detector's actual
  terminal state. Preserve visible distinctions between confirmed, completed,
  invalidated and expired outcomes.
- Each section has independent ordering, defaulting to recent first by the
  detection time of the occurrence's latest observable state transition, not
  its candidate start or underlying event time. A newly confirmed reversal
  therefore appears at the top of Completed even if its candidate began earlier.
- Selecting a pattern focuses and highlights it on the chart. During historical
  replay, selection pauses playback without advancing or rewinding the
  analytical cursor. During live monitoring, connection and detector processing
  continue.
- Pattern explanations are a separate action, exposed through an info icon on
  the pattern record, with a floating details panel. Hover previews the details;
  clicking pins the panel open so its evidence can be inspected and scrolled.
  Keyboard focus provides access to the same details.
  Selecting a pattern does not automatically open its explanation or replace
  the pattern lists. Exact dismissal behavior remains to be finalized.
- Pattern/detector hit records show a timeframe badge when their analytical
  timeframe differs from the displayed chart timeframe. Badge visibility is
  relative to the current chart timeframe.
- Chart pattern annotations default to the displayed chart timeframe. Selecting
  a pattern from another analytical timeframe additionally shows and highlights
  that occurrence on the current chart without changing its timeframe. The
  right panel continues listing records from all configured analytical
  timeframes by default.
- Developing, Active, and Completed show records from all configured analytical
  timeframes by default. Optional timeframe and detector filters affect record
  visibility only; they do not enable, disable, or reconfigure analysis.
- Pattern-panel scope follows the period displayed on the chart. Panning a live
  chart into history shows results for that historical window rather than
  continuing to display unrelated latest live signals. Live ingestion and
  detection continue in the background, with a clear status indication.
  Historical results respect detector operation modes; uncalculated coverage
  must be distinguished from a calculated period with no detected patterns.
- Developing and Active describe occurrence state as observable at the right
  edge of the displayed chart period, bounded by the replay cursor when replay
  is active. Include an occurrence that began before the visible window if it
  remains developing or active there. Do not use later stored transitions to
  relabel the occurrence prematurely; a compression released after the viewed
  period can still appear Active within it.
- Historical exploration supports calculating defined detectors on demand for
  the displayed chart period without starting a progressive replay or requiring
  a completed autonomous evaluation.
- Provide per-detector controls to load results for the current view and to
  enable automatic loading as the displayed data window changes. Configuration
  belongs to the detector's analytical timeframe within the tab, consistent
  with independent detector configuration across timeframes.

## Confirmed detector operation modes

Each detector/timeframe configuration has an enabled/disabled control and one
of three operation modes:

| Mode | Live monitoring | Historical exploration |
| --- | --- | --- |
| Live | Evaluate on completed candle close | Calculate current view on request |
| Manual | No automatic processing | Calculate current view on request |
| Always on | Evaluate on completed candle close | Calculate automatically as the viewed window changes |

Calculate current view remains available in every mode for enabled detectors.
During historical replay, all selected enabled detectors process advancing
candles regardless of their operation mode. Each analytical timeframe evaluates
only when its own candle closes; advancing the one-minute replay cursor does
not evaluate a higher-timeframe detector against an unfinished candle.

## Applying configurable settings

Supported detector/component parameters use an explicit Apply action; editing
a field does not immediately modify running analysis. A historical replay must
be stopped before changing its analysis settings. In live monitoring, applying
changes reinitializes the affected analysis using sufficient preceding history.
Earlier detections retain their original configuration attribution.

These controls change supported configurable parameters and create a new
configuration revision/snapshot, not a new detector version. Changes to detector
formulas or rules require a separately implemented and versioned detector;
arbitrary formula editing is not part of this settings UI.

## Historical exploration loading mechanics

Implementation proposals to refine:

- A bulk Calculate current view action can apply to selected detectors without
  changing their policies.
- Automatic analysis waits until navigation settles, reuses matching results,
  and cancels or ignores obsolete viewport requests. Indicate pending,
  completed, failed or unavailable analysis rather than implying that absence
  of loaded results means no patterns occurred.
- Scope requested event coverage to the visible time interval while processing
  sufficient earlier history to initialize each detector. The analytical
  initialization boundary must be defined for reproducibility; visible-range
  calculation must not assume arbitrary viewport starts yield identical state.
- Preserve timeframe/configuration/source lineage and distinguish these
  historical detection results from completed evaluations with outcomes.
- Selecting a hit preserves the displayed chart timeframe. Do not automatically
  switch timeframes or suggest switching to the hit's source timeframe; focus
  and inspect it within the current chart view.
- In live monitoring, panning into older history or selecting an older pattern
  stops automatic following of the newest candle and preserves the inspection
  viewport. Data ingestion and detector processing continue. A clear Return to
  latest control restores the latest candle and resumes following.

## Multi-timeframe analysis

- Combine emitted events in the right panel with explicit detector,
  source timeframe, lifecycle state and detection time, with filtering.
- Preserve causal timing: a higher-timeframe detector evaluates only after its
  candle closes, including during historical replay. Displaying its signals on
  a lower-timeframe chart must not imply they were known earlier.
- Seeing agreement or contradiction does not yet define an automated
  cross-timeframe confirmation rule or a new composite detector.

## Confirmed workspace purposes

- Live monitoring: visualize current provider-streamed market data with the
  selected detectors running as new data arrives.
- Historical exploration and replay: browse historical data and replay a chosen
  period with selected detectors progressing alongside the replay cursor.
- Completed evaluation review: load the results of an autonomous evaluation
  into the chart, with its detected events and outcome observations available
  for exploration.
- Opening completed evaluation results creates a separate analysis tab showing
  that run's recorded detections, outcomes and original configuration. Existing
  live and historical tabs remain intact; browsing results does not rewrite
  the evaluated run or its configuration.
- For the initial stage, completed evaluation tabs have right-panel views for
  Patterns, Results summary, and Run settings. Pattern info panels include
  recorded outcomes; Results summary contains counts, outcome statistics and
  unavailable-data counts; Run settings shows the original configuration
  read-only. The chart remains the main view. This layout is agreed for initial
  use and will be reviewed after practical use, rather than treated as final.
- Autonomous evaluations execute in the background without animating or
  advancing the active chart. Their execution is separate from interactive
  replay.
- Provide a workspace-level Runs view to configure and launch autonomous
  evaluations, inspect queue/progress status, and open completed results in
  separate analysis tabs. Evaluation processing remains independent of the
  chart currently being viewed.
- New evaluations offer Use current tab settings (copy its configured
  analytical timeframes, detectors and parameters) or Choose saved preset,
  followed by historical-period and outcome selection. Each evaluation pins
  its own resolved configuration; creating or executing it does not modify the
  source tab or track subsequent edits to that tab.
- In live monitoring, the chart may update while a candle forms, but detectors
  evaluate only on completed candle close. No provisional intrabar detector
  signals are required.
- Retain completed live candles as shared historical data for subsequent
  browsing, replay and evaluation, with source provenance and gap validation.

## Confirmed live persistence cadence

Durably record each completed source candle promptly on
close, independently of tab rendering. For canonical one-minute input this is
one persistence opportunity per instrument per minute, shared across tabs.
Forming-candle display updates do not require durable tick-by-tick storage.
Historical fetches persist validated batches as they arrive.

Separate crash-safe ingestion from packaging larger immutable Parquet history
segments. The existing durable import-batch approach is a candidate foundation.
Package durable records into larger history segments hourly as the initial
default; size thresholds and tuning can follow measurement. The last hour must
not exist only in memory. Recent durable batches remain available to chart
reads before packaging. Multiple tabs using the same feed share ingestion and
storage. Final storage mechanics and immutable publication remain to be designed.

The product includes live monitoring as a required workspace purpose. The
consolidated delivery map now sequences it under SCRUM-115/137 alongside the
other approved workspace capabilities.

Terminology follows the domain model: DetectionAnalysisConfig selects and
configures detectors/components; EvaluationPlan defines downstream context,
outcomes and segmentation; OutcomeDefinition specifies an observation and
OutcomeObservation is its event-level result. Completed-result Review mode may
show subsequent bars and outcomes; progressive replay preserves causal
visibility. Existing documentation's "live walkthrough" means interactive
historical replay, not provider-streamed live monitoring.

## Earlier requests to resolve in this discussion

These earlier requests are included in the consolidated specification; exact
implementation details are owned by the corresponding workspace stories.

- Keep the chart visible before replay starts.
- Ensure time and price axis labels are readable.
- Provide navigation to a selected date.

## Open decisions

- Full replay-state recovery after an application/backend restart; tab-focus
  recovery is already agreed to remain paused until explicit Play.
- First-launch behavior when there is no saved workspace. Restored tabs use
  their saved mode, lazy live reconnection and shared on-demand historical data.
- Default instrument when there is no saved selection or local history.
- Right-panel organization, sizing, and event inspection behavior.
- Historical exploration loading mechanics and deterministic initialization/reuse
  identity. The detector operation modes themselves are agreed.
- Higher-timeframe candle alignment and supported detector/parameter semantics;
  the seven supported chart/analytical timeframes and Apply rules are agreed.
- Replacement of an existing replay by Start replay from and reset semantics.

## Scope reference

The original MVP placed live data after its baseline. The approved next workspace
delivery now includes local live monitoring, tracked under SCRUM-115/137. The
current implementation uses TradingView Lightweight Charts and has replay,
event inspection and manual review functionality to preserve in the new layout.

The current analytical configuration contains a timeframe and selected
detectors, but the existing runtime/configuration validation is constrained
to one-minute analysis. Analysis on other timeframes extends that baseline
and requires explicit bar alignment, warm-up, parameter and detector semantics.

## Historical data access implications

Continuous historical scrolling is broader than the current selectable-revision
replay API. The existing acquisition worker publishes immutable validated
Parquet revisions with PostgreSQL metadata and provenance; chart navigation
must integrate with these services while preserving run reproducibility.

Recommended design, pending detailed agreement:

- Share retained instrument history across tabs, replay and autonomous
  evaluation; avoid independent per-tab downloads and storage.
- Track available ranges and request missing ranges incrementally in the
  background, with bounded chart rendering and visible loading/failure states.
- Reuse validated downloaded history on later navigation and application
  launches, preserving provider/source identity.
- Pin analytical runs to immutable source history; fetching more browsing
  history must not silently replace prices or reconfigure an existing run.
- Calculate required historical initialization across all enabled analytical
  timeframes before starting replay. Visible context and detector warm-up are
  related but distinct requirements.
- Preserve the replay cursor boundary even if later bars already exist in the
  local store. Fetching or browsing earlier candles does not rewind analysis.
