# Chart detectors without confirmed calendar coverage

The chart previously fetched provider prices independently of calendar coverage but rejected Calculate current view, replay and live detector initialization for those prices. The approved policy now permits chart detectors whenever completed provider candles are available. This supersedes the detector/replay calendar gate documented in the earlier calendar-independent price and latest-available startup reviews.

## Implemented behavior

- Live preparation outside verified coverage snapshots the latest completed provider candles and attaches the normal incremental detector runtime. Newly completed candles advance it once; forming candles remain display-only.
- Historical manual/always-on calculation and replay can use provider-price windows. Replay evaluates enabled detectors as candles advance. Normal initialization and timeframe applicability checks remain in force.
- The immutable input has warning validation status and a distinct `provider-observed-v1:<base-calendar-version>` identity. Its provenance records unclassified missing intervals. Actual source candles, canonical checksums, account/feed isolation and conflicting-redelivery checks are preserved.
- The observed calendar supplies timezone and trading-day alignment metadata without asserting verified sessions, holidays or closures. Analytical aggregates use `m1-provider-observed-aggregation-v1`, actual M1 inputs and explicit `calendar_unverified` / `source_coverage_unclassified` quality flags. Missing timestamps do not create synthetic candles or stop processing. A truncated first higher-timeframe bucket is still skipped.
- The UI explains unverified calendar/session coverage and permits calculation. It does not draw unverified session-boundary markers. Existing verified profiles and their strict completeness checks remain unchanged.
- Observed live minutes and receipts remain durable in the existing journal. They are not published as verified PASS hourly coverage. Explicit live captures use warning validation status.

## Initialization and HTTP timeouts

Provider snapshots are prepared separately from detector initialization. The browser requests initialization with `Prefer: respond-async`; the API immediately returns HTTP 202 and a task ID. Polling `/analysis/tasks/{id}` uses an independent lock, so long detector warm-up does not hold a proxy request open. Tasks are bounded and ephemeral, with two workers and cancellation cleanup. They are distinct from durable autonomous analysis jobs. Legacy callers without the preference retain synchronous behavior; interrupted initialization can be retried after server restart.

## Verification

- 208 targeted Python checks passed across calendar, aggregation, price history, ingress, live analysis/retention, session orchestration, provider-price/source handling and compact chart observations.
- Three task/protocol checks passed, including polling while detector work is blocked and cleanup after cancellation. An additional observed-aggregation regression passed for a five-minute bucket containing only three actual source minutes.
- Four cross-mode checks passed: curated detector/component chains, concurrent M1/M5 state and autonomous outcome-plan invariance.
- All 79 web unit tests passed. Twenty-three affected browser scenarios passed across provider-price navigation, manual calculation, observed live initialization/update, historical backpressure, live history and causal replay navigation. One newly added test initially used the wrong control label; the corrected scenario passed.
- Ruff, mypy (131 source files), TypeScript, ESLint and the production web build passed.
- An authenticated, read-only OANDA check fetched 1,440 completed candles for each of US30 and DAX into temporary SQLite/Parquet storage. Both instruments initialized M1 and M5 detectors through the asynchronous API with `calendar_status: unverified`. Acceptance returned in 3–5 ms; initialization completed in 11.30 / 12.69 seconds. Maximum observed task-poll latency was 84 / 267 ms. Both views contained calculated observations. Installed application data and containers were untouched.

The full Python suite and an independent agent review were not repeated for this change. Restart through `pnpm start` to rebuild/recreate the installed API and web services; restarting an existing Docker container alone does not install these code changes.
