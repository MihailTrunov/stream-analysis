# SCRUM-73 — Live structural TrendLeg qualification design

Scope: SCRUM-73 checked on 2026-09-28 against its approved 2026-09-24 reconciliation, `CONTEXT.md` and the canonical `docs/core/` documents. The earlier closed EMA-interval / absolute-movement / mandatory retracement-veto wording is superseded. SCRUM-72 supplies structural existence, lifecycle and source metrics; this Story supplies qualification, not a second structural state machine.

## Approach and source contract

Implement a separate bounded incremental qualification observer, driven once per completed bar after its explicitly bound `TrendLegState`. This keeps the structural and qualification definitions independently versioned and inspectable. Combining the layers inside SCRUM-72 would mix the two Story contracts; waiting until a raw EMA interval finishes would violate the approved live, observable qualification model.

Require the same resolved immutable configuration, run/dataset lineage, pinned configuration hash and exact source-bar/reset-generation continuity. Use the immutable source leg's semantic occurrence (definition/index plus lineage), initial protected swing, measured completed-bar duration and directional movement; do not recalculate swing progression or infer history from later snapshots. The MVP qualification profile operates on canonical one-minute bars and must not silently treat 30 five-minute bars as the same research default.

## Qualification and evidence

Expose configurable `min_duration_bars=30` (integer >=1) and `min_directional_move_points=70` (finite Decimal >=0), plus an explicit source instance binding and fixed supported qualification mode. Resolved defaults and binding participate in canonical configuration/hash and schema-driven editing. Validate at resolution/server preview and again at the observer constructor; scope rules to this exact version so future registered versions remain independent. Zero move threshold is allowed; movement remains directional, never absolute.

An active structural leg qualifies on the first observable completed bar for which both `duration_bars >= min_duration_bars` and `directional_movement_points >= min_directional_move_points`. Duration and movement stay anchored to the initial protected swing, not a later protection update, the EMA cross, elapsed wall-clock minutes or the observer's first sight of the leg. If both gates already pass when the structural leg becomes observable, qualify then, not on an earlier source-extreme bar.

Qualification is sticky while that same structural leg remains active. A later pullback, negative current movement, EMA cross, large retracement or protection advance cannot revoke earned qualification. Preserve the immutable first-earned threshold/metric snapshot and qualification time alongside current live gate evidence; a current failed gate does not erase why qualification was earned.

On a structural termination bar retain the leg's prior earned/unearned status and its final measured source snapshot. Do not newly qualify a leg that is no longer active, even if final duration/movement would meet the gates. Reset qualification for a genuinely new leg occurrence; do not transfer status from an ended leg to its successor. Keep current-bar qualification events and ended evidence bounded, with no full run history. Qualification event occurrence/detection times are the current qualifying bar, not the earlier protected-swing event time.

Retracement depth and favorable extremes remain source evidence. Do not add a mandatory 35-point veto or a speculative enabled/disabled filter here: detector rule tables own the disabled-by-default experimental filter and its 35-point reference. Optional dwell, EMA buffer/slope, ATR-normalized and session-specific hypotheses are not required v1 behavior.

## Availability, serialization and handoff

Distinguish no active leg, active unqualified leg and active qualified leg explicitly. Emit a qualification transition exactly once per active occurrence, retaining source references, definition/version, thresholds, pass evidence and run/dataset/config/calendar lineage. Preserve immutable typed evidence and deterministic nested debug output. The observer's warm-up declaration incorporates its source requirement and sensible qualification history without delaying independently observable live evidence. Scheduled closures carry state and count only actual completed bars; caller preflight owns unexpected gaps. Full-chain reset/replay recreates the same output without snapshot reconstruction.

## Verification

Test default and exact 30/70 boundaries, separate duration/move failures, negative directional movement, zero move threshold, UP/DOWN symmetry, delayed establishment without backfill, first-observable qualification, sticky qualification through pullback/EMA cross/retracement/protection advance, termination-before-qualification precedence and new-occurrence reset. Cover immutable first-earned/live/ended snapshots, current-bar event visibility, source binding and configuration/hash/API validation, non-M1 rejection, independent reset/config/bar guards, scheduled closure continuity, bounded state and deterministic replay under altered caller Decimal precision. Exercise the real structural chain as well as authored exact metrics; compare alternative qualification thresholds over identical structural transitions.

Implement with GPT-6-Sol medium and independently review with GPT-6-Sol x-high; resolve high/medium findings. Run uncached repository checks, commit this Story separately with scope/review/fix evidence, push and inspect hosted PostgreSQL/browser CI before Jira completion. Do not change SCRUM-71 calendar acceptance, SCRUM-122 manual acceptance or sprint scheduling.

### Implemented and verified — 2026-09-28

The separate `TrendLegQualificationState` and its exact four-field versioned schema are implemented. Immutable active/first-earned/ended records expose source evidence and lineage; the current-bar feed emits an explicit `QUALIFIED` transition once per occurrence without adding a second termination event. The assembler/runtime integration remains SCRUM-77/80 scope.

Local verification: 41 focused qualification tests; full uncached Nx lint/typecheck/test/build checks passed, with 442 Python tests passed and the two PostgreSQL integration tests skipped locally. Ruff, mypy (48 source files), web lint/typecheck/unit tests/build and `git diff --check` passed. The focused suite was rerun after the API rejection regression was tightened to use canonical `5m` and assert the actual M1 restriction message rather than merely an invalid enum error.

Independent GPT-6-Sol x-high real-chain oracles compared 5,152 bar snapshots across 32 mirrored UP/DOWN threshold profiles, including scheduled closure, first-observable qualification, retained initial anchors, sticky/final evidence and altered-Decimal-precision replay. Additional preview/source-guard checks exercised invalid thresholds/bindings/mode/filter inputs, disabled components, future independent versions, rejected-bar atomicity and upstream reset/settings drift.

Final independent review reread the stable implementation, configuration/API changes and complete test suite, reran the focused tests and targeted Ruff, and reported no unresolved high/medium findings. The test-only API coverage correction above was included in that final review.
