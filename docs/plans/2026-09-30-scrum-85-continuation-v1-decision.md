# SCRUM-85 — approved EMA-reclaim continuation v1 rule table

Decision date: 2026-09-30. This is the implementation interpretation of the
SCRUM-85 Story and its linked Trend Continuation research, confirmed by the
researcher. It resolves the Story's ambiguous `INELIGIBLE -> ELIGIBLE` wording
without changing its research hypothesis.

## Architecture and input boundary

Consume completed canonical MarketState frames, the bound qualified TrendLeg,
SwingStructure, and their current-bar EMA-cross and structure-break events.
Do not privately recalculate EMA or swing breaks. Eligibility is a predicate,
not a persisted lifecycle state: active qualified UP+BULLISH or DOWN+BEARISH.
The detector follows the EMA instance bound to that TrendLeg and records its
instance ID and period in evidence. EMA45 is the intended production default,
not a hidden hard-coded period; the v1 `candidate_trigger` label is therefore
`OPPOSING_BOUND_EMA_CLOSE`. A changed bound EMA period is visible in the
DetectionConfigHash. This clarifies the Story's provisional `OPPOSING_EMA45_CLOSE`
label without making an EMA9/EMA3 experiment falsely claim EMA45 provenance.
The alternative of persisting ELIGIBLE would add transitions without new
observable evidence. The alternative of updating references during a candidate
would change the hypothesis; v1 freezes both references at candidate creation.

## Transition table

| Current state | Next state | Rule on the completed bar |
| --- | --- | --- |
| INACTIVE | CANDIDATE | Eligible aligned context and the bound TrendLeg's opposing canonical EMA close-cross. Freeze the latest same-direction SwingStructure swing and opposite protected swing. |
| CANDIDATE | RECLAIMED | Canonical EMA close-cross back in the source trend direction. |
| CANDIDATE or RECLAIMED | INVALIDATED | Canonical opposite close-break refers to the exact frozen protected swing. This wins over reclaim or confirmation on the same bar. |
| RECLAIMED | CONFIRMED | Canonical same-direction close-break refers to the exact frozen continuation swing. |
| CANDIDATE | RECLAIMED -> CONFIRMED | Reclaim and matching same-direction break occur on the same completed bar; emit two ordered transitions with their own evidence. |
| CANDIDATE or RECLAIMED | EXPIRED | No earlier rule resolves it by the configured maximum subsequent completed-bar age (default 60). |

If an EMA reclaim first appears exactly on the expiry bar without a matching
continuation break, emit `CANDIDATE -> RECLAIMED -> EXPIRED` on that bar. Reclaim
alone does not extend the candidate's lifetime. A matching protected break or
confirmation still takes priority over expiry on that bar.

An EMA recross without the frozen protected break never invalidates. A break of
a newer swing, or a source TrendLeg ending against a newer swing, does not
substitute for the frozen-reference rule. Confirmation requires a matching
break on the confirmation bar: a same-direction break before reclaim is not
retrospectively counted. A candidate must be created from an active source
leg; a leg that ends on the candidate bar is not eligible.

The candidate event is visible on its opposing-cross detection bar; reclaim
is visible on its own cross bar. The confirmed event uses the matching
structure-break event_time and detection_time. No later extension or outcome
participates. Terminal occurrences remain inspectable and a fresh opposing
cross can start a distinct occurrence on a later bar.

## Evidence and tests

Freeze source-leg identity, both swing identities/prices, candidate/reclaim
times, and relevant cross/structure event references. Report available
pullback depth as the maximum completed-bar adverse high/low excursion from
the candidate's frozen source-leg favorable extreme, plus ATR, session and
HL/LH evidence, but none is a gate. Keep
run/dataset/config lineage in every emitted DetectorEvent. Test mirrored
bullish/bearish cases, alignment and absent references, same-bar chains and
precedence, exact frozen-reference matching, EMA recross, 60th-bar expiry,
config variation, typed rationale, no-look-ahead, and reset/replay parity.
