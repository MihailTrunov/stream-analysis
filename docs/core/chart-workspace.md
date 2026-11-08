# Chart workspace specification

Status: approved product and interaction decisions from the November 2026 UI
analysis; implementation progress is tracked in the delivery plan. Reconciled with Jira on 2026-11-03.

This is the authoritative specification for the next chart-workspace delivery.
It supersedes earlier single-walkthrough, one-minute-only analytical, UTC-display,
and deferred-local-live-feed wording where those conflict with the decisions
below. It does not declare the earlier MVP or new capabilities implemented.
The [discussion record](../plans/2026-11-01-chart-workspace-ui-analysis.md)
preserves the interview; the [delivery plan](../plans/2026-11-03-chart-workspace-delivery.md)
maps work to Jira and defines implementation prerequisites.

## Workspace and layout

- The chart occupies most of the available screen and exists before replay.
  Provide readable price/time axes, crosshair, pan and explicit zoom controls.
- Instrument and displayed chart timeframe controls overlay the chart's top-left
  corner. They are not duplicated as primary controls in the right panel.
- The right panel keeps live/history mode, navigation and playback controls
  accessible above switchable Patterns and Detector settings views. Patterns
  is the default view. Detector settings contains analytical timeframes,
  detector selections, modes and supported parameters.
- Multiple analysis tabs may hold different instruments and modes, including
  live monitoring, historical exploration/replay and completed evaluation review.
- Restore the previously open tabs and active tab on launch. Reconnect only
  the focused restored live tab; other restored live tabs reconnect when first
  focused. Already connected live tabs retain data/detector processing when
  unfocused. Only the focused tab updates chart visualization and DOM.
- Historical replay pauses on loss of tab focus and stays paused on return
  until Play. Rendering suspension must not drop live data or detector events.
- Chart axes, date/time picker and user-facing analysis times use one per-tab
  display timezone, default Europe/London (GMT/BST), clearly labelled. Display
  timezone changes never change UTC storage, candle alignment or analytical time.

## Data, browsing and live monitoring

- Support US30 and DAX initially. Continuous leftward scrolling loads local
  history or fetches missing provider ranges, up to actual provider availability.
  Use bounded requests/rendering rather than loading all history into the browser.
- Initially buffer 1,440 completed canonical M1 trading candles (24 trading hours),
  spanning scheduled closures, plus any extra detector initialization needed.
  Reuse retained prices and fetch missing ranges. Initially display approximately
  90 candles unless a saved viewport is restored. Higher-timeframe display reads
  cover at least that trading duration or a useful 90-candle window.
- Zoom buttons, wheel zoom, panning and resizing request older prices when the
  visible interval approaches or exceeds the loaded left edge. Older prices extend
  the display buffer without point focus or a zoom reset. Preserve candle spacing
  and a time anchor through prepends, forming-candle removal and rolling windows.
  Explicit Move to/event focus is a separate navigation action. Stop automatic
  retries on failure or known history limits; expose retry/loading status.
- Price loading is independent of historical detector execution. Respect each
  detector's live/manual/always policy; fetching prices does not restart live
  analysis or advance/rewind a replay. Bound individual pages, request cache and
  displayed candles (currently 500 per historical page, 16 cached windows and
  6,000 displayed prices per tab).
- Retain known detector hits and component observations alongside loaded prices.
  Panning or adding older prices must not discard calculated markers. Historical
  calculations supply their own chart observations; periods without results remain
  explicitly uncalculated. Replay evidence obeys revealed candle availability.
- Session annotations describe observed trading-session changes. Do not label
  the first loaded candle as a session opening or display the internal `provider`
  name as an unexplained marker. Calendar/session identities used for analytical
  lineage remain unchanged.
- Retained history is shared across tabs, historical replay and autonomous
  evaluation. Reuse fetched history after restart; preserve source provenance,
  validation, gaps, checksums and immutable revisions referenced by runs.
- Startup loads the selected instrument's last completed trading candles rather
  than assuming the wall-clock minute has a candle. Show retained provider prices
  while missing startup history is prepared, independently of live connection
  success. Weekends and holidays must not leave the chart blank.
- When the current date is outside verified calendar coverage, display the latest
  verified completed history and clearly report the live limitation. Do not invent
  calendar rules, imply those prices are a current live quote, or start an
  unverified live analytical session. Historical navigation/calculation remains
  available under the existing detector policies.
