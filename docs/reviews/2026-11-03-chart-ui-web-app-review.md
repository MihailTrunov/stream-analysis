# Chart UI and web app implementation review

Date: 2026-11-03  
Reviewed revision: `c3c8b1d` on `feat/SCRUM-132-chart-workspace`  
Scope: the current web application, chart integration, workspace/session state, supporting browser replay API, and test coverage. This reviews existing code as requested, not only an uncommitted diff.

Implementation follow-up: [fixes, structure changes and validation](2026-11-03-chart-ui-review-follow-up.md).

## Assessment

The current implementation is a useful first layout increment, but it needs a correctness and state-management pass before expanding into live and multi-timeframe analysis. Keep React, the chart library adapter and the backend-owned detector calculations. Refactor session orchestration rather than rewriting the application.

The chart-dominant layout, right-hand controls, separate evidence inspection and inactive-chart unmounting follow the agreed direction. The implementation is still a single-replay walkthrough wrapped in a tabbed workspace. It is not yet the independent analysis-session architecture described in [the workspace contract](../core/chart-workspace.md).

Four actionable correctness findings follow. P2 means a functional defect to fix in the next increment; none is presented as a security vulnerability or evidence that canonical detector calculations are wrong. The first finding was reproduced by executing the actual TypeScript helper functions. The others follow from the identified code paths; their browser regressions still need to be added.

## Correctness findings

### F1 — P2: Move to rejects valid loaded candles

**Location:** [ReplaySession.tsx](../../web/src/ReplaySession.tsx), `moveTo`, lines 208–214; [time.ts](../../web/src/time.ts), `inputInstant`; [replay.ts](../../web/src/replay.ts), `normalizeApiInstants`.

`inputInstant` returns ISO strings ending in `.000Z`. API normalization removes `.000`, leaving `Z`. `moveTo` checks exact string equality against loaded candle timestamps. Therefore a valid minute selected in either UK or UTC mode fails the membership check and displays an out-of-range error.

Executed reproduction using the actual helpers:

```text
inputInstant('2026-01-05T12:11', 'Europe/London')
  => 2026-01-05T12:11:00.000Z
normalized API candle
  => 2026-01-05T12:11:00Z
string equality: false; epoch equality: true
```

**Fix:** use one canonical instant representation at all boundaries, or compare epoch values and set `focusTime` to the matching candle's canonical timestamp. Fixing only the membership test is insufficient: the chart focus lookup also uses exact string equality.

**Regression:** choose an existing loaded candle in exploration and replay; assert successful viewport movement and an unchanged replay cursor. Include GMT, BST and UTC. The existing workspace test checks rejection of future candles but does not prove successful navigation.

### F2 — P2: Reload implicitly applies unsubmitted detector settings

**Location:** [ReplaySession.tsx](../../web/src/ReplaySession.tsx), configuration initialization and persistence, lines 74–101, and `preview`, lines 182–191; [workspace.ts](../../web/src/workspace.ts), `AnalysisTab.config`.

Editing a detector parameter clears `previewHash`, correctly preventing replay until Apply. However, every edit immediately persists `editedConfig` as the tab's configuration. On reload, initialization reads that draft, validates it through `/config/preview`, and sets `previewHash` without an explicit Apply action. A valid but unsubmitted edit consequently becomes launchable after reload.

**Fix:** distinguish draft settings from the last applied configuration and hash. Persist their identities and dirty state explicitly. Restoration may validate an applied configuration, but must not turn a dirty draft into an applied configuration.

**Regression:** edit a valid parameter, do not Apply, reload, and verify that replay remains blocked pending Apply or that the last applied settings are used with the draft clearly identified. Verify that an already running replay continues to display its pinned configuration.

### F3 — P2: Playback can advance while chart snapshots are repeatedly cancelled

**Location:** [ReplaySession.tsx](../../web/src/ReplaySession.tsx), snapshot effect, lines 118–130; command queue and timer, lines 139–169.

Every cursor change starts `/view` and annotation reads, cancelling their predecessors. `busy` covers the mutation request, not the following chart read. At 4× playback the next tick is scheduled 250 ms after the previous mutation completes. If `/view` takes longer than the interval between cursor changes, each response is cancelled before it can be accepted. The replay status advances while the chart and pattern panel can remain at an older snapshot until playback pauses. There is no visible snapshot-lag indicator.

This matters especially because `/view` builds full replay event history, rather than a small incremental response. The API returns a cursor identity, but the component discards it when installing the chart arrays.

