# SCRUM-96: autonomous detector execution

`execute_evaluation` executes a fresh immutable EvaluationRun through SimulationClock, CandleAggregator, DetectorRuntime and ReplayEventStore. Chart sessions and the executor call the same completed-candle processing/persistence function. There is no separate detector algorithm or UI pacing loop.

The executor authenticates the physical source and pinned clean build, detector definition/binding and context schema before producing output. It starts at the pinned source initialization origin, carries warm-up state forward, processes canonical M1 minutes once, and derives every selected analytical timeframe from that source. Scheduled closures finalize candles through the ordinary aggregator; unexpected open-slot gaps fail execution. Dispatch order is actual close availability, timeframe duration, detector identity and the native within-bar ordinal. Browsing Live/Manual/Always-on policies do not suppress selected evaluation detectors.

Each bounded source chunk atomically persists detector transitions, event-time X snapshots, primary EventSet membership and per-stream/root progress. The default chunk is 128 source minutes; allowed sizes are 1–2,000. Detector events and contexts are persisted before population filtering. The production executor retains no UI frame history. Internal diagnostic observers receive a completed frame or a persistent past-only ObservableBars prefix, never the clock/full source. The existing source loader and runtime still retain their normal source/event state; chunking is not a claim of constant-memory streaming.

The durable active slot permits one executor owner. SIGINT/SIGTERM requests a stop at processing boundaries; the unfinished chunk rolls back and the run becomes CANCELLED. Other exceptions roll back that chunk, preserve the committed prefix and mark FAILED with attributable diagnostics. No partial run is presented as complete, and neither failed nor cancelled runs automatically resume. Abrupt process-death recovery and full queue scheduling remain SCRUM-123.

Detection completion checks all pinned source/stream counts and final availability. Detector-only runs can become COMPLETED. Runs with eligible events awaiting selected outcomes remain DETECTION_COMPLETED; SCRUM-96 does not calculate outcome formulas. A population with no eligible events has no outstanding event outcomes and can complete. Outcome calculations/aggregation remain their separate stories.

## Explicit local worker

Create a run through `POST /evaluation-runs` using the configured database/source store, then execute its returned UUID in a separate process with the same clean build and environment:

```sh
uv run python -m market_analysis.application.worker evaluation --run-id RUN_UUID --chunk-size 128
```

The process uses `STREAM_ANALYSIS_DATABASE_URL` and `STREAM_ANALYSIS_DATA_ROOT`; it attempts to lower its own CPU priority and writes service/run heartbeats between committed chunks. An interactive chart may run concurrently. The no-ID evaluation service remains a heartbeat process until SCRUM-123 implements durable queue claiming; submission does not automatically launch an evaluation. This delivery does not add queue controls, a Runs UI or installed-database migrations.

## Verification contract

Unit/integration checks cover real transitions and snapshots, source-prefix future guards, chunk boundaries, cancellation, build/source rejection, downstream-outcome status, concurrent chart stepping, actual worker subprocess success/failure logs and PostgreSQL completion/rollback. SCRUM-110 adds the actual autonomous adapter to the four-mode matrix, including all MVP detectors/components, seven timeframes, long DST/session fixtures, fresh-run determinism and executed outcome-plan-only invariance.
