# Pattern detection runtime area review — 2026-10-01

Follow-up amended 2026-10-01: the original review scope and historical run
counts below are retained, while the findings are corrected against the
canonical runtime and the feasible defensive fix is recorded.

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
the fixture suite was checked for authored-ness and coverage claims. At the
original review HEAD, three clean runs reported `719 passed, 10 skipped`
with Ruff and mypy strict clean; the first run had the unidentified failure
described in finding 4. These are historical counts, not current HEAD results.

## Findings and follow-up disposition

1. **Low — `SCRUM-83` defensive validation: resolved in the follow-up.**
   The original `reversal.py` branch treated a malformed
   `max_candidate_age_bars` as "not yet expired". Normal pattern-parameter
   resolution (`minimum=1`) already rejected it at the public config
   boundary, but the detector now raises `ValueError` before its age
   comparison if the input is not a positive integer. A focused test covers
   `None`, zero, negative, boolean and string values at the
   detector boundary.
2. **`SCRUM-80` reentrancy identity: optional behavior decision, not the
   reported accumulation bug.** The runtime does create a fresh current
   `INACTIVE` instance ID on the first post-terminal bar even if no entry
   transition occurs. However, `runtime.occurrences` explicitly filters
   initial-state instances, so it does **not** expose an empty never-started
   occurrence. The retained older keys are terminal audit records; each
   terminal rolls once, rather than repeatedly creating unused IDs. Lazy
   allocation would change observable `runtime.instances`/debug identity
   timing and merits an explicit contract decision and parity tests before
   implementation. No runtime change is made in this follow-up.
3. **`SCRUM-85` same-bar leg succession: withdrawn as an actionable finding.**
   Although `continuation.py` suppresses entry for any bound-instance
   `TREND_LEG_ENDED` on the bar, the canonical `TrendLegState` clears its
   active leg on termination and skips all establishment/progression on that
   same bar. Therefore a different, fresh active leg cannot coexist with
   that end event in a canonical frame. The guard remains as conservative
   defense; revisit it only if same-bar leg succession becomes supported.
4. **Verification hygiene: one historical, unidentified failure.** The
   original review observed `1 failed, 719 passed, 10 skipped` once, then
   three green runs, without capturing the failing test. Order/state
   dependence was a hypothesis, not an established cause. The project does
   not install the plugin that supplies `pytest --count`; repeat separate
   full-suite processes with `-p no:cacheprovider` and preserve any failing
   test identity before proposing a fix. Follow-up results are recorded below.

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

## Follow-up verification — 2026-10-01

The `SCRUM-83` defensive guard and five malformed-input cases were added in
`reversal.py` and `test_reversal_detector.py`. The focused reversal suite
passed. Five separate full-suite runs using
`pytest -p no:cacheprovider -q --tb=short` all passed. The full local Nx CI
command also passed (`725 passed, 10 skipped`; Ruff, mypy and build green).
The historical intermittent failure was not reproduced, so no root cause or
flaky-test fix is claimed. PostgreSQL tests still skip without the test
database, and the original review's line-by-line limits remain. The
reentrancy and continuation implementations were not changed. Revisit lazy
occurrence allocation only after deciding when a new current instance ID
should become observable; revisit same-bar end matching only if canonical
TrendLeg succession semantics change.

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
