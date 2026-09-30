# SCRUM-81 — durable PatternInstance occurrences

Decision date: 2026-09-30. The owner chose a persistence-first boundary. This
Story will provide durable multi-occurrence identity, lifecycle and typed
context without changing SCRUM-80's current one-occurrence-per-binding detector
API. Concrete SCRUM-83/84/85 detectors can adopt the store when they are built;
support for concurrent occurrences emitted by one detector remains a separate
runtime integration change, not a claim made by this Story.

## Alternatives and choice

One table with arbitrary JSON state is quick but cannot validate context
versions, semantic references or legal transitions. Refactoring the detector
runtime to emit several occurrences now would satisfy concurrency earlier but
would change every detector interface before concrete detectors exist. Use a
run-scoped occurrence table plus ordered transition records and a strict
repository/service boundary. This stores several same-pattern occurrences,
including overlapping ones, independently of the current runtime's slot count.

## Identity and lineage

The database `instance_id` is a run-local UUID. Analytical equality uses a
versioned SHA-256 `instance_semantic_key` over immutable dataset revision,
DetectionConfigHash, instrument/timeframe, pattern id/version, definition
fingerprint, observable occurrence-start time and a caller-supplied deterministic
within-start ordinal. Run UUID, generated UUID, insertion time and build ID
are excluded from that key. A unique `(run_id, instance_semantic_key)` constraint
rejects duplicate occurrences within a run. The ordinal separates distinct
same-pattern occurrences starting on the same completed bar; callers must
derive it from deterministic detector emission order, never a database count.
The row pins the run snapshot's dataset, calendar, build and config lineage,
plus the executed detector binding fingerprint.

## State, context and transitions

An occurrence stores its current declared lifecycle state, optimistic row
revision, last processed completed-bar time and a canonical typed context
document. Only fields declared by the registered PatternDefinition context
schema are persisted; undeclared in-memory debug values are not persisted.
Present fields validate strictly as `int`, `decimal`, `bool`, `string`, UTC
`datetime` or `event_ref`. An event reference is a deterministic semantic
digest of a canonical observable market event, not a Python address or DB UUID.
The context document carries a schema/fingerprint pin; reading with a different
PatternDefinition version or semantic fingerprint fails closed. Missing fields
are allowed until their detector phase populates them; the detector Story owns
phase-specific requiredness.

Each accepted completed-bar update atomically compares the expected revision,
validates the transition chain against SCRUM-79, writes any ordered transition
rows and context, then advances row revision. Transition rows hold event_time,
detection_time, from/to state, trigger and a deterministic semantic event
reference for SCRUM-82 to reuse. Multiple transitions on one bar retain one
instance UUID/key and contiguous sequence numbers. Terminal states cannot
advance. Phase timestamps are derived from the immutable transition history,
so candidate/reclaim/confirmation/active/completion/invalidation/expiry times
remain auditable without detector-specific columns. No transition may be
backdated into observable state: detection_time is the completed-bar time.

## Restart and verification

Loading validates row lineage, definition fingerprint, context types, ordered
transition chain and terminal invariants. The result contains all persisted
detector state needed for the next identical bar; no hidden counter or frozen
reference is accepted as the only copy. Tests cover same-occurrence replay vs
evaluation parity despite different UUIDs, distinct same-start ordinals,
context round-trip for the three planned detector shapes, frozen event refs,
same-bar chains, stale-revision conflicts, incompatible definitions, terminal
protection and next-step parity with fixture detectors. PostgreSQL migration
tests exercise the same constraints. Actual SCRUM-83/84/85 detectors and their
rule-specific restart tests remain in those Stories.
