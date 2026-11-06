# SCRUM-120: versioned downstream outcome framework

The framework provides frozen `OutcomeDefinition`, typed `OutcomeObservation`, explicit status/window/reference/lineage records and `evaluate_outcome` as the application entry point. Metric-specific production formulas remain SCRUM-97–100; fixtures supply deliberately small calculation callbacks to verify the common contract.

## Execution and leakage boundary

The source DetectorEvent must exist in SQL and have its immutable SCRUM-119 snapshot before evaluation. The source run pins an EvaluationPlan selecting the registered outcome/version and normalized parameters. Source event, snapshot, immutable dataset lineage/checksum, calendar and configuration identities must agree. Changing a horizon changes EvaluationPlanHash and outcome semantic identity without changing detection identity or X.

Only downstream outcome orchestration reads future data. Calculation callbacks receive the declared M1 bar window, eligible canonical-event references and a detached immutable copy of detection-time context. They do not receive the full source sequence or detector runtime. Result values have explicit NUMERIC (finite Decimal), BOOLEAN, CATEGORICAL or EVENT_REFERENCE types. Categories must be declared. A referenced event must belong to the supplied compatible event inventory and bounded observable order; inventory producers remain responsible for supplying canonical runtime events. Outcome calculation does not recreate structural events from hindsight.

AVAILABLE requires complete window coverage and a correctly typed value. INSUFFICIENT_DATA, NOT_APPLICABLE and AMBIGUOUS require a reason and carry no zero-filled value. Same-bar dual-threshold fixtures report ambiguity without assuming an OHLC path. Callbacks return typed `OutcomeResult` values, including explicit ResultStatus; arbitrary objects/dictionaries/floats are rejected.

## V1 window semantics

`m1-same-trading-day-v1` counts completed canonical M1 bars. FROM_DETECTION_TIME is the default and uses the source snapshot's actual candle-close availability and completed analytical reference close. Its half-open OHLC window starts at availability and ends after the Nth expected open minute. Higher-timeframe source candles therefore contribute no pre-availability OHLC to future observations.

Expected slots follow the pinned instrument calendar. Scheduled closures/breaks do not create synthetic bars; unexpected missing open minutes return INSUFFICIENT_DATA without extending the window. A horizon crossing the source trading-day boundary is insufficient, including a daily candle closing at that boundary. Horizon unit/day policy cannot be inferred from the displayed chart timeframe.

Explicit FROM_EVENT_TIME mode requires a retrospective/descriptive label and currently supports M1 source events. It references that source minute's completed close and starts after that minute; its same-day boundary belongs to the event-time trading day. Other retrospective source-timeframe contracts require separate versioned support.

Event references preserve event_time, candle-labelled detection_time, actual available_at and within-bar ordinal. Within a stream, market-state events precede detector events at an equal close; same-close detector references must follow the source ordinal. Later event availability may equal the outcome window end, when its last completed minute has become observable.

## Immutable persistence

Migration `20261106_17` adds `outcome_observations`, source event/run/context foreign keys, retained definition descriptors, semantic identities, canonical typed JSON and verified content hashes. Identical recalculation is idempotent; conflicting recalculation fails. SQLite/PostgreSQL triggers reject UPDATE/DELETE, and used observations prevent destructive downgrade. `load_outcome_observation` supports durable typed retrieval independently of a chart session.

The EvaluationPlan decoder now reconstructs tagged parameter types without changing existing canonical payloads/hashes. Decimal parameters retain normalized identity across serialization. No production outcome metric or result UI is implied by these framework types.

## Migration correction

The new predecessor-retention test reproduced SQLite's legacy DDL transaction behavior: downgrading an empty newer table could commit that DROP before an older used-context guard rejected the requested chain. The migration runner now uses an explicit encompassing transaction and begins SQLite DDL transactionally. A failed chain retains both schema head and the newer table. PostgreSQL continues to use its normal transactional DDL.

This increment does not migrate installed databases or access providers. The authorized sequence continues with immutable EvaluationRun/EventSet models (SCRUM-95), the shared autonomous detector executor (SCRUM-96), and its SCRUM-110 parity adapter. Full queue scheduling and production outcome formulas retain their own stories.
