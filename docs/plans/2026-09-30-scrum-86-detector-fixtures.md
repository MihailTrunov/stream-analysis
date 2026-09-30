# SCRUM-86 — canonical detector regression fixtures

Decision date: 2026-09-30. SCRUM-83, SCRUM-84 and SCRUM-85 are Done. This
plan implements the existing SCRUM-86 contract without changing their v1
rules.

## Fixture design

Keep the already hand-authored boundary tests in their detector-specific test
modules. Add a shared, read-only canonical fixture with explicit input bars,
market-event expectations, post-step occurrence state/context, and selected
DetectorEvent fields: pattern/version, instance identity, sequence, transition,
event/detection times, source semantic refs, and typed rationale values.
Expected values are written and reviewed as literals; no update-snapshot or
regeneration command is permitted. A mismatch should identify the scenario,
bar and field. Keep the common full-chain bars in one named fixture and attach
short scenario-specific suffixes, avoiding many copies of the same 15 bars.

Use the same fixture to verify `process_bar` and finalized-frame replay, then
reset and construct a new runtime to check deterministic restart. Database
persistence of a typed detector context already has separate SCRUM-81 tests;
fixture-level restart must check the entire detector output, not merely the
serialization codec. The SCRUM-104 mode-parity Story remains responsible for
end-to-end walkthrough versus autonomous evaluation.

## Required coverage inventory

| Detector | Existing hand-authored boundary coverage | Canonical fixture addition |
| --- | --- | --- |
| Reversal | Up/down confirmation, misalignment, EMA recross, HH invalidation, source-leg end precedence, 60-bar expiry and config variation in `test_reversal_detector.py` | Up/down lifecycle and competing-candidate event envelope with literal source refs, context and timing |
| Compression | Five-bar persistence, separate axis failures, unavailable input, all releases/equalities, re-entry, config variation and computed RangeState in `test_compression_detector.py` | Real computed chain plus compact injected threshold scenarios with literal expected step records |
| Continuation | Up/down, frozen references, reclaim, same-bar chain, no-reclaim break, protected invalidation, recross, expiry and config variation in `test_continuation_detector.py` | Up/down lifecycle, same-bar event ordering and competing reversal/continuation outputs |
| Framework | Version binding, hash, same-bar ordering, reset, terminal behavior in `test_detector_runtime.py`; typed context restart in `test_pattern_instances.py` | Same input with separately authored v1/fake-v2 expectations, full-restart parity and fixture schema checks |

The manifest must name the individual focused tests for branches not repeated
in the compact cross-detector fixture. This makes the combination of canonical
records and focused boundaries auditable without creating an unreadable
historical golden file.
