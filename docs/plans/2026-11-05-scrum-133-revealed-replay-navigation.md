# SCRUM-133 — revealed replay windows and Stop position

Separate the viewed replay window from the analytical cursor. Move to and left-edge loading may request an older recorded window from the same run, bounded by its earliest completed context and current finalized watermark. These reads must not consume candles, initialize a replacement runtime, acquire provider data or expose later transitions at an earlier viewed right edge. Return to replay cursor clears the browsing window while retaining the same run.

Carry viewed-window identity alongside run/cursor/timeframe identity in frontend snapshot readiness. Abort obsolete reads and reject inconsistent envelopes, including price/event availability beyond the requested causal cutoff. Save a browsing target/end with its run ID for browser/tab restoration. Changing chart timeframe adjusts the window around the same target using only already-revealed data.

Stop freezes the last coherent displayed prices, source-window endpoint and logical viewport into historical exploration. Clear run-specific evidence/review controls while preserving detector settings, zoom and position. Subsequent explicit browsing or Return to latest may explore retained history, but Stop itself must not load the source's future tail.

Verify read-only state invariance, older-window paging, initialization context, fractional/higher-timeframe causal cutoffs, current-run ownership, delayed responses, return to cursor, continued progression while browsing, tab restoration and Stop/reload geometry. Existing legacy replay endpoints remain compatible; modern chart sessions use the analytical session's initialized recorded prefix.

## Delivered behavior

- Move to and left-edge panning load older recorded replay candles without changing run identity, analytical cursor or detector state, and without requesting provider acquisition. Windows remain bounded to 500 candles. Initialization context is available before the selected replay start where the session recorded it.
- Browsing fixes a viewed right edge while Step or playback can advance the analytical cursor. Return to replay cursor resumes the current revealed window. Timeframe changes retain the browsing target; browser restoration retains the browsing window only for its saved run.
- Modern views expose completed candles and events available by the viewed right edge. Legacy replay callers keep the existing full revealed timeline by default; the chart opts into `as_of_end=true` when browsing an older window. Fractional endpoints exclude incomplete candles.
- Snapshot readiness includes window identity. Aborted or delayed reads cannot replace a newer window, and inconsistent run/cursor/timeframe or future-data envelopes are rejected. Return to replay cursor remains available during a pending or failed history read.
- Stop clears run evidence and retains the last coherent prices, bounded history endpoint, zoom and viewport. Reload preserves that position. Return to latest remains available when the retained window ends before the source does; only that explicit action loads the latest historical window.

## Verification and remaining work

Final verification: **923 Python tests passed, 10 skipped**; **44 browser cases passed**; **44 web unit tests passed**. Backend Ruff/mypy, frontend lint/type checking, the production build and `git diff --check` passed. New browser scenarios exercise recorded-prefix paging, continued progression while browsing, timeframe changes, stale reads, actual leftward dragging and Stop/reload viewport preservation. API tests verify causal cutoffs and unchanged session state. External integration tests remain skipped; these results do not claim live-provider acceptance.

SCRUM-133 remains **In Progress** because live-follow suspension/resumption is still outstanding. This increment completes the previously outstanding revealed replay-window paging and Stop/viewport scenarios. Live ingress and its chart integration are the next implementation boundary; full API-restart cursor recovery and verified-calendar extension retain their separate scope. No schema migration is introduced.