**Fix:** establish an explicit snapshot policy. For this increment, waiting for the coherent chart snapshot before the next automatic advance is the simplest option. Alternatively, allow a bounded read in flight, accept monotonic snapshots for the same run, coalesce newer requests and display the rendered cursor separately. Keep run identity and cursor with the data, not in independent state variables alone.

**Regression:** delay `/view` responses beyond the 4× tick interval. Assert that the chart makes progress, requests remain bounded, and stale responses cannot replace data after Reset, Stop or a new run.

### F4 — P2: Older event rows remain selectable when their candles are unavailable

**Location:** [browser_replay.py](../../src/market_analysis/application/browser_replay.py), `visible_view`, lines 634–702; [ReplaySession.tsx](../../web/src/ReplaySession.tsx), `selectEvent`; [CandlestickChart.tsx](../../web/src/CandlestickChart.tsx), focus effect.

`visible_view` returns the last 500 replay candles but all emitted events. After a sufficiently long replay, an event outside those 500 candles remains in the panel. Selecting it sets the focus notice and suspends following, but the chart lookup finds no matching candle and returns silently. The UI suggests that it is viewing the selected time while the viewport has not moved there.

**Fix:** load a causal candle window around the selected event before reporting navigation success. Until range loading is implemented, show an explicit unavailable-history state instead of silently claiming focus. Preserve the replay cursor and prevent fetching unrevealed replay data.

**Regression:** replay more than 500 candles with an early detector event, select it, and verify either actual navigation or an explicit limitation. This is a defect in selection feedback even though full history loading is a planned follow-up.

## Architecture and code structure

### Boundaries worth preserving

- **Backend owns analytical truth.** The browser consumes canonical candles, observations, recorded events and evidence. It does not duplicate detector calculations. Immutable source revisions, pinned configurations and server-side replay visibility are appropriate research boundaries.
- **Pure chart projection is separate from rendering.** [chartModel.ts](../../web/src/chartModel.ts) validates candles and constructs series/markers. [CandlestickChart.tsx](../../web/src/CandlestickChart.tsx) contains the imperative chart-library lifecycle. This is a useful, testable separation.
- **Workspace shell is small.** [App.tsx](../../web/src/App.tsx) composes navigation, workspace and diagnostics; [AnalysisWorkspace.tsx](../../web/src/AnalysisWorkspace.tsx) handles tab identity and bootstrap. Inactive sessions retain their React state but unmount the chart DOM, matching the rendering-cost requirement for this increment.
- **Read-only exploration is separate from replay advancement.** The source-bars endpoint does not advance detectors. Keep that distinction as history loading expands.

### Main structural weakness: session ownership

`ReplaySession` combines source selection, configuration drafts, persistence synchronization, command serialization, timers, network reads, chart viewport state, evidence inspection, annotation integration and almost all right-panel markup. Its difficulty is the number of interacting state owners, not simply its roughly 400 lines.

The same concepts appear in several places: persisted `tab.runId`, parent `bootstrap.active`, local `state`, `stateRef`, independent chart arrays, and `draftConfig`. Their synchronization relies on effects and captured values. Several effects intentionally list only a subset of their referenced values, while the ESLint configuration has no React Hooks dependency rules. That makes future changes difficult to audit.

Recommended separation:

| Boundary | Owns | Must not own |
| --- | --- | --- |
| Workspace state | Tab identities, focused tab, persisted preferences | Replay transport timers |
| Analysis-session controller | Run lifecycle, command queue, accepted snapshot, focus/visibility policy | Panel markup or chart-library objects |
| History data service | Revision/range identity, cached windows, cancellation and request deduplication | Detector calculations |
| Detector configuration state | Draft, applied revision/hash, dirty state, validation | Playback clock |
| Chart adapter | Series, markers, viewport and user navigation callbacks | Network access or run mutation |
| Panel components | Controls, settings, patterns and evidence presentation | Competing copies of session truth |

Use a reducer or explicit state machine for session transitions and a small controller hook around it. This does not require adopting a new global-state library. Extract responsibility boundaries while fixing the defects, rather than splitting JSX into files without changing ownership.

Before independent live/replay tabs arrive, move session identity into a backend contract keyed by analysis session. The current process-local `BrowserReplayService` has one active session guarded by a lock. Frontend tabs cannot make that backend multi-session. Its restriction is correctly disclosed today.

### Data access and rendering costs

The 500-candle response cap limits payload size, not total computation:

