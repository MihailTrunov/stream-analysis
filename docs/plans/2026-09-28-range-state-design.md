# SCRUM-76 — RangeState v1 implementation design

Scope: approved SCRUM-76 and the linked `RangeState v1 — Choppiness & Compression Research and Rationale`, checked on 2026-09-28. Implement `CHOP_BANDWIDTH_RANGE_STATE_V1`, a provisional descriptive research primitive, not a compression detector or persistent bounded range.

## Approach and inputs

Use the existing incremental market-state contract with an injected `AtrState`, driven ATR → RangeState once per completed canonical bar. Consume ATR's current True Range immediately, independently of ATR smoothing warm-up. This reuses the approved first-bar high-minus-low and subsequent prior-close True Range conventions. An alternative standalone True Range calculation would duplicate SCRUM-70; an offline full-history calculation would weaken the causal incremental boundary. EMA is a completed sequencing prerequisite, not an input to either formula.

Require matching immutable run configuration, an enabled resolved `range_state` version `1` selection, exact upstream completed-bar lockstep and verified configuration hash. Reuse the existing read-only current-bar/reset-generation metadata to reject skipped updates, mismatched bars and independent upstream resets. Require explicit nonempty run and dataset-revision identifiers. Keep only bounded rolling windows; do not accumulate full per-bar history.

## Formula and independent availability

CHOP uses the last `chop_period` True Ranges and high/low envelope: `100 * log10(sum_tr / window_range) / log10(n)`. Default n=14. Scores below 38.2 are DIRECTIONAL, above 61.8 RANGE_LIKE, and both equality boundaries TRANSITIONAL. An incomplete window is WARMING_UP; zero envelope is explicitly DEGENERATE with diagnostic evidence, not infinity or a fabricated score.

BandWidth uses completed closes, default period 20 and multiplier 2, with SMA basis and population standard deviation. Preserve basis, variance/stddev, upper/lower bands and raw relative width. An incomplete window is WARMING_UP; a zero basis is explicitly DEGENERATE. Compute exact rational population moments from the finite Decimal inputs before rounding the moments and dimensionless squared-width ratio to 34 digits. Take the square root at that precision, with width sign following the basis. This algebraically equivalent calculation avoids rounded band-subtraction cancellation and preserves exact empirical ties for identical or proportionally scaled close windows; it introduces no ranking tolerance. Rounded upper/lower bands remain diagnostic evidence, not a second source for the width calculation.

Maintain the latest N valid BandWidth observations including the current one, default N=120. When the full reference exists and current BandWidth is valid, percentile is `count(previous < current)/(N-1)` and compression score is `100*(1-percentile)`. Equal widths are not counted as lower. Exactly 80 is COMPRESSED, exactly 20 EXPANDED, otherwise NORMAL. Invalid current BandWidth must not expose stale compression as available or add an invalid observation; previous valid observations remain retained under the Story's valid-observation convention.

Expose independent CHOP, BandWidth and compression statuses, with `UNAVAILABLE` categorical regimes whenever the corresponding score is unavailable. Intrinsic history is `max(chop_period, bandwidth_period + compression_reference_bars - 1)`, i.e. 139 completed bars by default. The component's declared preflight requirement also includes the injected ATR's warm-up; global pipeline requirements may require still more. None of these full-history gates delays the independently observable early axes. Reaching the count is not a guarantee that degenerate inputs became available. Use fixed 34-significant-digit Decimal arithmetic for deterministic logarithms, variance, square roots and ranking, independent of caller precision.

## Configuration, evidence and boundaries

Register the exact ten approved parameter IDs and defaults: `chop_period=14`, `chop_directional_threshold=38.2`, `chop_range_threshold=61.8`, `bandwidth_period=20`, `bandwidth_stddev_multiplier=2.0`, `compression_reference_bars=120`, `compressed_score_threshold=80`, `expanded_score_threshold=20`, `bandwidth_stddev_mode=POPULATION_V1`, and `compression_percentile_mode=STRICT_EMPIRICAL_V1`. Require CHOP/reference counts >=2, BandWidth period >=1, nonnegative BandWidth multiplier, and finite thresholds in [0,100] with non-overlapping ordered category cutoffs. Validate types/ranges/modes and cross-parameter ordering during resolution/server preview and at the constructor boundary. Do not alter canonical hashes of configurations that do not select the new definition.

Read-only state/debug output preserves all formula inputs needed to inspect the current rolling calculation: TR/high/low window, close window, valid width reference observations, strict-lower/tie counts, thresholds/modes, availability and reasons, calculation/detection time, instance/definition/version and run/dataset/config/calendar lineage. Reset/replay with identical inputs must reproduce identical output, including under a different caller Decimal context.

Canonical launch policy blocks unexpected gaps in warm-up/detection intervals; scheduled closures and day/session changes retain analytical state. This component must not guess closures from elapsed timestamps or create missing bars. Explicit reset of the whole driver chain clears history for a new origin; automatic unexpected-gap reset/recovery is post-MVP. Test scheduled timestamp gaps without synthesis and explicit reset recovery; do not introduce provider-specific calendar rules or arbitrary quality-flag semantics.

## Verification and handoff

Use hand-calculated CHOP and population BandWidth fixtures, exact category boundaries, ties, zero range/basis, independent warm-up (including default 14/20/139 boundaries), rolling eviction, range-like-wide versus compressed-directional cases, configuration variations/aliases/hash compatibility, immutable nested evidence, negative causal/dependency tests, session/closure continuity and deterministic replay/reset. Compare a real ATR → RangeState chain with an independent oracle; avoid tests that simply duplicate the implementation formulas without external expected evidence.

Implementation uses GPT-6-Sol medium; independent review uses GPT-6-Sol x-high and resolves all high/medium findings. Run Python tests/Ruff/mypy and uncached Nx repository checks, then commit with scope/review/fix evidence, push, inspect PostgreSQL/browser CI and update only SCRUM-76. Other stories, sprint statuses, SCRUM-71 calendar acceptance and SCRUM-122 manual installation validation remain unchanged.

### Verification completed — 2026-09-28

The independent x-high review identified two medium-severity numeric tie cases: rotating identical close windows and proportionally scaled windows could produce rounding differences that incorrectly switched compression regimes. Exact rational moments and the dimensionless width calculation resolved both, with no comparison tolerance. Regression coverage also includes large-price band cancellation and negative bases. Final re-review found no outstanding findings.

All 60 focused RangeState tests pass. The uncached repository check passes all seven Nx targets: Python 344 passed / 2 PostgreSQL tests skipped locally, Ruff, mypy across 46 source files, web lint/typecheck/test/build. The independent Fraction/80-digit oracle passed 10,125 formula checks, 4,854 strict rank/tie checks and 66 zero-basis checks across 5,250 completed bars, seven BandWidth periods and three multipliers. Hosted PostgreSQL/browser CI is checked after push before Jira completion.
