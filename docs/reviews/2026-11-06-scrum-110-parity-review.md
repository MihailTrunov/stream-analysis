# SCRUM-110 parity foundation and reference identity fix

This is the implementation/self-review record for the current parity increment, not an independent Astra review. Full SCRUM-110 remains In Progress because autonomous EvaluationRun execution (SCRUM-96) and DetectionTimeContextSnapshot capture (SCRUM-119) are absent. The existing manual/history driver is not autonomous evaluation, and persisted occurrence context is not an event-time X-side snapshot.

## Confirmed production defect

The new curated reversal case compared session execution to a separately driven causal runtime with the same source and configuration. The first reported divergence was a candidate source-reference hash after earlier checkpoints matched. A minimal regression using two fresh MarketStateAggregators with different execution run IDs reproduced the mismatch on real EMA/structural events.

`market_event_semantic_ref` excluded the frame's run ID at its top level but hashed structural evidence containing nested `StructureLineage.run_id`, including ended/started EMA segments. This violated the documented run-independent reference identity contract and changed frozen detector rationale/context across independent reruns.

The producer now uses the explicit `market-event-ref-v2` hash domain and excludes only execution run IDs in typed structural lineage, recursively. Dataset/config/calendar/component identity and observable evidence remain significant. Debug/audit serialization retains the complete lineage. Persisted v1 digests and detector versions/configuration hashes are not rewritten. New references intentionally differ from v1; old stored evidence retains its original identity and build attribution.

The original authored detector fixture hashes are preserved. Tests independently reconstruct the documented v1 envelope from full debug evidence and project matching v2 source identities back to that authored envelope. This retains the original evidence/golden checks without regenerating expected hashes from the changed producer. A separate hand-written v2 payload test verifies the new hash domain, semantic-field inclusion and exclusion of execution UUIDs. Real fresh-run tests cover nested EMA-segment lineage.

## Current-mode coverage

- Real replay and requested history directly compare equal. Captured-live execution compares against a separate causal reference chain bound to its actual immutable input identity, preserving opaque source-event hashes rather than deleting them to force equality.
- Curated real reversal/continuation cases cover all eight resolved MVP components and observable candidate/confirmation transitions. Compression cases cover M1, M5, M15, M30, H1, H4 and D1; additional DAX cases cover M1, H4 and D1.
- A concurrent M1/M5 case checks independent analytical streams over shared source minutes. The longer deterministic source fixture exceeds 10,000 M1 candles and crosses the October/November DST changes, weekends and scheduled closes. These are authored/generated offline fixtures, not real-provider acceptance or an EvaluationRun benchmark.
- Source checksum, initialization origin, selected start, finalized availability, calendar, aggregation and resolved configuration/version identity are checked before frame values. Component state/availability, ordered market/detector events, rationale, exact source references, persisted instance semantic keys, within-bar ordinals and final typed occurrence context are compared.
- Live forming data stays outside the analytical prefix, duplicate polling does not repeat evidence, and durable consumption receipts are verified after disconnect.
- Mutation probes verify first differing checkpoint/path/value, source/config/version changes, event ordering, source-reference changes, missing fields/events/checkpoints, occurrence-context changes and explicit path-specific numeric tolerances. Availability `None` remains distinct from zero; nonfinite numeric values fail comparison.

## CI and verification boundary

The normal platform test command is `uv run pytest`, included in `pnpm run ci` and the existing GitHub CI test job. The regression directory is discovered by that command; failures fail the job without a second duplicate workflow invocation. No provider credentials or installed database are required. Branch-protection configuration is not asserted by this test delivery.

Full acceptance still needs real EvaluationRun and DetectionTimeContextSnapshot adapters, actual outcome-only EvaluationPlan invariance and their checkpoint comparisons. The comparator's exclusion of operational EvaluationPlanHash metadata is tested, but that is not claimed as execution-level evaluation-plan invariance.

## Final verification

**1,025 Python tests passed, 12 skipped; 56 browser tests passed.** Ruff, mypy (91 source files) and diff checks passed. The local skipped PostgreSQL integration checks were not represented as executed. No provider, installed database, schema migration or UI feature change was needed for this increment. SCRUM-110 remains In Progress for the missing evaluation/context execution paths.
