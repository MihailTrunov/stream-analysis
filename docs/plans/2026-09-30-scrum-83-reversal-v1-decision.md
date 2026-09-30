# SCRUM-83 — approved Trend Reversal v1 rule table

Decision date: 2026-09-30. The researcher approved this implementation rule
table and its eligibility, timeout, and simultaneous-condition interpretations
in conversation. This decision resolves the open details in SCRUM-83 without
changing the provisional research hypothesis in the Story or its [research
note](https://docs.google.com/document/d/1k4wpzgZagJnN3-VtBCCOz5OQE7M6smLDcPfTb8Wfm_E/edit).

## Eligibility and occurrence boundary

`ELIGIBLE` is a current-frame predicate, not a persisted PatternInstance phase.
An occurrence is created only on a qualifying opposing EMA close-cross and
starts in `CANDIDATE`. This avoids creating open-ended occurrences on every
eligible bar. The UI may derive eligibility from MarketState without claiming
a reversal candidate exists.

The pre-cross source context must be an active, qualified UP TrendLeg with
BULLISH SwingStructure, or an active, qualified DOWN TrendLeg with BEARISH
SwingStructure. An UP source opens a bearish candidate when the previous close
was on/above its bound EMA and the current completed close is below it. A DOWN
source opens a bullish candidate on the mirror cross. Use the canonical EMA
transition; no intrabar touch or detector-private EMA calculation is a trigger.
If the protected-swing break terminates the source leg on the same bar, that
bar cannot create a new candidate from the ended leg.

## Approved transition table

| From | To | Observable completed-bar evidence | Priority |
| --- | --- | --- | --- |
| No occurrence | CANDIDATE | Eligible source plus opposing canonical EMA close-cross | After source-leg break guard |
| CANDIDATE | CONFIRMED | Canonical `TREND_LEG_ENDED` for the candidate's source leg, caused by its protected-low (UP) or protected-high (DOWN) completed-close break | 1 |
| CANDIDATE | INVALIDATED | Newly confirmed same-direction HH (UP source) or LL (DOWN source), before protected break | 2 |
| CANDIDATE | EXPIRED | Configured maximum age reached with neither structural outcome | 3 |

The default maximum age is 60 *subsequent completed bars*: the cross bar has
age 0. On the 60th subsequent bar, check protected-break confirmation first,
same-direction-extreme invalidation second, then expiry. An EMA recross alone
does not invalidate. Loss of alignment after candidate creation is not a new
v1 invalidation rule. A full opposite SwingStructure or TrendLeg is not
required for confirmation. Terminal occurrences remain inspectable; a later
new opposing EMA cross may start another occurrence only on a later bar while
a valid source leg still exists. Remaining on the opposing EMA side cannot
restart one.

The confirming DetectorEvent uses the source leg's canonical `TREND_LEG_ENDED`
event's `event_time` and `detection_time`; candidate start time stays separate.
The end event is the authority for the source leg's protection, not a generic
SwingStructure break. A real-chain check found a bar where the source leg
ended on its protected swing but SwingStructure emitted a break for a newer
reference swing. The researcher explicitly approved confirmation on the
source-leg end in this case. Require the end event's leg index and direction
to match the candidate's frozen source leg; cite its semantic event reference.
If a matching SwingStructure break is also present, it may be cited as
corroboration but is not required. Confirmation and invalidation preserve
the exact consumed market-event semantic reference and typed rationale under
SCRUM-82. Expiry emits an `EXPIRED` lifecycle event for research/audit, not a
confirmed reversal pattern signal.

## Worked examples and precedence

1. An aligned qualified UP leg crosses from on/above to below EMA45 on bar
   `s`. Its protected low close-break is emitted on `s+4`. A bearish candidate
   begins on `s` and confirms on `s+4`, even though the leg ends on that bar
   and no opposite trend has formed.
2. The same candidate sees only an EMA recross on `s+1`; it stays open. A new
   confirmed HH on `s+3` invalidates it. A later opposing close-cross, not
   merely a close below EMA, is required for another candidate.
3. No structural outcome occurs on bars `s+1` through `s+59`. On `s+60`, a
   protected break confirms; otherwise a new same-direction HH/LL invalidates;
   otherwise the candidate expires. If both structural events are present on
   `s+60`, the protected break wins.
4. An opposing EMA cross and protected break on the same bar end the source
   leg before a candidate can be opened. No reversal occurrence is created
   from that already-ended source.

Required tests cover both UP/bearish and DOWN/bullish symmetry, misalignment,
EMA equality boundaries, exact source-break matching, same-bar precedence,
recross persistence, the `s+59`/`s+60` age boundary, re-entry only on a later
fresh cross, detection-time causality, typed rationale, reset/replay parity,
and coexistence with the independent continuation hypothesis.
