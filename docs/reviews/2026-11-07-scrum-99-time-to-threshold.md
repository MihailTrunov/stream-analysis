# SCRUM-99 implementation verification

Author verification for the [time-to-threshold contract](../plans/2026-11-07-scrum-99-time-to-threshold.md). No independent Astra review is claimed for this ticket.

Three production v1 definitions are registered: categorical TIME_TO_THRESHOLD, numeric TIME_TO_THRESHOLD_BARS and TIME_TO_THRESHOLD_SECONDS. Explicit per-side configuration preserves one typed value per observation. Inclusive high/low touches determine the first hit; equal first-hit candles produce AMBIGUOUS/SAME_BAR_AMBIGUOUS without guessing an intrabar path. Individual offsets remain available when their candle is known. Complete untouched windows retain AVAILABLE/NOT_REACHED categorically; an untouched numeric side carries NOT_APPLICABLE/NOT_REACHED, never a zero value. Full-window insufficiency takes precedence over an early hit.

Thresholds pin separate magnitudes and POINTS/ATR units per side, with a captured ATR instance where needed. EVENT_DIRECTION reads immutable continuation/reversal direction only; UP_DOWN provides explicit price axes for directionless events. Mixed units are supported. Threshold products and comparisons preserve exact Decimal arithmetic independently of ambient precision. The shared captured-ATR reader was extracted without changing existing excursion semantics or definition fingerprints. Missing ATR does not invalidate point-based thresholds or an independently requested point-side offset.

Offsets count future open-session M1 candles, or wall-clock seconds to the hit candle's close availability. Actual higher-timeframe source-close availability is preserved. Definition/configuration/source/context/dataset/calendar/code lineage, immutable typed SQL storage, exact configuration identity and queue ownership/completion remain the existing shared path. No schema migration or UI changes were required.

Focused verification: 131 tests passed / 3 PostgreSQL-dependent skips across thresholds, forward returns, excursions and configuration hashing, then 8 additional catalog/configuration/identity/zero-level cases passed. A separate fresh PostgreSQL run passed 25 tests, including actual queued threshold execution. Ruff, mypy (121 source files), changed-outcome-file formatting and diff checks passed. Initial fixture corrections fixed invalid OHLC after adjusting a high, and explicitly selected UP_DOWN for a directionless M5 source. No installed database or provider was used. Full regression result follows below.

Full Python regression: **1,258 passed / 24 PostgreSQL-dependent skips** (519.42 seconds). Separate PostgreSQL integration: **25 passed**. The authorized sequence continues with SCRUM-100.

[Independent Astra high follow-up](2026-11-07-astra-threshold-structural-review.md) found one unresolved P2: equal first-hit candle indices are always marked ambiguous, even when the opening price uniquely establishes the first side. The original same-candle ambiguity description and passing fixture above do not override the Jira/core conditional unknowable-order contract. No implementation fix was made in the review.

Implementation follow-up: the [opening-order correction](2026-11-07-threshold-opening-fix.md) addresses this P2 with exact opening-price checks and corrected ambiguity fixtures. Earlier unresolved wording records the review-time state.
