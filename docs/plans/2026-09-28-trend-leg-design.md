# SCRUM-72 — Structural TrendLeg implementation design

Scope: SCRUM-72, reviewed on 2026-09-28 against its approved 2026-09-24 reconciliation, `CONTEXT.md` and the canonical documents under `docs/core/`. The linked historical Google Drive `US30 EMA-45 Trend Leg Analysis System` describes the older crossing intervals and 30/70/35 research profile; it does not override the subsequent structural decisions. SCRUM-73 separately owns live qualification.

## State, dependencies and observability

Use the existing completed-bar incremental contract and injected, explicitly bound EMA and SwingStructure instances. Drive ATR → SwingPoint → SwingStructure and EMA before TrendLeg on every bar. Require matching configuration/hash, run/dataset lineage and exact input/reset lockstep. Compared with an independent historical swing-list processor, this approach retains the causal dependency boundary; compared with consuming only SwingStructure's latest-reference break events, it can retain an older protected swing correctly.

UP establishment requires a confirmed higher low followed by a confirmed higher high; DOWN requires lower high followed by lower low. These become observable only at the directional swing's detection time. Equal-valued swings cannot establish or advance protection. Retain immutable references to the corrective and directional confirmations, distinguish the initial protected swing's event time from the later establishment detection time, and keep active state and current-bar transitions bounded rather than accumulating full bar history.

Protection advances only after a new corrective swing and subsequent directional confirmation; it never moves backwards and is not backdated. Apply the configured SwingStructure definition consistently when recognizing confirmed progression. Preserve strict raw-price checks as an additional guard against equal or backwards protected levels. HH/LL is relative to the previous same-type confirmed swing, not the leg's all-time favorable extreme: a confirmed HH after an intervening LH can advance a higher protected low while still below an older peak. Do not establish or advance to a proposed reference broken by the current completed close when the directional confirmation becomes observable. This is not a permanent historical veto: a breach while the newer point was not yet protection does not retrospectively terminate the leg or prevent an otherwise valid later advance after recovery. Order the pair by confirmation/detection times, not by requiring distinct source extreme bars.

## Termination and event order

Check each active leg's own protected opposite swing against the current completed close, using the existing configured close-break convention and frozen reference ATR. That reference may differ from SwingStructure's latest swing. For example, a newer higher low does not replace UP protection until its following higher high is confirmed; breaking the newer low alone must not end the leg, and a later break of the older protection must still be recognized.

Evaluate the old protection's break before processing same-bar protection advances. A wick-only breach or opposing EMA cross does not end the structural leg. On termination discard same-bar establishment/advance opportunities and require both confirmations of a fresh opposite sequence to be observed after termination. Termination alone does not imply an opposite TrendLeg or a reversal detector event. Carry state across scheduled closures/session changes; caller launch preflight owns unexpected-gap rejection.

## Separate EMA-cross evidence and metrics

Preserve the historical raw completed-close crossing rules as separate EMA-cross intervals: UP when previous close <= previous EMA and current close > current EMA; DOWN is the mirror. No crossing is synthesized at the first valid EMA value. Opposing crosses finish the raw interval and start its opposite with the documented shared-boundary bar convention; they do not change structural validity.

Keep raw interval direction, times, inclusive bar count, start/current/end closes, signed/directional net move, span and explicitly unavailable zero-span efficiency. Bind the EMA instance in versioned configuration rather than hard-coding period 45 or guessing among multiple EMA selections. Structural leg evidence records its own protected reference, establishment/detection time, current duration/movement and favorable completed-bar extreme/retracement for later qualification/detector consumers; do not import the obsolete completed-interval qualification semantics.

## Configuration and handoff

Register `trend_leg` version `1` with `ema_instance_id` and `structure_instance_id` bindings, plus fixed establishment, termination, EMA-segment boundary and favorable-extreme modes. Resolved values participate in the normal schema, preview validation and ConfigHash. Bindings are validated before run start; v1-specific checks must not constrain separately registered future definitions. Reuse the approved shared component framework and immutable typed/debug evidence. Do not implement detector candidates, outcomes, speculative dwell/ATR experiments or qualification filters in this Story. SCRUM-73 adds the configurable 30-bar / 70-point live, sticky qualification layer; 35-point retracement remains evidence and a disabled-by-default detector filter, never a structural veto.

## Verification

Cover manually authored UP/DOWN confirmations, strict/equal boundaries, no synthetic initialization, EMA pullbacks with continuation, protection advancement and stale-reference breaks, wick-only versus completed-close breaks, break-first conflicts, termination with no automatic opposite, fresh post-termination sequences, raw EMA equality/shared-boundary metrics and zero-span efficiency. Also exercise the real ATR/SwingPoint/SwingStructure/EMA chain, invalid dependency/config/reset guards, immutable lineage/evidence, scheduled closure continuity, bounded state and deterministic replay under changed caller Decimal precision.

Implement with GPT-6-Sol medium, independently review with GPT-6-Sol x-high and resolve high/medium findings. Run uncached repository checks, commit this Story separately with implementation/review/fix evidence, push and validate hosted PostgreSQL/browser CI before Jira completion. Leave calendar alignment, manual fresh-checkout acceptance and sprint scheduling unchanged.

### Verification completed — 2026-09-28

Contract review removed unapproved historical-peak and distinct-source-bar restrictions, preserved explicit raw-cross direction even on same-direction recrosses, and scoped binding validation to v1 so future registered versions remain independent. Regression tests cover these fixes and realistic favorable-extreme tracking. Final independent x-high review found no actionable high/medium issue.

All 57 focused TrendLeg tests pass. Uncached repository checks passed all seven Nx targets: Python 401 passed / 2 PostgreSQL tests skipped locally, Ruff, mypy across 47 source files and web lint/typecheck/test/build. A separate real-chain oracle matched lifecycle, anchors, protection, duration/movement, favorable extremes/retracement and raw-cross metrics across 7,500 completed bars with three reversal multipliers: 178 establishments, 213 protection advances, 161 terminations and 2,707 EMA crosses. Hosted PostgreSQL/browser CI is checked after push before Jira completion.