- Live monitoring receives current provider data. The forming candle may update
  on the chart, but detectors evaluate only completed candles. Do not generate
  provisional intrabar detector signals.
- Save every completed source candle durably on close, once per feed/instrument
  rather than once per tab. Persist validated historical-fetch batches as they
  arrive. Forming-candle updates do not require tick-by-tick archival.
- Package already durable records into larger historical segments hourly as the
  initial default. Recent records remain readable before packaging. Packaging
  failure must not lose acknowledged saved candles or mutate pinned revisions.
- Panning a live chart back or selecting an older pattern suspends automatic
  following. Preserve its viewport while ingestion/detection continue. Return
  to latest restores the newest candle and resumes following.
- Loading, disconnection, unavailable source history and uncalculated analysis
  must be distinguishable. Never fabricate candles to fill an unexpected gap.

Aggregation, initialization, availability and outcome compatibility follow the
[versioned implementation contract](../plans/2026-11-03-multitimeframe-session-implementation.md).
Display timezone never changes those analytical rules.

## Timeframes and detector configuration

- Supported chart and analytical timeframes: 1m, 5m, 15m, 30m, 1h, 4h and 1D.
  Build higher-timeframe candles from shared canonical one-minute history;
  do not independently fetch a separate provider history per timeframe.
- A tab explicitly selects one or more analytical timeframes. Each has its own
  enabled detectors, parameters and operation modes. Enabling a one-minute
  detector does not implicitly enable it on other timeframes or other tabs.
- The chart timeframe controls display independently of analytical timeframes.
  Changing chart timeframe does not enable/disable detectors or restart analysis.
- Each detector evaluates completed candles of its analytical timeframe. A
  five-minute detector never sees an unfinished five-minute bar as completed.
  Live, exploration, replay and evaluation use the same aggregation and detector
  semantics. No automated cross-timeframe confirmation formula is implied.
- Operation modes apply per detector within its tab/analytical timeframe:

| Mode | Live monitoring | Historical exploration |
| --- | --- | --- |
| Live | Evaluate on candle close | Calculate current view on request |
| Manual | No automatic processing | Calculate current view on request |
| Always on | Evaluate on candle close | Calculate as the viewed period changes |

- Calculate current view is available in every mode for enabled detectors.
  During replay, every selected enabled detector processes advancing candles
  regardless of operation mode. Disabled detectors remain disabled.
- Historical window calculations need sufficient earlier initialization history,
  not just the displayed bars. Preserve configuration/source/timeframe lineage;
  absence of loaded results is not evidence that no pattern occurred.
- Use an explicit Apply action for supported settings. Stop historical replay
  before editing its analytical configuration. For live analysis, reinitialize
  affected analysis from sufficient preceding history and retain attribution
  for earlier detections. Parameter changes create configuration revisions;
  formula/rule changes require detector versions and tested code.

## Navigation and replay

- One date/time picker has Move to and Start replay from actions. Fetch missing
  history when needed for navigation or initialization.
- Move to changes the viewport. During replay, it is limited to already revealed
  history and never advances/rewinds the analytical cursor or detector state.
- Start replay from creates a fresh run, initializes sufficient preceding history
  and opens paused, ready for Play or Step. Show preceding candles as context.
  Research event selection still uses detection time within the selected interval.
- Default endpoint: latest completed source candle available at launch, pinned
  for that run. Advanced controls may choose an earlier endpoint. Reaching the
  endpoint stops progression; it does not switch to live monitoring.
- Step advances to completion of the next displayed chart candle while processing
  every intervening one-minute candle and analytical close in sequence. Changing
  display timeframe cannot skip intermediate detector updates or events.
- Pause preserves run/cursor. Stop ends replay and returns to exploration at the
  same chart position, retaining detector configuration and the visible chart.
- Preserve useful existing speed and next-event controls, audited annotations
  and causal event history. UI navigation is distinct from analytical seek;
  reuse a deterministic backend seek only for an explicitly defined seek action.
- Historical future bars/events must not leak into progressive replay even when
  already cached or produced by another tab. Higher-timeframe signals become
  visible only when their candles close. Outcome-window access belongs to
  completed-result review and downstream evaluation, not detector processing.

## Pattern panel and chart annotations

- Stack Developing, Active and Completed sections vertically. Each section has
  independent ordering, default latest observable state transition first, using
  detection time rather than candidate start or underlying event time.
