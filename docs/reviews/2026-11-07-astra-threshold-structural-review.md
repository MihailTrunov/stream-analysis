# Independent Astra high review: SCRUM-99 and SCRUM-100

Reviewed `1cce1f3..378eb58` on master: threshold outcomes `7395052` and canonical structural outcomes `378eb58`. Astra used high reasoning and the project code-review skill; the root agent independently checked the result and ran additional probes. No production implementation was changed during this review.

## P2: use the opening price before declaring same-candle ambiguity

Location: `src/market_analysis/application/time_to_threshold.py:158–165`.

The implementation unconditionally returns AMBIGUOUS when both thresholds' first-hit candle indices match. It ignores the opening observation, which can establish the first side without assuming any intrabar path.

Example: source reference 100, upper threshold 110, lower threshold 90, first future OHLC **110 / 120 / 85 / 95**. The upper threshold is touched at the opening observation, while the lower threshold is reached afterward. The correct first-side category is POSITIVE_FIRST (UP_FIRST for explicit UP_DOWN); current output is AMBIGUOUS with no value. An opening observation at 90 likewise establishes the lower side first. Beyond-threshold opening prices reproduce the same defect. Bearish captured direction must reverse the directional labels correctly.

This incorrectly moves known-order cases out of AVAILABLE categorical results and into ambiguity counts. Numeric first-hit candle/time offsets remain known and are unaffected by this finding.

Jira SCRUM-99 and `docs/core/technical-architecture.md:409` condition ambiguity on order being unknowable. The author's plan broadened this to every same-candle tie without an explicit user override. Its SAME fixture also sets `open=high=110`, so the current ambiguity regression enshrines a knowable-order case rather than testing an opening price between the thresholds.

Recommended correction: in the shared first-hit candle, use exact direction-aware opening-price comparisons. If exactly one side is already reached at the opening observation, classify that side first. Keep AMBIGUOUS when the opening observation lies between the levels or both sides are simultaneously satisfied (for example coincident zero thresholds). Preserve numeric offsets. Correct the ambiguity fixture and add exact-level/beyond-level opens for both directions and both bases.

Astra verified exact/beyond-threshold opens, both event directions and EVENT_DIRECTION/UP_DOWN. Root independently reproduced two failing expected-result cases: open 115 above upper 110 and open 85 below lower 90, with both levels reached in that candle. Scratch probes: `/private/tmp/threshold-structural-review/test_opening_order.py`; these expected failures reproduce the finding and are not changes to the project's regression suite. The finding remains unresolved.

## Other review results and validation

No actionable structural-outcome finding was verified. Traced the canonical producer, source initialization, future bounds, component/transition filtering, source-event independence, actual availability and same-close phase ordering, immutable evidence and legacy reference serialization. Scheduled-closure tail and future-gap inventory concerns did not reproduce a valid false-NO_EVENT result.

Astra independently passed six focused tests covering M5 runtime parity, future inventory/gaps/cancellation, instance/qualification filters, legacy serialization and same-close ordering. Root additionally passed three integer-based differential probe cases, totaling 300 price-window scenarios and 1,500 category/offset calculations under low Decimal precision with mixed point/ATR thresholds. That differential oracle checks first-hit candle indices and numeric offsets; it does not establish correctness of the opening-price tie rule identified above.

No PostgreSQL or full-suite rerun was performed during this review. Prior author verification (1,285 Python passed / 25 PostgreSQL-dependent skips and 26 separate PostgreSQL checks) is preserved in the implementation reports; those passing results do not resolve this newly reproduced case. No browser implementation changed. No installed database, provider or credentials were accessed. Review used the project [code-review skill](../../.agents/skills/code-review/SKILL.md).
