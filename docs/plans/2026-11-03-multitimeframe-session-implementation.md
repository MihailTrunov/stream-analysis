# Multi-timeframe sessions: implementation contract

Owner direction: merge the chart workspace to master, continue on master, and
proceed with the recommended four-task implementation sequence. This accepts the
recommended UTC intraday / instrument trading-day daily alignment. No additional
feature branch is used.

## Versioned semantics

`m1-aggregation-v1` builds 1m/5m/15m/30m/1h/4h/1d from finalized M1 input with
exact Decimal OHLC. Intraday intervals use UTC epoch clock boundaries. Clip an
interval at instrument trading-day boundaries; such candles carry a
`boundary_shortened` flag. Daily bounds use the calendar's local trading-day
boundary and therefore have their real elapsed duration through DST. Scheduled
closed minutes are excluded from required coverage, never synthesized. A bucket
with no expected open minutes produces no candle. Missing expected-open minutes
invalidate a bucket and fail the analytical stream; no partial gap candle is fed
to detectors. Truncation at the beginning/end of a supplied revision is incomplete
coverage, not a shortened trading-day bucket.

Candle labels remain interval starts. `available_at` is the interval end, separate
from the last required source minute. A caller advances the finalized-source
watermark; a higher bar cannot be observed before that watermark reaches its end.
Input coverage must start no later than the bucket's first expected-open minute.
M1 events keep their existing source-start timestamps. The session event envelope
adds `available_at`, leaving recorded detector evidence unchanged. Cross-stream
ordering is `(available_at, timeframe duration, pattern id/version, local emission
order)`. Presentation ordering is separate.

Every analytical stream initializes from the same pinned source revision origin,
not the viewport start. Warm-up requirements count completed bars of that
stream. Replay starts paused after processing preceding initialization data;
insufficient history is an explicit launch error. Chart timeframe is independent
of selected analytical streams. One Step completes the next displayed candle,
processing all intervening source minutes and analytical closes.

Existing M1 qualification/reversal/continuation v1 restrictions remain enforced.
Compression v1 and its ATR/range components use their declared input-bar counts;
users explicitly select those timeframes and parameters. No thresholds are copied
and represented as validated trading recommendations. Unsupported combinations
are rejected before starting a stream.

Existing outcome v1 horizons retain M1 units and same-trading-day eligibility.
Higher-timeframe event envelopes do not authorize overnight outcomes or include
pre-availability OHLC. A new outcome definition/version is required for another
unit/day policy. Numeric outcome execution remains its existing evaluation story.

## Sessions and UI

A tab-local session owns immutable source/aggregation/calendar/config/build lineage
and independent runtimes. Reuse the existing DetectorRuntime, MarketStateAggregator,
ReplayEventStore and persisted event/review boundary; no new detector formulas.
Session configuration revisions are separate from detector code versions. Replay,
manual history and automatic history use the same engine from the same origin.
Per-detector Live/Manual/Always-on policies gate automatic mode scheduling;
replay and explicitly requested calculation run enabled detectors regardless of
policy. Apply requires Stop first and creates new attribution rather than mutating
existing runs. Provider ingestion/durable live retention remain SCRUM-136/137;
the policy dispatcher must be testable with finalized fake ingress independently.

Project recorded transitions as occurrences at the viewed right edge, capped by
replay availability. Developing includes candidates, Active includes ongoing
confirmed compression, Completed includes terminal/reversal/continuation confirmed
states. Include earlier-started ongoing occurrences, preserve transitions and
terminal reason, and independently sort sections newest first. Off-chart-timeframe
badges and selection do not change the chart timeframe. Evidence and manual audit
remain separate actions.

Provider finality, immutable correction revisions and verified-calendar extension
retain the contracts in the SCRUM-127 review. Do not claim provider live acceptance
or later-date verified coverage merely from these local session changes.

## Verification

Use exact hand-calculated OHLC/availability fixtures, missing-minute and scheduled
break cases, UTC/DST/trading-day boundaries, batch/incremental parity, supported
config validation, tab isolation, display-timeframe invariance, policy scheduling,
as-of projection without future leakage, persisted lineage/review checks and
browser integration. Existing M1 golden tests remain compatibility checks.
