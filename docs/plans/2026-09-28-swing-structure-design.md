# SCRUM-75 — SwingStructure v1 implementation design

Scope: the approved SCRUM-75 contract and linked `SwingStructure v1 — HH-HL-LH-LL Classification & Structure-Break Research` document, checked on 2026-09-28. This is a provisional descriptive research definition, not a trading signal or TrendLeg implementation.

## Approach

Use an incremental component with an injected SCRUM-74 `SwingPointState`, driven in the order ATR → SwingPoint → SwingStructure once per completed bar. Compared with accepting an arbitrary caller-supplied swing list, this allows fail-closed checks of dependency identity, configuration and per-bar lockstep. A standalone historical classifier would not supply the required causal close-break stream.

The component consumes newly confirmed points only, using their detection time rather than backfilling labels at event time. It preserves latest and previous same-type references and immutable classification evidence. Initial labels are HIGH_UNCLASSIFIED/LOW_UNCLASSIFIED; new-swing ATR anchors the configurable 0.10 equality tolerance, including the exact boundary. HH+HL is BULLISH, LH+LL is BEARISH, insufficient comparable history is UNDEFINED, and all other comparable combinations are MIXED.

Apply new confirmations before close-break checks. Each active reference freezes a configurable 0.10 ATR buffer from that reference's extreme. Strict completed-close inequality emits SWING_HIGH_CLOSE_BREAK/SWING_LOW_CLOSE_BREAK once per reference; a newly activated reference is ineligible on its detection bar. Replacing a reference resets that side's broken flag. Break context is captured after this bar's classifications, before break-flag mutation.

## Configuration, evidence and boundaries

Register the six approved v1 parameters under `swing_structure` version `1`; equality/break multipliers are editable non-negative finite Decimals. The supported anchor/source/no-same-bar semantics are fixed v1 options. Schema resolution and constructor validation must reject unsupported modes, missing parameters, disabled/wrong-version selection, mismatched dependencies and forged config hashes. Existing configuration hashes must remain unchanged for configurations that do not select the new component.

Require explicit immutable run ID and dataset revision ID for the component's evidence. Configuration hash is recomputed from its immutable run configuration and checked against any supplied pinned hash. Evidence retains complete source/prior swing references, event/detection times, prices, ATR anchors, multipliers, tolerances/thresholds and overall-state/label context. Expose read-only state and deterministic debug serialization. Run/dataset identity is lineage, not a price-rule input.

Reset the driver components together and replay from the same origin. Independent upstream reset, skipped/mismatched updates or future confirmations are errors, not opportunities to replay an accumulated future swing history. Session/day transitions do not reset structure. No provider, database or browser dependency belongs in this component.

## Verification and handoff

Use manually authored confirmed-point fixtures for exact high/low classifications, conservative overall states, frozen buffers, strict threshold boundaries, duplicate suppression, replacement, and same-detection-bar exclusion. Also exercise the real ATR/SwingPoint/SwingStructure chain with canonical bars, causal negative tests, immutable nested state, config/hash/lineage binding, reset/replay, session continuity and Decimal-context independence.

Run the full Python tests, Ruff, mypy and relevant configuration/API regressions. Review with GPT-6-Sol at x-high effort and resolve high/medium findings before committing. Record implementation/review fixes in the commit body, push, inspect GitHub Actions, and update SCRUM-75 only after its own acceptance is established. SCRUM-71 calendar alignment and SCRUM-122 manual installation validation remain unchanged.

## Implementation verification — 2026-09-28

The implementation adds 63 tests. Uncached `pnpm run ci --skip-nx-cache` passed all repository lint, typecheck, test and build targets: 284 Python tests passed and two PostgreSQL integration tests were skipped because no local test database URL was configured. Remote CI exercises those tests with its isolated PostgreSQL service and runs the browser smoke checks.

The separate GPT-6-Sol x-high review found no high, medium or low issues. Its independent oracle also matched classification, overall state, break ordering, suppression and evidence across 12,000 completed bars, ATR periods 1/14 and three multiplier configurations. The implementation was produced with GPT-6-Sol at medium effort. Remote workflow results and the final commit are recorded in SCRUM-75 after pushing.
