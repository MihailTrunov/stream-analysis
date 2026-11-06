# Outcome and autonomous evaluation implementation sequence

Owner direction: continue the recommended sequence on master after the independent Astra review. Use the existing approved contracts; do not add a second detector engine.

1. **SCRUM-120:** frozen versioned outcome definitions/results; explicit typed outputs/statuses; bounded canonical-M1 windows from source close availability; same-trading-day eligibility; immutable SQL observations and source/config/code lineage. Formula callbacks receive detached detection-time context and only the declared observation window. Fixtures demonstrate the four output kinds; production metric formulas remain their own stories.
2. **SCRUM-95:** immutable multi-stream EvaluationRun inputs and EventSet criteria/membership. Validate source/config/definition/schema references before execution. EventSet predicates can read only approved event/X fields after context capture. Preserve per-stream hashes and source/aggregation/calendar/initialization identity.
3. **SCRUM-96:** chronological autonomous execution through SimulationClock, CandleAggregator, DetectorRuntime and ReplayEventStore. Bounded transactions, durable progress/failure and exactly one active execution. A dedicated worker entry point executes an explicitly selected run. The complete queue/reorder/recovery UI scheduler remains SCRUM-123, which depends on this executor.
4. **SCRUM-110:** extend the existing actual-driver matrix to autonomous execution, including plan-only invariance, phase/context identity, deterministic ordering and failure diagnostics. Do not confuse historical UI calculation with this driver.

Existing `m1-aggregation-v1` fixes UTC intraday/instrument-day boundaries and close availability. Outcome v1 offsets count canonical M1 observation bars; scheduled closed slots do not create bars, unexpected open-slot gaps return insufficient data, and an outcome cannot cross the source trading day. Higher-timeframe source windows exclude pre-availability OHLC. Retrospective event-time mode must be explicitly labelled and supported, never selected implicitly.

Detection completion and all-stage completion are distinct. A run with requested downstream stages is not reported COMPLETED merely because detector execution ended. Metric/outcome calculation and full queue scheduling remain their explicitly separate contracts; their absence must stay visible in status and documentation.

Each logical increment includes meaningful contract/SQL tests, documented evidence, Jira status reconciliation and a separate descriptive commit. Installed databases and provider services are not changed by implementation tests.

Delivery checkpoint: SCRUM-120 and SCRUM-95 are committed on master. SCRUM-96 now supplies real explicit-worker execution, and SCRUM-110 includes its actual adapter plus executed outcome-plan-only invariance. See the individual implementation/review records for final verification. Production formulas and full queue scheduling retain their separate scope.
