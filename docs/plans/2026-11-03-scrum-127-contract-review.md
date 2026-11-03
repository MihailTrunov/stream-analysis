# SCRUM-127 — multi-timeframe contract review

Status: review draft, not an approved aggregation/runtime implementation contract.
The candle-anchor question has been presented to the owner while SCRUM-132
layout work proceeds independently. Keep SCRUM-127 open until its decisions
and hand-reviewed golden expectations are finalized.

## Confirmed constraints

- Canonical retained source is completed one-minute history. Supported analytical
  and display intervals are 1m/5m/15m/30m/1h/4h/1D, selected independently.
- Derive higher bars consistently across live, exploration, replay and evaluation.
  Evaluate only completed analytical bars. Display timezone defaults to UK time
  and does not redefine analytical boundaries.
- Use per-tab/per-timeframe resolved detector settings and immutable source,
  configuration, calendar, aggregation and implementation attribution.
- Existing M1 behavior and already-recorded results must not change silently.
  One-minute outcome defaults and same-trading-day limits have not been replaced.

## Findings from the implementation

1. `Timeframe` currently defines M1/M5/M15/H1/D1; M30/H4 need an explicit schema
   extension. Merely showing those choices does not make them supported.
2. `config/resolution.py` and `indicators/trend_leg_qualification.py` both enforce
   M1 qualification v1. Its registered duration parameter explicitly counts M1
   bars. Preserve that restriction until an independently versioned higher-timeframe
   contract is authored, or reject dependent unsupported detector selections.
3. TrendLeg duration is counted from canonical component bar indices, so changing
   its input interval changes duration meaning. Do not quietly reinterpret the
   existing 30-minute baseline as 30 five-minute bars or reuse points thresholds
   as validated higher-timeframe defaults.
4. Replay preflight validates a minimum history amount, but the current pipeline
   processes the supplied revision prefix before the selected interval. Viewport
   start alone is not a sufficient initialization or cache key: path-dependent
   structure may differ when the origin changes.
5. Source candle timestamps are interval starts. Existing v1 detector/context
   timestamps also use source bar labels. Higher-timeframe completion must be
   gated by processed source coverage, not the aggregated bar's start label.
   Define explicit interval end/availability and versioned event-time mapping
   before cross-timeframe event sorting or as-of projection is implemented.
6. Verified OANDA calendars cover analytical start dates 2023-09-27 through
   2026-09-28. Local live monitoring in later dates requires newly verified
   calendar versions; no extrapolation or edits to pinned versions are permitted.

## Proposed contract decisions for review

### Candle construction and availability

Pending owner answer: recommend UTC clock anchors for intraday intervals and
instrument-calendar trading days for 1D. An alternative is session-start anchoring
for all intervals. The chosen anchor belongs in aggregation identity, not the UI.

Use half-open source ranges. Open is the first source open, high/low the extrema,
and close the last source close, with exact decimal precision. All expected open
source slots must be present; calendar-closed slots are not synthesized. Explicitly
classify boundary-shortened/partial buckets and never silently treat a missing-open
minute as scheduled closure. Do not let a bucket cross an instrument trading-day
boundary merely because an intraday UTC anchor would otherwise do so; the chosen
partial-bucket policy must cover that case.

Proposed causal example (if UTC anchors are selected): a 5m interval [10:00,10:05)
needs source candles labelled 10:00 through 10:04. Processing only the completed
10:03 source candle cannot expose that 5m bar. Once 10:04 has been finalized and
processed, the interval is analytically available. The display label 10:00 is
not the observation instant 10:05. Decide/version how that availability maps to
legacy detector timestamp fields before writing golden events.

### Initialization and identity

Pin an explicit initialization origin for every analytical stream and include it
in historical reuse identity. Overlapping views sharing that origin must agree;
different origins are not assumed equivalent just because minimum warm-up passed.
Compute requirements in each supported stream's declared units, then resolve the
required source history through the calendar/aggregation contract. Preserve old
M1 schema/hash fixtures; add a versioned session/orchestration snapshot rather than
silently changing old canonical payloads. Define a stable cross-timeframe tie key
that preserves within-stream transition order; it is independent of panel sorting.

### Higher-timeframe parameters and outcomes

Keep existing definitions restricted to their declared supported intervals until
the component/detector schema provides a tested contract for others. A new
configuration value is not a license to change a v1 parameter's units. Extending
timeframe support may need new component or definition versions with explicit
defaults and fixtures; this does not imply that ordinary supported parameter
edits require detector versions.

Recommend keeping existing v1 outcome bar offsets based on the canonical M1
observation stream to preserve their current meaning. A definition using source
analytical bars or crossing trading days must declare/version that policy
explicitly. In particular, a 1D event at a trading-day boundary does not gain
overnight outcomes automatically; existing insufficient-data semantics still apply.
Outcome-window OHLC must not include pre-detection portions of a higher bar.

## Provider finalization: verified documentation finding

OANDA documents that its pricing stream is sampled and can differ between
connections. It is therefore unsuitable as the sole source of finalized OHLC
when parity with historical candles is required. The stream may drive the forming
display; use provider-confirmed completed M1 candles as authoritative analytical
and durable input, using the same price basis as historical acquisition.
[OANDA pricing stream](https://developer.oanda.com/rest-live-v20/pricing-ep/)

The existing adapter requests midpoint, unsmoothed candles. OANDA's candle schema
defines a start timestamp and completion flag; confirmation availability and
request/retry cadence need live-service implementation and account validation.
Do not declare a source minute final solely from a client wall-clock timer.
[OANDA candle definitions](https://developer.oanda.com/rest-live-v20/instrument-df/)

Reconnect catch-up must recover missed finalized minutes and validate expected
open slots before claiming continuous detector state. Identical duplicates may
collapse; conflicting previously retained prices create a revision/conflict path,
never in-place mutation of a run's source. Capture ingestion/receipt time separately
from canonical analytical time. Provider-specific live access is not part of CI.

## Remaining closure checklist

- Owner decision on intraday anchors and explicit partial-boundary bucket policy.
- Versioned availability/event-time mapping, ordering and exact golden examples.
- Approved per-timeframe initialization and supported-detector parameter contracts.
- Explicit outcome units/day-policy applicability, preserving old v1 behavior.
- Source finalization, conflict/catch-up, recent-history publication and coverage
  contracts delegated consistently to SCRUM-128/136/137.
- Preserve existing calendar versions; verified later-date coverage remains a
  real prerequisite for live acceptance, not a documentation-only assertion.
