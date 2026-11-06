# SCRUM-119: immutable detection-time context

Follow-up: SCRUM-96 now uses this capture boundary in production autonomous execution, and SCRUM-110 compares its SQL snapshots with all three chart modes. Earlier references below to autonomous outcome execution as a parity prerequisite were too broad: these stories require outcome-plan-only invariance of X, while production outcome calculation remains downstream and outside SCRUM-96/110.

## Capture boundary

`DetectionTimeContextSnapshot` is the canonical X-side observation for a persisted DetectorEvent. `ReplayEventStore` captures it in the event's SQL transaction from the actual immutable `DetectorRuntimeResult.frame` and an event-aligned PatternInstance observation. No later instance lookup reconstructs analytical values. The persisted source event is independently checked against the supplied event, occurrence and pinned RunSnapshot.

The shared store covers legacy M1 replay and current historical/replay/live analytical streams. EvaluationRun will use this boundary when implemented in SCRUM-96; it is not implemented by this story.

## Version 1 contract

`context_schema_version = detection-time-context-v1`. The frozen record contains snapshot/event/run identity, source event semantic reference, instance semantic key, instrument, candle-labelled detection time, actual `available_at`, and the source event's per-occurrence within-bar ordinal. UUIDv5 of the persisted event UUID and schema version makes repeat capture idempotent. Semantic comparisons exclude snapshot/event/run UUIDs.

Lineage pins dataset revision/input identity, timeframe, DetectionConfigHash, calendar version, config-schema version, build/Git capture information, component versions, detector version and definition fingerprint. For captured live input, the immutable input ID plus the event's availability boundary identifies the consumed prefix. Capture does not depend on a mutable live feed or require its manifest before launch warm-up has committed.

Values contain the completed source candle, completed-bar count, component availability, optional-family status, selected component state, current-bar market events with semantic references, and the exact emitted detector phase/context/rationale. Session, EMA, ATR, provisional swings, current SwingStructure, structural TrendLeg/protected swings, qualification and RangeState are included where configured. ATR/True Range provide the current volatility observation; no new volatility formula is introduced.

`COMPONENT_FIELDS` is the explicit v1 inclusion list. Growing all-history swing/classification/break lists are excluded. Current structural records and current-bar evidence remain included. Optional families report `NOT_CONFIGURED`; configured families report `PRESENT` and their existing per-instance warm-up/availability status. V1 supports the existing version-1 analytical components and pinned session calendar. New component versions or changes to included field meaning/representation require an explicit context-schema decision, including nested structural evidence changes.

Decimal values become finite decimal strings; counts/ordinals remain integers; booleans remain booleans; aware timestamps become UTC ISO strings; enums become ordinary strings; collections become immutable mappings/tuples in memory and JSON objects/arrays on disk. Pattern context remains constrained by its detector definition's typed schema. Arbitrary Python/UI objects, floating-point/nonfinite values, unsupported schemas and outcome-side fields are rejected.

## Same-candle transitions and time

Runtime results expose an observation aligned with each emitted event. A single transition uses that step's detached context. Multi-transition detectors supply explicit contexts per transition; continuation supplies separate reclaim/terminal observations. A legacy/custom multi-transition detector without those observations reports `context_status = UNAVAILABLE`, retaining the exact transition and rationale instead of substituting final context. All current MVP detectors provide available context.

Existing detector `detection_time` labels the completed candle's start. Snapshot `available_at` records when its close became observable. Higher-timeframe streams pass the aggregator's actual availability boundary, including scheduled shortened candles; the store does not guess their duration. The legacy M1-only pipeline uses label plus one minute. Datetime observations, including datetime text inside typed evidence, must not exceed the emitting candle label. Later qualification, structure events, bars and outcomes cannot supply values. This guard supplements causal runtime/frame provenance; it cannot infer provenance from an arbitrary numeric value supplied by a separate producer.

## Persistence and access

Migration `20261106_16` adds `detection_context_snapshots` with source event/run foreign keys, one record per event/schema, canonical payload and verified content hash. SQLite/PostgreSQL triggers reject UPDATE/DELETE. Identical capture returns the original; conflicting capture fails. Downgrade refuses to discard used context and requires restoration of a pre-capture backup. Existing events are not backfilled from current occurrence state.

`GET /detector-events/{event_id}/context` reads durable context independently of an active session. Existing events without snapshots return 404. Normal production migration is required before the new capture path runs; implementation tests do not migrate an installed database.

EvaluationPlanHash and downstream outcome/segment/export settings are excluded from X. A pinned evaluation plan may select the supported context schema; unsupported selections fail capture. Outcome-only plan changes are tested through separately driven shared-runtime executions and SQL capture, and subsequently through the production autonomous driver. Actual OutcomeObservation evaluation remains downstream outcome work, outside SCRUM-96's detector-execution scope.

## Acceptance evidence

Fixtures cover exact minimal values, real continuation phases/references, deliberately different same-bar intermediate contexts, future component/context/evidence values, optional components, schema/type/source validation, plan-only invariance, deterministic semantic capture, future-bar stability, SQL round-trip, corruption, idempotency/conflicts and durable API retrieval. Migration tests exercise used/unused SQLite downgrade and update/delete guards; a disposable PostgreSQL test covers capture, durable retrieval and identical immutability/retention guards.

The SCRUM-110 matrix compares persisted snapshots against a separate causal driver at every emitted event for replay, historical calculation and captured-live execution, including actual higher-timeframe availability. The subsequent SCRUM-96/110 increment adds production autonomous execution and actual outcome-plan-only invariance.

Final verification: **1,037 Python passed (13 skipped), 56 browser passed**, plus the separate real PostgreSQL integration test and the final 14-test schema/context hardening check. Ruff, mypy (99 source files) and diff checks passed.