- `published_source_bars` scans checksum-verified revision data to produce a bounded deque. Switching back to a history tab fetches it again; no shared client range cache exists.
- `visible_view` traverses visible replay steps and serializes all emitted events on every read. Repeated advancement makes the cumulative event serialization grow roughly quadratically with replay length when events accumulate steadily.
- The UI refetches annotations on every cursor change, reconstructs chart models and calls `setData` for complete series snapshots. During replay, new arrays also defeat memoization on unrelated renders.
- The event timeline mounts every returned row and scans annotations for each row.

These are architectural limits, not measured latency claims. Implement shared indexed/range-aware history under SCRUM-128, then bounded snapshot/event delivery and incremental chart updates where profiling supports them. Do not extend the present full-snapshot loop directly to multiple live tabs.

### Persistence, types and recovery

`restoreWorkspace` validates tab identity, instrument, timezone and viewport, but retains unchecked optional fields such as `start`, `runId` and `config`. A direct helper probe confirmed that a numeric `start` and incomplete configuration survive restoration. Server configuration validation provides a later guard, but this is not a complete persistence schema or migration policy.

`apiJson<T>` normalizes selected fields then casts to `T`; it does not validate response shape. `ReplayState.status` is an unrestricted string, and detector selections rely on optional identifiers and an index signature. Introduce explicit lifecycle unions and validate persisted state and critical session snapshots. Add workspace migration and recovery tests before changing the storage format for multi-timeframe settings.

There is no app/session error boundary. An unexpected render failure can remove the whole workspace. Add a session-level recovery boundary that preserves other tabs and offers a reset of the affected local tab. Keep durable source/run data outside that reset.

### UI composition, styling and accessibility

The layout direction is appropriate, but [style.css](../../web/src/style.css) layers new workspace overrides onto old global `section`, `table`, `label` and chart rules. The existing TradingView table reset demonstrates the resulting coupling. Scope diagnostic/review styles and chart-container styles explicitly, remove superseded rules, and centralize repeated spacing/color values as the UI settles.

Workspace tabs implement arrow navigation and roving focus. The Patterns/Detector settings tablist does not implement the equivalent keyboard behaviour or `aria-controls` links. Pinned evidence moves focus into the popover, but closing it does not explicitly restore focus to the originating info button. These should be included in keyboard acceptance tests.

Manual missed-pattern inputs remain explicitly UTC while the chart defaults to UK time. The labels avoid an undisclosed conversion, but this forces users to translate between two clocks during summer. Use the tab display timezone consistently while keeping stored instants UTC.

No new browser visual/accessibility audit was performed in this review. Small-screen, short-window and high-zoom usability remain unverified here; the fixed-height desktop shell and non-shrinking controls particularly merit checking.

## Accepted scope still outstanding

The following are delivery gaps, not newly discovered regressions in this deliberately limited increment:

| Agreed capability | Current implementation / delivery owner |
| --- | --- |
| Independent live and replay sessions | One active backend replay; SCRUM-130/137 and workspace integration |
| Continuous retained history and fetching | Published local revision, bounded window; SCRUM-128/133 |
| Multiple chart/analytical timeframes | M1 only; SCRUM-127/129/130 |
| Per-detector operating policies and manual history calculation | Parameter editing only; SCRUM-131/134 |
| Developing / Active / Completed as-of occurrence groups | Chronological transition list; SCRUM-135 |
| Runs management and completed result tabs | Disabled Runs entry; SCRUM-138/139 |

The transition list currently sorts oldest first and does not follow the displayed period. Treat it as a temporary baseline component, not as the completed Patterns panel.

## Verification and recommended next work

Fresh checks on the reviewed revision:

- Web unit suite: **16 passed** across five files.
- ESLint: passed.
- TypeScript and Vite production build: passed.
- Direct execution of current TypeScript helpers reproduced F1 and confirmed the persistence validation gap.

The unit suite mostly tests pure helpers and server-rendered markup. It does not exercise chart effects or session async orchestration. Existing Playwright tests were inspected, not rerun; prior browser/Python results are recorded in [the increment report](../plans/2026-11-03-scrum-132-workspace-increment.md) and are not claimed as fresh verification here. No full security audit, provider test, production load test or cross-browser certification was performed.

Recommended order:

1. Fix F1–F4 with targeted browser/controller regressions, including slow and failed requests and runs exceeding the candle-window cap.
2. Extract session lifecycle/snapshot ownership and draft-versus-applied configuration state; add dependency-aware Hooks linting and persistence validation.
3. Resolve SCRUM-127 analytical contracts and implement range/session foundations before adding live and multi-timeframe controls.
4. Integrate the as-of occurrence panel and perform keyboard, short-window and larger-history acceptance checks.

This review changes documentation only. It does not mark implementation stories complete or silently amend the agreed product contract.
