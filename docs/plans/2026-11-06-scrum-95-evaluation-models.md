# SCRUM-95: immutable EvaluationRun and primary EventSet

## Copied analytical inputs

`evaluation-run-v1` pins authenticated immutable M1 source revision/checksums, selected interval, source initialization origin, aggregation/calendar identity, clean committed build identity and copied detector/timeframe settings. Later tab/preset edits cannot change these rows. One requested setting per timeframe expands into per-detector streams exactly as the chart workspace does: resolve the complete selected component set, retain it, then pin one enabled detector per child RunSnapshot with its own DetectionConfigHash and EvaluationPlanHash. Browsing policies are not evaluation inputs.

Preflight verifies physical dataset contents and lineage, supported definitions/timeframes, copied-plan hash binding, calendar identity, context schema, outcome references and per-stream warm-up/expected completed candles. Invalid inputs fail before run creation. Segmentation/export execution and selective context capture are explicitly refused until their contracts are registered; v1 captures the full approved detection-time schema. Production metric definitions are registered by their separate stories; no placeholder formulas are advertised.

The immutable manifest retains original canonical copied settings and exact effective per-stream snapshots, pattern/binding/outcome-definition fingerprints, expected candle counts/last availability and both hashes. Semantic input identity excludes persistence UUIDs and capture-method bookkeeping. Primary EventSet identity excludes downstream outcome choices, while the full evaluation identity includes them.

## Versioned population selection

Existing `evaluation-plan-v1` canonical payloads and hashes are unchanged. `evaluation-plan-v2` adds `event-set-v1`: a bounded conjunction of typed predicates over explicitly approved event-native and snapshot fields. Equality, inequality, ordered numeric/datetime comparisons and explicit IS_UNAVAILABLE are supported; arbitrary queries, outcome fields and unknown paths are not. Predicate ordering does not change identity. Tagged canonical operands preserve Decimal/datetime/bool/int distinctions.

An unavailable numerical value does not satisfy a nonzero comparison. Unknown/missing fields do not satisfy ordinary predicates; explicit availability/null tests are separate. Per-detector context paths are validated against the full copied detector set, so a field legitimately absent on another detector is unavailable rather than an execution error.

Ordering is `DetectorEvent -> DetectionTimeContextSnapshot -> EventSet membership -> downstream outcomes`. Membership validates the actual persisted source event and requires its matching snapshot. Primary interval eligibility preserves the approved candle-labelled `detection_time` half-open range `[selected_start, selected_end)`, with actual close availability additionally capped at selected_end. Thus earlier-labelled higher-timeframe events are not reclassified into the primary population merely because their close falls after selected_start. Ongoing earlier patterns may still be displayed by chart views; they do not rewrite this population rule.

All emitted events receive immutable membership/exclusion records, including excluded warm-up events, with canonical emission order and preserved source within-bar ordinal. Read-only views may include excluded events; they cannot mutate the primary population or define it using outcomes.

## Lifecycle and honest completion

States are CREATED, RUNNING, DETECTION_COMPLETED, COMPLETED, FAILED and CANCELLED. A transactional unique active slot allows exactly one detector execution owner; progress and terminal changes require its token. This is an execution ownership primitive, not the full SCRUM-123 queue/reorder/recovery scheduler.

Detection can finish only after the source cursor reaches the pinned end and every stream has produced its pinned candle count/last availability. Full completion also verifies that every source event has membership and every eligible event has each requested immutable, correctly attributed OutcomeObservation. A detector-only plan can then complete; a plan awaiting metrics remains DETECTION_COMPLETED. Failed/cancelled output stays attributable and cannot be marked complete. No automatic detector checkpoint resume is provided.

Migration `20261106_18` adds immutable inputs/stream links/EventSet rows and mutable owned lifecycle/stream progress. Input/population UPDATE/DELETE guards and used-data downgrade refusal apply on SQLite/PostgreSQL.

## Transport and integration boundary

`POST /evaluation-runs` copies/preflights inputs and returns CREATED. `GET /evaluation-runs/{id}` and `/events` retrieve durable state and bounded, ordered event/context/outcome records independently of a chart session. Partial results carry their run status and are not presented as complete. Submission does not pretend to schedule or execute a run; SCRUM-96 supplies the actual bounded worker executor next, and SCRUM-123 owns full queue controls/recovery.

The integration fixture already drives the real analytical runtime/store offline and verifies candidate/active/completed source events, capture-before-selection and pending-outcome completion gates. It is an explicit test harness, not a production autonomous executor. The actual-driver parity/failure/worker checks follow in SCRUM-96/110. Installed databases and provider services are not changed by this implementation.
