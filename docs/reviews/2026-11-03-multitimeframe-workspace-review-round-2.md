# Multi-timeframe workspace review — round 2

Date: 2026-11-03  
Reviewer: Astra, medium reasoning  
Reviewed revision: `d2e22e87b29d0f306b871e656cdb7c5c43297883` (`master`)  
Change range: `4a79b4f..d2e22e8`

## Assessment

The increment establishes the right main boundaries: canonical M1 input, a shared aggregation implementation, independent session runtimes, immutable run attribution, and browser projections of recorded events. Keep these foundations. Four confirmed P2 correctness issues need a focused follow-up before extending this orchestration into provider live ingestion. None requires changing detector formulas or replacing React/the chart library.

This review applies the repository code-review skill and reads the workspace specification, multi-timeframe implementation/delivery contracts, and prior UI review/follow-up. P2 means a functional defect for the next correction increment; these are not claims of a security vulnerability. All four findings are ordered by analytical/audit impact, then operational impact.

## Confirmed findings

### F1 — P2: Scheduled closed time can leave a complete analytical candle permanently unprocessed

**Locations:** [analysis_sessions.py:185](../../src/market_analysis/application/analysis_sessions.py#L185), `watermark` and `consume`; [analysis_sessions.py:394](../../src/market_analysis/application/analysis_sessions.py#L394), historical completion; `history_candles` at lines 634–637. The domain aggregator already exposes the required `advance` operation.

Session time advances only when a source candle is consumed. A bucket whose final required open minute precedes its interval end remains pending until another source bar arrives. If the selected endpoint is in scheduled closed time, the session exhausts its source bars, reports `completed`, and never closes that bucket. The history-candle endpoint has the same push-only integration. Scheduled closed minutes are not missing source coverage and must not require fabricated input candles.

**Executed reproduction:** an isolated seeded SQLite database, with the source loader/calendar replaced by a deterministic fixture: open session 09:00–10:02 UTC, complete M1 input 09:00 through 10:01, retained bounds extending to 14:00, selected history endpoint 10:05. The API returns 201/completed with watermark 10:02 and last M5 candle 09:55. Calling the same session aggregator's `advance(10:05)` immediately produces the valid 10:00–10:05 candle with two source minutes. No missing-open-minute repair or additional price data is involved.

**Impact:** valid terminal buckets and their detector transitions disappear at historical endpoints in breaks/after session close. Across a gap, availability can also be delayed until the next source minute is consumed. This is an integration defect in already delivered aggregation semantics, not deferred provider fetching.

**Fix:** track a finalized analytical watermark independently of the last source index. Advance through calendar-verified closed slots to the selected/revealed endpoint, dispatching closures in causal order, including before initialization checks and at completion. Never advance across an unverified missing open slot or reveal beyond the replay cursor. Add endpoint-in-break, end-of-day, and replay/history parity regressions.

### F2 — P2: Higher-timeframe missed-pattern reviews cannot be saved

**Locations:** [ReplaySession.tsx:293](../../web/src/ReplaySession.tsx#L293), first-stream review routing; [analysis_sessions.py:591](../../src/market_analysis/application/analysis_sessions.py#L591), review scope; [app.py:703](../../src/market_analysis/api/app.py#L703), missed annotation creation. The existing membership validation is in [validation_annotations.py:376](../../src/market_analysis/persistence/validation_annotations.py#L376).

The new session exposes an analytical stream's timeframe in its review scope, but its pinned dataset revision contains only canonical M1 membership. `create_missed_pattern_annotation` requires membership for the requested timeframe. Passing a 5m stream run therefore fails even though its source revision, interval, and aggregation lineage are valid. Additionally, the UI always chooses `initialization[0].run_id`, so users cannot select which analytical timeframe a missed occurrence concerns.

**Executed reproduction:** launch the standard offline US30 revision with streams ordered 5m, 1m and chart timeframe 1m; Step once; submit a missed compression annotation for the revealed 10:00–10:01 interval through the same first-stream run used by the UI. The API returns 422, `target dataset revision/instrument/timeframe does not exist`. The storage lookup seeks `(revision, US30, 5m)` in an M1-only source revision. Reordering analytical streams changes the audit target independently of the displayed chart.

**Impact:** the delivered manual-audit workflow is unavailable for higher analytical timeframes, and mixed streams have no explicit target selection. This affects existing review functionality rather than a deferred evaluation outcome feature.

**Fix:** give missed reviews an explicit analytical timeframe/stream target and validate their source coverage through the pinned aggregation/session lineage instead of requiring a separately fetched higher-timeframe dataset. Preserve the distinction between canonical source timeframe and analytical review timeframe. Use actual selected candle boundaries rather than the existing unconditional one-minute interval when selecting higher candles. Test both stream orders and successful persisted/exported higher-timeframe review attribution.

### F3 — P2: Historical calculation reports complete coverage without any initialized analytical bars

**Locations:** [analysis_sessions.py:385](../../src/market_analysis/application/analysis_sessions.py#L385), replay-only initialization guard; `view` at line 576 (`coverage: calculated`); [useHistoricalAnalysis.ts:58](../../web/src/useHistoricalAnalysis.ts#L58), cached coverage; lines 72–78, loaded projection.

Only replay checks the required warm-up count. Historical calculation unconditionally becomes completed, its view unconditionally says `calculated`, and the browser marks a requested stream loaded based on the requested end alone. A stream with no completed input bars is consequently indistinguishable from a calculated stream with no patterns.

**Executed reproduction:** on the real seeded `offline-replay-us30-v3` revision, request history 09:00–09:05 with enabled 15m compression. The API returns 201 with `processed: 0`, `required: 11`; `/view` returns `coverage: calculated` and zero events. The browser's coverage logic accepts that response as loaded.

**Impact:** early/short historical views, particularly higher analytical timeframes, falsely present absence of detector results as absence of patterns. This contradicts the explicit insufficient-initialization/uncalculated distinction. Loading all available preceding source does not prove that enough analytical candles exist.

**Fix:** either reject historical windows without sufficient preceding initialization, consistently with replay, or return per-stream valid-analysis bounds and an explicit initialization-unavailable status. The browser must only advertise loaded coverage inside those bounds, including after reuse while panning backward. Add the zero-bar case and a partially initialized historical interval.

### F4 — P2: Hidden completed history sessions exhaust replay capacity after browser reloads

**Locations:** [analysis_sessions.py:266](../../src/market_analysis/application/analysis_sessions.py#L266), common 20-session limit; lines 405 and 410–415, registration/discovery; [useHistoricalAnalysis.ts:11](../../web/src/useHistoricalAnalysis.ts#L11), browser-only session ownership and lines 67–69, cleanup.

Completed historical calculations stay in the same registry counted against the 20-session limit. Their IDs exist only in hook refs and are not returned by `/analysis/active`. A browser reload/navigation does not provide a reliable React-unmount cleanup transaction with the server. Losing the page consequently loses the only UI handles that can stop those sessions, while their memory and capacity remain occupied.

**Executed reproduction:** twenty valid history launches against an isolated SQLite database all returned 201. `/analysis/active` then returned zero sessions; the next replay launch returned 422, `stop an analysis session before opening another`. The reload/orphan path was traced in the frontend; this was not a browser unload automation test.

**Impact:** repeated ordinary history use and reloads can block all new replay/history launches until server restart or manual API cleanup. Multiple individually calculated streams also consume the common limit faster than the visible tab count suggests. This concerns lifecycle management in a still-running API process, not deferred process-restart reconstruction.

**Fix:** separate retained historical results from active runtime capacity, with explicit eviction/leases or recoverable owner/session discovery. Do not make correctness depend on unload requests. Keep durable evidence and audit references valid when runtime caches are reclaimed. Test repeated calculate/reload behavior, mixed replay/history capacity, and replacement at the capacity limit.

## Architecture and code structure

- **Preserve analytical ownership.** `CandleAggregator` is provider-independent, uses Decimal OHLC, distinguishes interval labels from availability, rejects unexpected open gaps, and leaves M1 identity intact. Session views cap event visibility by availability; occurrence projection preserves transition history and earlier ongoing occurrences.
- **Preserve isolation and immutable attribution.** Each detector execution owns its runtime/store/snapshot. The session manifest records source checksum, aggregation/calendar versions, initialization origin, and stream identities. Display changes do not rebuild detector state. No confirmed future-event leak or cross-tab cursor mutation was found in the inspected paths.
- **Make orchestration lifecycle explicit.** The manager currently combines source loading, initialization, persistence, scheduling, runtime retention, view projection, and audit authorization. Extract a clock/coverage policy and a result/runtime lifecycle boundary as part of fixing F1/F3/F4. A separate detector runtime per detector is understandable for policy isolation, but duplicates component processing; keep that tradeoff visible when work budgets are measured.
- **Frontend separation improved, but async ownership needs stronger contracts.** `useReplayController` retains serialized mutations and matching run/cursor/timeframe snapshots. History identity/generation guards stop obsolete results replacing current settings. However, result coverage is inferred from request bounds, and result/session ownership depends on ephemeral refs. Carry backend coverage and lifecycle identities explicitly rather than adding more local flags.
- **Audit reuse needs analytical/source separation.** Reusing `ReplayEventStore` avoids a competing evidence store. F2 shows that copying legacy M1 review scopes is insufficient for aggregated streams. Fix that boundary without mutating canonical source records or detector evidence.
- **Additional follow-up, not a confirmed finding:** transaction commit-failure recovery and deterministic next-event tie selection deserve focused tests. The latter currently uses lexical timeframe ordering in `command`, unlike numeric-duration ordering in `view`. This review did not produce a concrete simultaneous-event fixture demonstrating user impact, so it is not severity-ranked.

## Verification and limits

Executed `.venv/bin/pytest tests/test_analysis_sessions.py tests/unit/test_aggregation.py`: **28 passed** (one existing Starlette/AnyIO deprecation warning). Executed temporary Python probes using the actual FastAPI application, a temporary SQLite database and temporary seeded source files. Capacity, missing terminal aggregation, historical zero-initialization coverage, and failed higher-timeframe audit submission were reproduced. The scheduled-close probe substitutes the source loader/calendar with a small explicit fixture; it does not claim provider acceptance. Temporary probes were kept outside the repository at `/tmp/round2_review_checks.py`; the scenarios and observed outputs above make the findings independently reproducible.

No production code or existing tests were modified. No credentials/settings file was read. No commits, pushes, Jira updates, or further agents were used. No full browser suite, PostgreSQL integration, live provider connection, year-scale performance run, or process-restart reconstruction test was performed in this round. Existing test successes do not cover the new failure scenarios above.

Explicitly deferred range fetching/indexing, provider finality and durable live retention, verified-calendar extension, full API-restart cursor reconstruction, and autonomous evaluation outcomes remain outside this review's defect list. This review does not reopen their accepted delivery boundaries.

## Next priority

Correct analytical time/coverage and audit attribution first (F1–F3), then make historical-session retention reclaimable (F4). Add focused regressions at these boundaries before wiring provider live ingress. A rewrite or broad component reorganization is not justified by these findings.
