# Claude review: context field-table drift risk

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-field-table-drift.md).

Reviewed state: current master (evaluation/context files unchanged since the
SCRUM-101 delivery).

## Verdict

**No current inconsistency; one latent P3-grade drift risk with a concrete
guard proposed.** The two tables agree today; the failure mode if they diverge
is silent population exclusion, which currently has no guard. The proposed
contract test below closes it without coupling the curated predicate table to
the capture schema.

## 1. Consistency today — confirmed

Cross-checked the predicate-approval table
(`application/event_sets.py:15-55` — `NATIVE_FIELDS`, `COMPONENT_FIELDS`,
`field_types()`) against the capture-side inclusion list
(`detection/context_snapshots.py:38-62` — `COMPONENT_FIELDS`, plus
`_VALUE_FIELDS`/`_DETECTOR_FIELDS`):

- Families: both sides cover session, ema, atr, swing_point, swing_structure,
  trend_leg, trend_leg_qualification, range_state. The predicate side's
  optional-family enumeration adds `swing_point` explicitly
  (`event_sets.py:68`), matching the capture-side optional set exactly — and
  capture validates `optional_components` keys against its own list
  (`context_snapshots.py:177`), so an availability predicate path cannot
  reference a family X never captures.
- Every approved component field exists in the capture list: session
  (session_name/is_open/is_break), ema (ema/period/value_ready), atr
  (atr/true_range/period/value_ready), swing_structure
  (overall_structure), trend_leg (active_leg with nested
  direction/duration_bars/directional_movement_points/protected_swing),
  trend_leg_qualification (status, active_qualification with both gate
  booleans), range_state (all five approved score/regime fields).
- Types agree under the documented serialization: decimals are captured as
  finite decimal strings and `_typed` converts them back for decimal
  predicates (`event_sets.py:97-109`); ints/bools/strings round-trip natively.
- Detector-context paths are generated from each registered definition's
  `context_schema` (`event_sets.py:86-93`) with ambiguity rejection — the same
  definitions the capture side serializes, so no second list exists for these.
- `context.availability.<instance_id>` paths are derived from the resolved
  config's `effective_instance_id`, the same identity the capture side keys
  its availability map by.

## 2. Drift scenario — real, and silent in the harmful direction

Adding a captured field leaves the predicate table merely stale (field
unfilterable — capability lag, no correctness impact). But **renaming or
removing** a captured field that the predicate table still approves resolves
to `MISSING` at decision time (`event_sets.py:171-180`): ordinary predicates
evaluate False and events are silently excluded from the primary population
with only the generic `predicate did not match` reason. There is no guard
today: `field_types()` never consults the capture schema, and no test compares
the tables. The SCRUM-119 contract already requires an explicit context-schema
decision for field changes — the risk is that such a decision updates the
capture side while the predicate table is overlooked.

## 3. Proposed contract test

Full derivation of the predicate table from the capture schema is not
appropriate — the predicate table is deliberately narrower and typed (a
curated approved subset, not everything X contains). The right coupling is a
resolution test over a real snapshot:

> Capture a `DetectionTimeContextSnapshot` through the shared store with the
> full component configuration (the existing SCRUM-119 fixtures already
> produce these). For every path in `field_types(config, PATTERN_DEFINITIONS)`
> except `event.*` and `context.detector.*`, resolve the path against the
> captured snapshot values exactly as `event_set_decision` does and assert it
> is not `MISSING`, and that `_typed(value, kind)` accepts it. Additionally
> assert the predicate-side family set equals the capture-side family set.

This fails loudly on both drift directions (approved-but-uncaptured → MISSING;
family set mismatch) and would have caught a rename at CI time. It belongs
beside the SCRUM-119 capture fixtures; no production change is required.

## Independent verification

```sh
.venv/bin/pytest -q tests/unit/test_evaluation_runs.py tests/unit/test_outcomes.py \
  tests/unit/test_detection_contexts.py
```

Passed within the full-suite run on current master (exit 0). Static analysis
plus fixture inspection; disposable SQLite only; no tracked files modified by
the reviewer beyond this verdict.

## Verification limits

The nested `active_leg.*`/`active_qualification.*` sub-fields were verified
against the capture serializers' attribute names rather than by resolving a
live snapshot per path — exactly the gap the proposed contract test closes.
