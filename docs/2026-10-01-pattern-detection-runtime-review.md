# Pattern detection runtime area review — 2026-10-01

## Review scope

Read-only review of the pattern-detection runtime area, Jira Stories
`SCRUM-78` through `SCRUM-86`, all currently Done. The reviewed range is
`54dc981..8c60e0e` (SCRUM-80 runtime and instance amendments, SCRUM-82 event
evidence and persistence, the three real detectors SCRUM-83/84/85, the
SCRUM-86 fixture suite, and the SCRUM-81 restart-parity work), assessed
against each Story's approved 2026-09-30 decision table in
`docs/plans/2026-09-30-scrum-8{3,4,5}-*-decision.md`, the SCRUM-86 fixture
plan, and the earlier SCRUM-80/81 review resolutions. This is a review, not
an implementation change and not a claim that follow-up Stories
(SCRUM-104 mode parity in particular) are complete.

Method: the three decision tables and the fixture manifest were read in
full; `detection/continuation.py` was reviewed line-by-line against its rule
table; `detection/reversal.py` and `detection/compression.py` were reviewed
at their decision points (eligibility, precedence, thresholds, invalidation,
expiry); `detection/runtime.py` was reviewed at the reentrancy, causality and
identity sections; `patterns/event_evidence.py` and the persisted
`PersistedDetectorEvent` records were checked against the SCRUM-82 contract;
the fixture suite was checked for authored-ness and coverage claims. Gates on
HEAD: `719 passed, 10 skipped` with Ruff and mypy strict clean on repeated
runs (see finding 4 for one exception).

## Findings

1. **Low — `SCRUM-83`: an invalid `max_candidate_age_bars` silently disables
   expiry instead of failing.** In
   `src/market_analysis/detection/reversal.py:393`, the branch
   `if type(maximum) is not int or maximum < 1 or age < maximum:` treats an
   invalid configured maximum as "not yet expired", so a malformed value
   reaching the detector produces candidates that never expire rather than a
   defensive error. Resolution-level parameter validation (`minimum=1`)
   normally prevents this, so it is a defense-in-depth gap, not a reachable
   bug through the public config path. Recommended fix: raise on the invalid
   value before the age comparison.
2. **Low — `SCRUM-80`: the reentrancy roll happens on the first
   post-terminal bar even when no new occurrence opens.**
   `src/market_analysis/detection/runtime.py:520-544` creates the fresh
   `base:{n+1}` instance whenever the current occurrence is terminal,
   incrementing `occurrence_index` and swapping `slot.instance_id` on that
   bar regardless of whether the detector emits anything. Correctness holds
   (each terminal occurrence rolls exactly once; sequences reset under the
   new key), but `runtime.occurrences` then includes an empty never-started
   INACTIVE occurrence after every terminal one, and the `instances` dict
   accumulates stale keys under the old ids. Recommended fix: roll lazily —
   keep the terminal occurrence current and construct the fresh identity
   only on a bar where the detector actually emits an entry transition.
3. **Low — `SCRUM-85`: the same-bar source-leg-end guard blocks entry on any
   `TREND_LEG_ENDED` from the bound leg instance.**
   `src/market_analysis/detection/continuation.py:253-258` returns without a
   candidate if any `TREND_LEG_ENDED` for the bound instance is visible on
   the cross bar. The approved rule is that a leg ending *on the candidate
   bar* cannot seed a candidate; if a different, earlier leg's end event
   lands on the same bar as a fresh active leg's opposing cross, the
   detector stays ineligible. Same-bar leg succession of this shape is rare,
   so this is over-conservative rather than wrong. Recommended fix: check
   that the ended leg is the *active* source leg (`leg_index` match) before
   suppressing entry.
4. **Medium (verification hygiene): one unreproduced intermittent test
   failure.** On the first full-suite run of this review the result was
   `1 failed, 719 passed, 10 skipped`; three subsequent runs were fully
   green with identical counts. The failing test's identity was not captured
   before the re-run. This is consistent with an order- or state-dependent
   test (plausibly in the newer detector or fixture modules sharing module
   state), but it is unconfirmed. Recommended action: run the suite under
   `pytest -p no:cacheprovider --count 5` (or repeat runs in CI) once to
   identify the test, then fix its ordering dependence before it erodes
   trust in the suite.

## Verified sound (not findings)

- **SCRUM-85 continuation conforms to its approved rule table** including
  the intricate cases reviewed line-by-line: eligibility as a predicate;
  `OPPOSING_BOUND_EMA_CLOSE` with bound-EMA provenance (instance id and
  period recorded in context/evidence and covered by the hash); frozen
  continuation/protected references matched exactly by
  (index, type, event_time, price) with silent switching impossible;
  invalidation evaluated before reclaim/confirmation on the same bar;
  same-bar `RECLAIMED -> CONFIRMED` and the expiry-bar
  `RECLAIMED -> EXPIRED` chains emitted in order; a same-direction break
  before reclaim is never retrospectively counted; EMA recross alone never
  invalidates; source-leg end against a newer swing does not substitute for
  the frozen-reference rule; pullback depth and HL/LH are recorded as
  evidence only.
