# Chart UI review implementation follow-up

Date: 2026-11-03  
Branch: `feat/SCRUM-132-chart-workspace`  
Review: [Chart UI and web app implementation review](2026-11-03-chart-ui-web-app-review.md)

## Changes

| Review item | Resolution |
| --- | --- |
| F1: valid Move to fails | Match picker values by instant and use the loaded candle's canonical timestamp for chart focus. Successful navigation clears the previous error. |
| F2: reload applies drafts | Persist draft and applied configurations separately. Dirty and migrated legacy drafts require explicit Apply after restoration. |
| F3: chart reads starve during playback | A session controller owns serialized commands and accepted chart snapshots. Automatic advancement waits for a matching run/cursor snapshot. Failed commands or reads block advancement and expose explicit recovery. Recovery rereads server state before fetching the chart. |
| F4: old event selection silently fails | Keep selection/evidence available, pause playback and explicitly report that the candle is outside the loaded window. Do not claim successful chart focus. Full causal range loading remains SCRUM-128/133. |
| Session responsibility overload | Extract `useReplayController`, `useDetectorSettings`, `useHistoryWindow` and `PanelTabs`. Keep the chart renderer independent of networking and detector calculations. |
| Persistence and transport guards | Migrate workspace records to version 2; validate optional fields and configuration shape. Validate critical replay state/snapshot envelopes, restrict lifecycle statuses and reject snapshots for a different run/cursor. |
| Recovery | A per-session rendering boundary preserves access to other tabs and offers local tab-settings reset while retaining active run identity. |
| Repeated history/annotation work | Share immutable history requests in a bounded 16-window cache. Cancel subscriptions independently. Fetch annotations on session activation instead of each cursor change; use sets for review badges. |
| Effect dependencies | Enforce React Hooks rules, including exhaustive dependencies as errors. Separate configuration initialization from date/time input changes. |
| Keyboard and evidence focus | Give panel tabs roving focus, arrow/Home/End handling and panel relationships. Closing pinned evidence restores its originating info-button focus. |
| Mixed chart/review timezones | Use the tab timezone for missed-pattern inputs and interval display; convert to UTC for persistence and reject ambiguous/nonexistent picker values. |
| Global style coupling | Scope diagnostics tables, remove obsolete global layout/fixed chart rules and chart-internal table patches, and introduce workspace accent/panel-width tokens. Controls scroll within short desktop panels; narrow windows retain the stacked layout. |

Each logical change is committed separately with a descriptive commit body. The implementation leaves the review intact as a record of its reviewed revision.

## Architectural boundaries

React and Lightweight Charts remain in place. Backend calculations and immutable research records remain authoritative. Local tab recovery resets browser preferences, not source data or research records.

The session controller holds lifecycle state and a coherent snapshot with run/cursor identity. A readiness guard coordinates these two states. View updates may lag the server briefly during a request, but the interface identifies the update and automatic playback waits. Pause, Reset and Stop remain available during chart reads.

History caching is a bounded client optimization over immutable revision/range identities. It does not turn the current checksum-scanning endpoint into an indexed data store. Indexed range fetching, backend independent analysis sessions, incremental/bounded event delivery, multi-timeframe scheduling and as-of occurrence grouping remain their established delivery stories. These require their analytical contracts and backend foundations; they were not introduced as incidental fixes to the initial UI increment.

## Validation

- Web unit tests: 20 passed, including malformed workspace migration, invalid configuration/snapshot input, shared history requests and retry after failure.
- ESLint with Hooks dependency checks and the TypeScript/Vite production build passed.
- Chrome acceptance: **20 passed**. The suite exercises the original replay and annotation flows plus navigation, draft/apply restoration, slow and failed chart reads, clipped-event selection, panel keyboard navigation, session render isolation and short/narrow control access. A 1440×600 screenshot was also visually inspected for chart axes, panel access and navigation controls.

The code changes are confined to the web application and its tooling/tests. No detector version, analytical timeframe or backend data contract was changed.
