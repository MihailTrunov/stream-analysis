# SCRUM-65 — replay playback-speed controller

Date: 2026-10-01. Scope: headless playback orchestration over the completed-bar
`ReplayCursor`. The controller changes when bars are requested, never how bars
are analysed. SCRUM-89 owns browser controls and animation.

## Decision

Use a caller-driven controller in the application layer. The UI or service
passes elapsed monotonic time as a `timedelta` when it schedules a playback
tick. One alternative is an internal sleep/thread loop; that complicates
pause, cancellation, test determinism and integration with browser scheduling.
Another is to put playback rate into `SimulationClock`; that would mix wall
time with market-time visibility. Neither is needed for the MVP.

The controller starts paused. Manual `step_one` is allowed only while paused.
`play(bars_per_second)` accepts a positive finite rate; paced ticks accumulate
fractional bar credit and call `ReplayCursor.step_one` once per due bar, in
order. `play_maximum()` removes intentional delay and drains the remaining
cursor through its existing `run_to_end` path on the next tick. Pause stops
advancement immediately and preserves analytical state. Pause/resume and a
rate change discard fractional *wall-time credit* to avoid a surprise burst;
they do not reset the cursor. Repeating the same active speed is a no-op.

A tick returns the number of bars processed, not a duplicate event stream.
The owned analytical pipeline remains the source of MarketState and
DetectorEvents. A tick with no bar due returns zero. End of sequence
automatically pauses. Invalid rate/elapsed values fail before advancement.
If analytical processing fails, the controller pauses, clears timing credit,
and propagates the original error; the ReplayCursor latch remains authoritative.
An exhausted or failed cursor cannot start playback until recovered by its
existing reset/seek lifecycle. The controller does not mutate a persisted
ReplayRun directly or create an independent analytical path.

## Verification

Compare manual, slow, fast and maximum-speed runs over the same seeded bars,
including full MarketState frame and DetectorEvent parity. Test fractional
credit, repeated tick, pause/resume, rate change, end-of-sequence, invalid
inputs, and mid-tick analytical failure with no skipped or duplicated bars.
