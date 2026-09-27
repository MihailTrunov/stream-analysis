# MVP Sprint 1 implementation design

Scope: SCRUM-55, SCRUM-58, SCRUM-59, SCRUM-68, SCRUM-79, and SCRUM-106. SCRUM-122 remains in progress pending the user's fresh-checkout validation. This design consolidates the approved Jira contracts and `CONTEXT.md`; it does not change detector rules or the MVP scope.

## Approach

Two possible cuts are provider-first (build OANDA ingestion immediately) and contract-first (build the canonical interfaces and seeded/offline path first). Use contract-first, consistent with the approved seeded vertical slice and the hard dependencies. Do not implement SCRUM-56/57 or mutable shared bar persistence in this sprint.

## Boundaries and data flow

- Historical acquisition consumes a provider-neutral `HistoricalDataSource` contract. Requests use UTC, an explicit half-open interval, canonical instrument/timeframe identity, and deterministic ordered results plus source metadata. Fake providers and later OANDA adapters implement the same contract. Provider retry/rate-limit policy stays inside adapters.
- PostgreSQL persistence owns instruments and dataset metadata/lineage. SCRUM-124 owns the immutable Parquet bar revisions and canonical bulk range reads. No schema or repository method may make a mutable shared bar table the research source of truth.
- Dataset validation consumes canonical bars and an injected, versioned market-calendar interval contract. It reports structured, deterministic findings without repair. No unverified US30/DAX breaks or holidays are invented here.
- Incremental market-state components consume one completed canonical bar at a time, reject out-of-order input, expose read-only deterministic state/debug serialization, declare warm-up requirements, and reset cleanly. Core state code imports no API, ORM, provider, or browser code.
- Pattern lifecycle definitions supply their permitted states, transitions, triggers, terminal semantics, and simultaneous-condition precedence. A shared validator/runner rejects undeclared transitions and records every permitted same-bar transition in order. Price formulas stay in later detector Stories.
- Detection and evaluation hashes derive from fully resolved immutable configuration and canonical UTF-8 JSON; persist the algorithm/version and canonical payload alongside the digest. UI display settings are excluded; dataset/calendar/build identity remains separate lineage.

## Verification and remaining gates

Add focused unit/contract tests for empty/partial provider results, ordering, range boundaries and errors; migration plus repository tests against PostgreSQL; duplicate/gap/flag/OHLC/calendar validation fixtures; component reset/ordering/parity; lifecycle invalid graph, terminal, precedence and same-bar cases; and exact hash golden fixtures. Run the repository lint, type, unit, integration, and browser checks relevant to touched code.

SCRUM-122 cannot be marked Done until the user completes the deferred fresh-checkout test. Instrument-specific calendar rules remain an open implementation decision for the affected import/session/outcome Stories. Sprint dates are planning bounds, not evidence that acceptance criteria are met.