- **SCRUM-83 reversal implements the approved precedence** —
  confirmation-first via `TREND_LEG_ENDED` matching the candidate's frozen
  source leg (index and direction) with the end event's own times and an
  optional corroborating `SwingStructure` break of the same swing; then
  same-direction HH/LL invalidation (detection strictly after candidate
  start); then expiry at the configured subsequent-bar age. Re-entry after
  terminal occurrences rides the runtime's reentrancy with a fresh opposing
  cross.
- **SCRUM-84 compression implements the approved thresholds exactly**:
  entry is `compression >= 80 and CHOP > 61.8` (inclusive/strict as
  specified); confirmation is the single `CANDIDATE -> ACTIVE` transition at
  the configured consecutive-bar count with `event_time = candidate start`
  (permitted by the runtime's same-occurrence earlier-event rule); release
  is strict `<` on either axis with `BOTH`/cause recording; equality stays
  ACTIVE; unavailable input invalidates a candidate but never invents an
  active release; no breakout requirement anywhere.
- **SCRUM-80 review resolutions are intact on HEAD**: canonical
  `(pattern_id, pattern_version)` execution ordering (hash-consistent), the
  failure latch with reset-only recovery, `binding_fingerprint`, and the
  causality ledger — now extended with `_detector_event_times` so an
  earlier `event_time` is accepted only when the same occurrence already
  emitted that event, exactly what SCRUM-84's confirmation timing requires.
- **SCRUM-82's evidence contract is real**: `patterns/event_evidence.py`
  validates typed `detector-evidence-v1` documents (typed values, PASS/FAIL/
  NOT_APPLICABLE statuses, operator vocabulary, de-duplicated semantic
  references, no unknown fields), and the persisted
  `PersistedDetectorEvent` carries `event_semantic_key` plus
  `within_bar_ordinal` derived from analytical identity — no DB UUID or
  wall-clock leakage found in the identity chain from definition
  fingerprint through instance key to event key.
- **SCRUM-86 fixtures are authored, not regenerated**: the suite docstring
  declares literal reviewed expectations; assertions compare against
  hand-written dictionaries (e.g. `COMPRESSION_EXPECTED`), with reset,
  fresh-runtime and finalized-frame parity all asserted byte-identically.
  The manifest's focused-test mapping covers the Story's required branch
  lists, and the fake second version proves coexisting-version support.

## Observations (design notes, no action required)

- Runtime-internal state (the event-time ledger, event-ref sets, per-slot
  occurrence indices) is deliberately not persisted; restart parity is
  achieved by fresh-run re-execution, consistent with the SCRUM-61
  "reset means a new run" design. Anything richer belongs to SCRUM-104.
- The cross-detector fixture verifies `process_bar`/`process_frame` parity
  only; end-to-end walkthrough-vs-autonomous parity remains SCRUM-104's
  responsibility, as its plan states.

## Verification and limits

Reviewed at commit `8c60e0e` (plus the final SCRUM-108 commit, out of
scope). Gates run four times: one run with the unreproduced failure noted in
finding 4, three clean (`719 passed, 10 skipped`; Ruff and mypy strict
clean). Not verified line-by-line: the full `_features` blocks of all three
detectors against every optional evidence item in the Story lists (spot
checks passed), the PostgreSQL integration suite (skips without a database),
every literal in the 694-line fixture module, and the row-level constraints
of the detector-event migration. The two review subagents originally
tasked for this area were terminated by API rate limits; this review was
performed directly instead, with proportionately narrower empirical probing.

Relevant Story links:
[SCRUM-78](https://mihailtrunov.atlassian.net/browse/SCRUM-78),
[SCRUM-79](https://mihailtrunov.atlassian.net/browse/SCRUM-79),
[SCRUM-80](https://mihailtrunov.atlassian.net/browse/SCRUM-80),
[SCRUM-81](https://mihailtrunov.atlassian.net/browse/SCRUM-81),
[SCRUM-82](https://mihailtrunov.atlassian.net/browse/SCRUM-82),
[SCRUM-83](https://mihailtrunov.atlassian.net/browse/SCRUM-83),
[SCRUM-84](https://mihailtrunov.atlassian.net/browse/SCRUM-84),
[SCRUM-85](https://mihailtrunov.atlassian.net/browse/SCRUM-85),
[SCRUM-86](https://mihailtrunov.atlassian.net/browse/SCRUM-86).
