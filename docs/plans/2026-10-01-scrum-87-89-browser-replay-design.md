# SCRUM-87–89 — offline browser replay launch, chart and controls

Date: 2026-10-01. Scope: local single-user MVP over immutable published M1
dataset revisions and registered detector versions. The old two-bar installation
check remains explicitly separate and non-research-grade.

## Architecture

The API owns one active in-process walkthrough session. Launch first verifies
the selected revision and checksum, exact instrument/timeframe membership,
versioned calendar, resolved DetectionAnalysisConfig/hash, required warm-up
history, and absence of unexpected open-minute gaps in warm-up or visible
range. It creates a pinned ReplayRun and a `ReplayPipeline`, returning metadata
at cursor `-1`: no candle or event is observable yet. The browser never receives
the full source sequence or calculates indicator/detector results. The local
API process retains the analytical state while it is alive; restart requires a
new launch. Persisted run rows remain an audit trail, not a serialized
analytical checkpoint.

For seek/reset, a persisted ReplayRun cannot move its cursor backwards. The
server aborts the old run, creates a new run ID with the same immutable inputs,
then causally steps from bar zero through the requested target. Configuration
edits require stopping the current session and launching a new run. Manual
step, play ticks and next-event navigation all call the same pipeline step
path; next-event uses `run_until_event` and does not pre-scan future output.

## Offline data and browser presentation

Ship an idempotent, clearly non-research-grade US30 and DAX Parquet seed with
a small real compression-detector fixture and a registered default config.
The seed is available on a fresh local stack without OANDA credentials.
Imported immutable revisions appear alongside it when complete. The launch
form selects revision, instrument, timeframe, interval and versioned config;
it shows source ID, required warm-up and resolved hash before starting.

The chart requests only a bounded, cursor-clamped viewport. Responses contain
canonical OHLC bars at or before the visible cursor, never future data, and
return no bars while the cursor is in warm-up. A simple candlestick view uses
these values without analytical calculations. The browser refreshes the
viewport after seek/reset and uses bounded requests as the cursor advances.
Event feedback is limited to the current/last stop; full annotations,
timeline and inspector remain SCRUM-91–93.

## Verification

API/integration tests cover valid US30/DAX launch, revision integrity,
configuration hash equivalence, invalid intervals, insufficient warm-up,
gaps and calendar failures; chart payload tests assert future isolation and
bounded viewport behavior. Control tests cover step, play/pause, speed,
seek/reset with new run identity, next-event, final state and output parity.
Browser smoke covers launch, causal candle reveal and controls using the
offline seed. The user-facing error includes the failed preflight condition.
