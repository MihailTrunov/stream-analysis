# SCRUM-82 — immutable detector events and typed rationale

Decision date: 2026-09-30. The owner approved extending the existing
PatternInstance transition store rather than adding a parallel event table.
This design builds on SCRUM-81's durable occurrence identity.

## Alternatives and decision

Adding a second event table would duplicate lifecycle history and require an
atomic two-table projection. Replacing the transition store would force a
larger migration for no new detector behavior. Extend each existing immutable
transition row into a detector event instead. Its run-scoped `event_id` is a
storage UUID; its `event_semantic_ref` remains the cross-run comparison key.
That key derives from the instance semantic key, lifecycle transition identity,
observable times and deterministic sequence, never the run or event UUID.

## Contract and flow

Each event exposes run, dataset, instrument, timeframe, pattern/version,
instance UUID/semantic key, DetectionConfigHash, calendar/build lineage,
event/detection times, old/new states, trigger/reason, lifecycle sequence and
within-bar ordinal. The latter is the count of earlier transitions of the
same occurrence at the same detection time; deterministic stream ordering also
uses pattern/version and occurrence identity when several detectors emit on a
bar. Event kind is the destination lifecycle state, so candidate, reclaimed,
confirmed, active, completed, invalidated and expired are all first-class.

New v1 detector definitions opt into a strict `detector-evidence-v1` rationale
envelope. It contains one or more immutable evidence items with declared
condition ID, pass/fail/not-applicable status, typed observed value and
threshold, comparison operator, units, source market-event semantic references
and optional typed features. Decimal and timestamp encodings are canonical.
The exact canonical source-event reference is computed from the observed
MarketState event, never a reconstructed lookalike. Older generic rationales
remain readable for legacy fixture definitions; v1 definitions reject them.
The persistence boundary validates on write and load and never updates event
rows after insert. Outcome and evaluation-plan data do not enter semantic
identity or detection-time rationale.

## Failure and verification

Reject undeclared conditions, malformed typed values, invalid source refs,
non-canonical JSON, time reversal, out-of-order or illegal lifecycle edges,
and changed definition fingerprints. A migration backfills storage UUIDs for
existing transition rows without changing their semantic references. Tests
cover all lifecycle kinds, typed evidence round-trip, same-bar chains,
source-event identity, semantic parity across run/UUID changes, evaluation-plan
invariance, immutability and PostgreSQL migration behavior. SCRUM-83/84/85
provide their own market-rule fixtures and rationale contents.
