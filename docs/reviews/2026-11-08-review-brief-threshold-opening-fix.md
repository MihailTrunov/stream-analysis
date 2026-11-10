# Review brief: threshold opening-order fix verification

Status: reviewed
Reviewed by: [Claude threshold-opening-fix review](2026-11-08-claude-threshold-opening-fix-review.md) — no findings; original P2 correction satisfied
Verdict: `2026-11-08-claude-threshold-opening-fix-review.md`
Priority: high — the fix resolves an independently confirmed P2 but has only
author verification; the repository convention is a focused independent review
of fixes before dependent work proceeds.

## Scope

Commit: `edce78e` ("Correct same-candle threshold order from the opening
observation"). Diff is expected to be confined to:

- `src/market_analysis/application/time_to_threshold.py`
- `tests/unit/test_time_to_threshold.py` (corrected ambiguity fixture plus new
  regressions)

Verify the diff touches nothing else semantically (adjacent startup-seeding
commits `0705609`/`1031c7e` are unrelated and out of scope).

## Contracts to review against

- [Original P2 finding](2026-11-07-astra-threshold-structural-review.md) —
  ambiguity is required only when order is unknowable; an opening observation
  at or beyond exactly one threshold establishes that side first.
- [Fix record](2026-11-07-threshold-opening-fix.md) — author verification; an
  input, not a verdict.
- [SCRUM-99 plan](../plans/2026-11-07-scrum-99-time-to-threshold.md), review
  follow-up paragraph.
- `docs/core/technical-architecture.md` result-types section: same-bar
  dual-threshold results are AMBIGUOUS only when ordering cannot be inferred.

## Areas to probe (hypotheses, possibly unfounded)

1. **Exact-level and beyond-level opens.** For both bases (EVENT_DIRECTION and
   explicit UP_DOWN), both sides, and bullish *and* bearish/reversal-captured
   direction (labels reversed): an opening observation exactly at a threshold
   and strictly beyond it must classify that side first, not AMBIGUOUS.
2. **Genuine ambiguity preserved.** An open strictly between the levels stays
   AMBIGUOUS/SAME_BAR_AMBIGUOUS; coincident zero thresholds (both sides
   satisfied by the same open) stay AMBIGUOUS. Verify the fix did not
   overcorrect known-order cases out *or* unknowable cases in.
3. **Fixture honesty.** The corrected shared fixture must open inside its band
   (the original defect was `open=high=110` at the upper threshold). Check the
   fixture's OHLC is internally valid (high ≥ max(open,close), etc.).
4. **Numeric offsets unchanged.** In every corrected classification case, the
   bars/seconds offsets keep their pre-fix values and availability semantics.
5. **Later dual touches.** An established first side is not erased by a later
   candle touching the other side; first-hit candle indices still drive
   offsets.
6. **Precedence and precision.** Full-window insufficiency still overrides an
   early hit; exact Decimal comparisons hold under low ambient precision;
   mixed POINTS/ATR thresholds interact correctly with the opening check.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
- Disposable SQLite only unless a PostgreSQL-specific behavior is probed.
- The fix record's claims are inputs to check, not accepted evidence. A
  no-finding verdict is valid if the correction genuinely satisfies the
  contract.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_time_to_threshold.py tests/unit/test_outcomes.py \
  tests/unit/test_forward_return.py tests/unit/test_excursions.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A focused verdict in the existing Astra format: reviewed range, verdict per
the original P2's required correction, any new confirmed findings with
`path:line` locations, reproduction evidence, verification limits. Link back to
this brief.