- Developing: candidates and other pre-confirmation intermediate states.
- Active: confirmed ongoing states, such as compression before release.
- Completed: ended detector lifecycles, preserving actual terminal status.
  Reversal/continuation move here on confirmation; compression moves from
  Developing to Active on confirmation and Completed on release. Invalidated
  and expired candidates also belong here, clearly distinguished from success.
- Rows represent occurrences; preserve ordered transitions and structured
  evidence rather than changing detector lifecycle semantics for presentation.
- Show configured analytical timeframes together by default. Optional detector
  and timeframe filters change visibility only, never computation settings.
- Scope the panel to the chart's displayed period. Developing/Active reflect
  what was observable at the period's right edge, capped by the replay cursor.
  Include earlier-started occurrences still developing/active there. Later
  stored transitions cannot prematurely relabel an occurrence as completed.
- Panning live history changes the panel's viewed period while live processing
  continues in the background. Historical calculations respect operation modes.
- A record whose analytical timeframe differs from the chart gets a timeframe
  badge. Selection keeps the chart timeframe and does not suggest switching it.
- Chart annotations default to the displayed timeframe. Additionally show and
  highlight a selected occurrence from another analytical timeframe.
- Selecting a pattern focuses/highlights it and pauses historical replay without
  changing its cursor. Live ingestion/detection continue.
- Details are a separate info-icon action. Hover previews a floating panel;
  click pins it for inspection/scrolling; keyboard focus accesses the same
  evidence. Do not replace the pattern lists or open details merely on selection.
- Show recorded rationale, timing, units, configuration/version and data lineage.
  Do not recalculate detector decisions or generate substitute narrative in UI.

## Autonomous evaluations and result tabs

- A workspace-level Runs view configures/launches evaluations and shows durable
  queue/progress status. Evaluations do not animate or take over the active chart.
- Offer Use current tab settings or Choose saved preset, then historical period
  and outcomes. Copy and pin resolved analysis settings; later tab edits do not
  mutate the run. Retain the existing serial durable evaluation queue contract.
- DetectionAnalysisConfig governs detection; EvaluationPlan governs downstream
  context/outcomes/segmentation. DetectorEvents, OutcomeDefinitions and
  OutcomeObservations retain their separate meanings and lineage.
- Opening completed results creates a separate analysis tab showing recorded
  detections/outcomes and original settings. Existing tabs are unaffected.
- Initial results layout: Patterns, Results summary and read-only Run settings
  as right-panel views. Pattern info includes recorded outcomes. Summary includes
  counts, outcome statistics and unavailable/ambiguous status counts. Review
  this layout after practical use; it is not a final visual design.
- Result review may show later outcome-window bars, clearly separated from
  detection-time evidence. It never rewrites source events or the original run.
- Preserve manual review/audit and export contracts already established for the
  research MVP. Production alerts, order execution, ML and hosted operations
  have not been added by this UI discussion.

## Implementation prerequisites still open

These are not permission to guess analytical behavior. Resolve and version
affected contracts before shipping their implementation; unrelated layout
work can proceed against existing deterministic one-minute fixtures.

1. Intraday bucket anchors, partial sessions, 1D instrument trading-day boundary,
   DST and missing/partial bucket policies. UK display timezone is not an answer
   to analytical alignment. Existing calendar coverage must be extended through
   a new verified version for later source dates.
2. Multi-timeframe initialization anchors, cross-timeframe event tie-breaking,
   supported detector/timeframe combinations and parameter semantics. Do not
   silently remove the existing M1-only TrendLeg qualification restriction.
3. Outcome horizon units/trading-day policy for higher-timeframe events. The
   existing same-day intraday outcome policy has not been changed by adding 1D.
4. Provider live capabilities, completed-minute finalization, reconnect catch-up
   and conflict handling between captured live and downloaded historical bars.
5. Immutable publication/read-through of recent durable records, range coverage,
   download coalescing and deterministic historical-analysis reuse identity.
6. Full restart recovery of replay state, replacement of an active replay,
   reset behavior, playback-speed units on different chart timeframes, first-run
   defaults, and DST-ambiguous/nonexistent date/time inputs.
7. Measured rendering/work budgets, automatic historical request scheduling,
   info-panel dismissal and detailed sizing. These are implementation design
   details, not reasons to silently change the agreed workflows.
