# SCRUM-128 — bounded higher-timeframe chart history

The price-only `/analysis/history/{revision_id}` endpoint now selects the source interval for its latest requested completed chart candles before reading prices. It no longer materializes a whole revision or aggregates from its origin. The endpoint response, source checksum, aggregation version and maximum 500-candle output remain compatible.

## Window selection and validation

Walk existing calendar-aligned bucket bounds backward from the requested endpoint, count only nonempty completed buckets, and stop at the requested limit or start boundary. A bucket's label must lie inside the requested range. A partially elapsed final bucket is excluded; scheduled closed buckets do not consume the requested candle limit. Intraday clipping and actual trading-day/DST bounds come from the existing aggregation/calendar implementation.

Stream only the selected M1 interval through authenticated Parquet row groups and the existing `CandleAggregator`. Validate missing leading source minutes before accepting the first bar, preserve the aggregator's interior-gap checks, and verify the final closed interval before advancing to the last completed bucket end. A complete last bucket whose remaining time is a scheduled closure is finalized without fabricated bars. Missing required open minutes fail instead of appearing as an empty chart or a truncated origin.

The result buffer holds at most 500 analytical candles; the aggregator retains at most its pending bucket. After cold authentication, price traversal uses only row groups overlapping the selected window and emits only selected M1 rows; the boundary groups may also decode adjacent rows at Parquet block granularity. First access still verifies the full immutable revision's file/canonical checksums, and calendar selection still examines the intervening calendar buckets. This is a bound on price loading and aggregation, not a constant-time latency claim.

Earlier price gaps outside the selected completed candles no longer force unrelated chart aggregation to fail. This does not change detector initialization or source validation for analysis runs: those still process their pinned source context and report initialized coverage independently. Views containing no complete candles authenticate the source and return an empty price list.

## Verification

Regressions compare selected output with full aggregation for every supported timeframe and prove that only the required final source interval is streamed, with full-sequence loading disabled. They cover fractional endpoints, truncated revision origins, a start inside a bucket, scheduled closure finalization, leading/interior/trailing missing minutes, irrelevant earlier gaps, and 23/25-hour UK daily candles. API checks prove narrowed source reads and rejection of an unfinished final chart candle.

Final verification: **914 Python tests passed, 10 skipped**; **25 browser cases passed**; **46 focused price/session tests passed**. Targeted Ruff/mypy and `git diff --check` passed. The skipped external integration tests remain outside this local/fake-ingress verification. No new migration, detector formula, calendar version or provider connection is introduced.

## Delivery status

The round-three Astra review found no confirmed defects in the preceding shared-range/composite-input increments; its written report is retained under `docs/reviews`. This increment addresses the remaining higher-timeframe price-read path identified there.

SCRUM-128 remains In Progress pending complete chart-facing integration/acceptance. SCRUM-133 is In Progress: the existing local single-picker/replay behavior is partially implemented, while shared request/composite publication, fetch-backed Move to and leftward navigation still need connecting to the chart. The backend history service and immutable input APIs are ready for that integration; this increment does not claim those UI flows are complete or live-provider acceptance has passed.
