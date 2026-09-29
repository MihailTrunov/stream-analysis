# SCRUM-61 — complete replay execution evidence

Date: 2026-09-29. Scope: continue the partial implementation of SCRUM-61's transferred execution acceptance. The existing lifecycle/snapshot design in [the Sprint 2 replay design](2026-09-27-sprint-2-replay-design.md) and Jira comment 10753 remain authoritative. SCRUM-63, SCRUM-77 and SCRUM-80 now provide the causal cursor, observable frame and shared detector runtime.

## Boundary and chosen approach

Continue `application/replay_pipeline.py` as an application-level adapter joining the existing primitives. Do not add a second analytical implementation or put SQLAlchemy into domain/indicator/detector code. Reusing only the lifecycle's `step_replay` would not meet acceptance because it advances clock/cursor without executing components. Checkpoint restoration is also unsuitable: the approved MVP recovery contract starts a fresh run, not an invented partial-state resume.

The seeded detector used by tests is a stand-in exercising the existing canonical market-event and lifecycle interfaces. It is not an implementation or approval of SCRUM-83's reversal formulas. Browser controls, chart launch, outcomes, provider import, the Parquet content store and autonomous evaluation remain their own Stories.

## Verified inputs and causal stepping

Construction loads the immutable ReplayRun snapshot, resolved DetectionAnalysisConfig/DetectionConfigHash, dataset revision/checksum, instrument, calendar version and build identity. The caller supplies canonical bars until the dataset store is available. `load_bar_sequence` checks that content against persisted lineage before construction; changed prices must fail even when they form individually valid candles.

The replay boundary resolves the exact pinned calendar and supplies it to the shared MarketState/DetectorRuntime. It must not silently omit session processing when calendar resolution is absent. Each construction calls `bindings_factory` once to obtain fresh, exclusively owned detector instances, then resets the new runtime before recording continuity generations. The factory must never hand out a detector owned by another run; accepting and resetting caller-supplied live bindings would corrupt that other run. A fresh in-memory driver cannot be attached to a previously advanced persisted cursor: metadata reload is supported, but resuming analytical execution from that cursor is not.

The flow for one step is:

`verified pinned run → past-only ReplayCursor view → shared DetectorRuntime → successful persisted cursor advance`

Every completed bar is processed exactly once and in source order. The existing SimulationClock marks bars before the selected interval as warm-up and does not expose later bars through the observable view. Warm-up initializes the same components/detectors; crossing the visible boundary does not reset state. The execution fixture demonstrates this causal behavior, not completion of the later browser/queue launch preflight or validation of every experimental detector threshold.

Before stepping or completing, verify lifecycle/clock parity and runtime continuity. Check aggregator progress, each owned component's reset generation/count/last bar, and the runtime's processing-attempt generation: `process_frame` can advance detectors without advancing the owned aggregator, and even a failed external attempt can invalidate analytical state. A direct session-component reset can leave the aggregator's count unchanged, so the component checks matter independently. Calling an exposed runtime's reset/process methods must not silently reset or advance a persisted replay behind the driver's back. Pausing and resuming the same in-memory driver preserves analytical state; paused/terminal runs cannot step. Completion requires exhaustion and successful processing, not merely a clock that consumed the last source bar.

## Failure, recovery and transaction ownership

The analytical call and database cursor advance share a savepoint. If analysis or the cursor write fails, roll back the cursor write, latch the driver, record a structured execution error and transition an eligible active ReplayRun to FAILED with a concise reason. The failed bar may already have advanced the in-memory clock/components; do not claim a rollback of analytical state or publish that partial step as a successful result. Committed detector-event history remains distinguishable from unprocessed/failed state.

After a failure no further stepping, completion or in-place reset is allowed. Recovery creates a new run ID with fresh analytical state over the same pinned dataset/configuration, without rewriting the old snapshot. The failure timestamp is a UTC lifecycle timestamp consistent with the run's creation time, not an invented analytical event.

The application caller owns the outer transaction. A caller catching an execution failure must commit its enclosing transaction to retain the FAILED record; rolling that transaction back also rolls back its database changes. Tests must establish committed failure/reload behavior, including PostgreSQL savepoint semantics. If the database itself becomes unavailable, no in-memory latch can guarantee a durable failure write; surface the persistence error instead of reporting successful persistence.

## Diagnostics and acceptance evidence

Success records carry run, dataset, instrument, build and component/binding context plus the processed bar index/time and visibility. Failure records add the responsible detector identity where applicable and an actionable error/recovery description. Use the existing SCRUM-112 structured logger; logs are diagnostics, not substitutes for canonical DetectorEvents or persisted lifecycle state.

Verification covers lifecycle/immutable snapshot reload, exactly-once stepping, warm-up before visible results, no-look-ahead negative access, current-bar canonical events, same-input fresh-run parity after removing only run-scoped identity, pause/resume, checksum/calendar rejection, runtime continuity, failed final-bar rejection, persistence failure rollback, durable failure reason and repeat recovery under a new run ID. PostgreSQL integration and hosted browser smoke complement the provider-free unit fixture.

Implementation uses GPT-6-Sol at medium effort, followed by independent GPT-6-Sol x-high review. Resolve high/medium findings, run the uncached checks, then commit only the SCRUM-61 scope with the implementation/review/fix details, push, verify the exact hosted workflow and update Jira. SCRUM-122's manual fresh-checkout acceptance is unchanged.

## Review and verification record — 2026-09-29

Review scope: the five SCRUM-61 replay wiring, lineage and test files, the small shared detector-runtime continuity change, and this design against Jira's transferred execution acceptance and the approved 2026-09-27 replay design. The independent x-high review reproduced two medium issues: external `process_frame` could advance detectors outside the replay cursor yet still permit completion; and reusing a mutable detector object could carry state into a nominally fresh run. The same continuity review reproduced direct session-component reset/update bypassing the aggregator-level count. No high finding was raised.

Resolution: the runtime exposes monotonic processing-attempt generation; the replay driver verifies it and each component's reset generation, completed count and last bar before stepping or completing. Construction uses a once-called factory for exclusive per-run detector instances and initializes them through the detector reset protocol. Regression cases exercise real out-of-range frames (with and without events, successful and failing), direct session reset/update before another step and after the final bar, and stateful-detector repeat/failure recovery. The independent reviewer confirmed no remaining high or medium findings after those fixes.

Final local uncached Nx suite with the dedicated PostgreSQL test database: 581 Python tests passed with no skips, one web unit test passed, all Ruff and mypy checks passed (55 source files typed), and both project builds passed. The browser smoke, pushed GitHub workflow and Jira transition are checked separately from this local test record. This fixture is not a full browser replay, OANDA import, dataset Parquet store, launch preflight, detector-rule implementation, autonomous evaluation, or SCRUM-122 manual fresh-checkout acceptance.
