# Chart history buffer and stable navigation

Approved scope: initialize normal live prices with 1,440 completed M1 trading candles, spanning closed periods; display about 90 candles initially. Warm-up may require older data. Reuse retained data and fetch only missing ranges. Read historical prices in bounded pages. Price loading does not change detector live/manual/always policy.

Use a shared viewport controller to preserve candle spacing and a time anchor when prices are prepended, forming candles disappear or retained windows change. Pan, wheel, zoom buttons and resize request coverage before the loaded left edge. Coalesce requests and stop on a known boundary or error. Earlier loading extends a bounded display buffer and never invokes event focus; explicit Move to and pattern focus remain distinct.

Keep replay price visibility causally bounded and never change replay progress when browsing. Retain focused-tab connection/rendering policy. Replace raw provider session markers with contextual session information and show loading, failure and history limits near navigation.

Implementation and verification cover price paging/merge, calendar-aware initialization, shared viewport geometry, live/history/replay integration, loading failure/retry, session markers and isolated browser regressions. Continue on master; do not restart the installed stack or contact a real provider during verification.
