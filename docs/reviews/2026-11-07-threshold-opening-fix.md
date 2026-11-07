# Correction for the Astra threshold-ordering finding

Fixes the P2 in the [independent Astra high review](2026-11-07-astra-threshold-structural-review.md). This document records author verification, not a new independent review.

When both sides first touch in the same candle, the evaluator now uses the opening observation. Exact direction-aware comparisons identify a uniquely reached side at open; that side is first without assuming an intrabar path. If the opening observation satisfies neither side, or both sides simultaneously (coincident zero thresholds), the result remains AMBIGUOUS. Captured bearish/reversal direction and explicit UP_DOWN labels are respected. Numeric hit-candle and close-availability offsets are unchanged.

Corrected the shared test fixture to open inside its price range at the reference price, making its ambiguity case truly unknowable. New regressions cover exact and beyond-threshold upper/lower opens, inside-band ambiguity, bullish/bearish/reversal/explicit-axis configurations and unchanged offsets. A low-precision captured-ATR fixture verifies exact boundary comparisons. Before the fix 17 new cases failed and four legitimately ambiguous cases passed. Existing immutable observations are not rewritten; corrected runs retain their own pinned code lineage.

Verification: **176 focused Python tests passed / 4 PostgreSQL-dependent skips** across threshold, shared framework, forward returns and excursions. Ruff, mypy (123 source files) and diff checks passed. The final full Python/PostgreSQL regression will run after the separately authorized SCRUM-101 work. No installed database or provider was used.
