# Claude review: evaluate_outcome structure and decomposition

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-outcome-evaluator-structure.md).

Reviewed state: current master (`be478a2`;
`application/outcome_evaluation.py:80-301` unchanged since the review batch).

## Verdict

**Recommendation: decompose when the next outcome family lands; no urgency.**
The function is contract-conformant and fully tested, but it carries six
responsibilities inline, and two family-side oddities indicate the seams where
a framework capability is missing. A phased refactor is sketched below with
provably-unchanged behavior; a leave-as-is decision is also defensible until
SCRUM-102 touches this file's neighborhood.

## Responsibility inventory

`evaluate_outcome` currently interleaves: (1) source/occurrence/snapshot
prerequisites; (2) pinned lineage verification (build, plan hash, definitions,
dataset/calendar/config chain); (3) selection resolution and multi-configuration
matching; (4) reference and window construction (prospective from
`available_at`, retrospective from `event_time`, expected-open-minute
enumeration, day bounds); (5) candidate-event bounding by lineage and
observable order; (6) algorithm execution, result validation, observation
assembly, idempotent persistence. Phases 1–3 are pure verification; phase 4 is
the subtle math; 5–6 are straightforward.

## Fragility points (where a future family could misuse shared logic)

- `reference_price` defaults to a cast of `snapshot.values["bar"]["close"]`
  (`:168`) — an untyped dict crawl whose failure mode is a KeyError, not a
  typed error, if the capture schema evolves.
- The `early` short-circuit (ineligible kind / retrospective unavailability)
  interleaves with window construction: the window is still built and stored
  on NOT_APPLICABLE results, so a refactor cannot skip phase 4 when `early` is
  set — this invariant is implicit today.
- The retrospective branch owns its own bar-index lookup and minute-alignment
  raise; prospective/retrospective divergence is the least-tested seam.
- Families currently resolve their own definitions from `source.configuration`
  (each algorithm calls `definition.resolve(...)` internally — see
  `time_to_threshold.py:91`, `structural_outcomes.py:86`). The framework
  already resolves the selection (`:131`); passing resolved parameters into
  `OutcomeInput` would remove per-family re-resolution and the risk of a
  family resolving against a different definition than the framework matched.

## Family workarounds worth surfacing

Structural outcomes receive pre-bounded candidate events (the SCRUM-100
inventory) through the existing `events` argument — that is already a
framework capability and needs no change. The per-family definition
re-resolution above is the one genuine workaround to fold back into the
framework during decomposition.

## Proposed refactor sketch (for a separate story)

- `_verify_lineage(...) -> _VerifiedSources` (frozen): event/occurrence/
  snapshot/run/plan/definition/dataset/calendar checks, returning typed
  handles.
- `_resolve_selection(...) -> OutcomeSelection`: plan matching incl. exact
  multi-configuration selection.
- `_build_window(...) -> tuple[ObservationWindow, Decimal, OutcomeResult | None]`:
  reference, expected minutes, day bounds, retrospective branch; returns the
  early-status if any (window still constructed).
- `_bound_events(...) -> tuple[OutcomeEventReference, ...]`: lineage and
  observable-order filtering.
- `_persist_observation(...) -> OutcomeObservation`: assembly, validation,
  idempotence.
- Extend `OutcomeInput` with the framework-resolved parameters so families
  stop re-resolving.
- Migration rule: existing suites pass unmodified; add per-phase unit tests;
  pin the "`early` still constructs the window" invariant explicitly.

## Verification limits

Static analysis of a fully-tested function; no behavioral probes were needed
(the phase behavior is pinned by the family suites and the SCRUM-120
fixtures). No tracked files modified by the reviewer beyond this verdict.
