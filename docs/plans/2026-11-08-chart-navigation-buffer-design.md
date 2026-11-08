# Chart history buffer and stable navigation

Approved scope: initialize normal live prices with 1,440 completed M1 trading candles, spanning closed periods; display about 90 candles initially. Warm-up may require older data. Reuse retained data and fetch only missing ranges. Read historical prices in bounded pages. Price loading does not change detector live/manual/always policy.

Use a shared viewport controller to preserve candle spacing and a time anchor when prices are prepended, forming candles disappear or retained windows change. Pan, wheel, zoom buttons and resize request coverage before the loaded left edge. Coalesce requests and stop on a known boundary or error. Earlier loading extends a bounded display buffer and never invokes event focus; explicit Move to and pattern focus remain distinct.

Keep replay price visibility causally bounded and never change replay progress when browsing. Retain focused-tab connection/rendering policy. Replace raw provider session markers with contextual session information and show loading, failure and history limits near navigation.

The later reported loss of markers is part of this scope: preserve already calculated detector events and component observations for loaded candles while navigating. Historical calculations must expose observations for indicator overlays. Mark uncalculated or partially calculated periods explicitly; do not run manual detectors merely because more prices were loaded. Keep evidence scoped to the analysis identity/timeframe and revealed availability, without changing immutable event IDs.

Implementation choices: historical pages remain bounded at 500 candles, the shared request cache retains 16 windows, and each displayed price buffer is bounded at 6,000 candles. Eviction preserves the visible interval. At capacity, fill actual exposed history without repeatedly trying to obtain an impossible extra buffer. Preserve pending Fit actions through forming-candle removal, avoid replacing unchanged price series during polling, and retain the original navigation intent when retrying a failed load.

Delivered in master commits `106171b` and `0d8e9ec`. The current product contract is [chart workspace](../core/chart-workspace.md); implementation and test evidence are in the [verification report](../reviews/2026-11-08-chart-navigation-buffer-verification.md).

Implementation and verification cover price paging/merge, calendar-aware initialization, shared viewport geometry, live/history/replay integration, loading failure/retry, session markers and isolated browser regressions. Continue on master; do not restart the installed stack or contact a real provider during verification.

Startup follow-up: load the last completed trading candles for the selected instrument, not an assumed candle at the current clock minute. Show retained history while preparing the larger buffer. Historical prices are independently usable if live connection fails or the current calendar is out of verified coverage; clearly identify the latter as latest verified history, without extrapolating analytical calendar rules. See [startup failure and verification](../reviews/2026-11-08-latest-available-startup.md).
