# Claude review: threshold opening-order fix

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-threshold-opening-fix.md).

Reviewed range: commit `edce78e` ("Correct same-candle threshold order from the
opening observation"). Verified the diff is confined to the scoped files:
`src/market_analysis/application/time_to_threshold.py` (+27/−11) and
`tests/unit/test_time_to_threshold.py` (+68), plus documentation records.

Process note: an Opus-tier subagent began this review and confirmed the focused
suites pass before being interrupted by a provider usage limit; the
main-session reviewer completed the verification independently, including a
fresh baseline run.

## Verdict

**No findings. The original Astra P2's required correction is satisfied.**
When both thresholds first touch in the same candle, the evaluator now uses the
opening observation with exact, direction-aware comparisons; a uniquely reached
side at open is classified first, and genuinely unknowable orders remain
AMBIGUOUS.

## Correction verified against the original finding

The implementation (`time_to_threshold.py:158-173`) in the tie branch:

- computes `opening_move = exact_difference(bar.open, reference)` and negates it
  for captured DOWN direction, so thresholds stay in the same directional frame
  the scan loop uses (`:122-136`) — bearish/reversal label mapping is respected;
- classifies POSITIVE/UP first when `opening_move >= positive` and not
  `−opening_move >= negative` (and symmetrically), with inclusive `>=` covering
  exact-level opens as the original finding required;
- remains AMBIGUOUS when the open lies strictly between the levels, or both
  sides are satisfied simultaneously. The both-true case is only reachable when
  `positive ≤ −negative`, i.e. coincident zero thresholds with the open exactly
  at the reference — precisely the case the fix record names. The guard is
  exhaustive: no configuration classifies a knowable order as ambiguous or an
  unknowable one as known.
- the numeric BARS/SECONDS path (`:139-155`) is untouched by the diff, so
  per-side offsets keep their pre-fix values and availability semantics.

UP_DOWN basis fixes `direction = "UP"` (`:100`), giving explicit price axes and
UP_FIRST/DOWN_FIRST labels for directionless events; EVENT_DIRECTION keeps
POSITIVE/NEGATIVE labels and returns NOT_APPLICABLE when no captured direction
exists — both match the SCRUM-99 contract.

## Regression honesty

`git show edce78e -- tests/unit/test_time_to_threshold.py` adds exact-level and
beyond-level opens for upper and lower sides, bullish, bearish, reversal and
explicit-axis configurations, inside-band ambiguity, coincident zero
thresholds, a low-precision captured-ATR boundary case, and offset-preservation
assertions. The corrected shared fixture now opens at the reference price
inside its band — the original defect (`open=high=110` at the threshold) is
gone, and the fixture OHLC remains internally valid. The commit message records
17 new cases failing before the fix and four legitimately ambiguous cases
passing before and after; the pre-fix failure mode is evident from the removed
unconditional AMBIGUOUS return, so the pre-fix state was not re-executed.

## Independent verification

```sh
.venv/bin/pytest -q tests/unit/test_time_to_threshold.py tests/unit/test_outcomes.py \
  tests/unit/test_forward_return.py tests/unit/test_excursions.py
```

Passed within the combined evaluation baseline run executed for this review
batch (exit 0; PostgreSQL-dependent cases skipped). Disposable SQLite only; no
installed database, provider or provider credentials were accessed, and no
tracked files were modified by the reviewer.

## Verification limits

The full Python suite and browser checks were not repeated for this focused
fix; the author's recorded broad regression (1,331 passed / 26 PostgreSQL
skips, plus 27 PostgreSQL checks) remains separate implementation evidence.
