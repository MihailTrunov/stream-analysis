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

## Implemented coverage manifest

`tests/unit/test_detector_regression_fixtures.py` is the shared authored
record fixture: both directions of reversal and continuation, computed
compression, competing candidates, exact semantic refs, lineage hash, context
fields, event sequence/timing, and reset/new-runtime/finalized-frame parity.
Its expected records are literal test data, not snapshots.

Focused branch fixtures remain in these named tests:

| Required branch | Focused fixture/test |
| --- | --- |
| Reversal eligibility, EMA recross, HH/LL invalidation | `test_unqualified_and_misaligned_chain_does_not_open_candidate`, `test_ema_recross_alone_does_not_invalidate_but_new_hh_does`, `test_mirrored_new_ll_invalidates_bearish_source_after_recross` |
| Reversal protected-break versus newer structure, 60-bar expiry, config/expiry precedence, terminal/replay | `test_source_leg_end_wins_over_new_hh_and_newer_structure_break`, `test_candidate_expires_on_sixtieth_subsequent_bar_not_before`, `test_source_protection_break_on_expiry_bar_confirms_before_age_limit`, `test_configured_candidate_age_is_hashed_and_respected`, `test_terminal_occurrence_does_not_reenter_without_fresh_cross`, `test_reset_replay_is_byte_identical` |
| Compression separate axis failure/unavailability, exact entry/equality, hysteresis and each release cause | `test_candidate_fails_on_first_nonqualifying_or_unavailable_bar`, `test_entry_thresholds_are_independent_and_strict_on_chop`, `test_single_axis_negative_fixtures_do_not_start_a_candidate`, `test_active_hysteresis_and_strict_release_cause`, `test_unavailable_bar_does_not_invent_an_active_release` |
| Compression persistence/config/re-entry, no breakout and computed RangeState | `test_five_consecutive_bars_confirm_with_earlier_candidate_event_time`, `test_configured_persistence_count_changes_hash_and_confirmation_bar`, `test_reset_replay_is_identical_and_no_breakout_is_required`, `test_hand_authored_bars_confirm_and_release_from_computed_range_state` |
| Continuation eligibility, frozen refs, no-reclaim break, protected invalidation, EMA recross | `test_misaligned_structure_does_not_open_candidate`, `test_unqualified_crosses_before_aligned_leg_do_not_open_candidate`, `test_post_reclaim_break_of_newer_high_does_not_confirm`, `test_same_direction_break_before_reclaim_does_not_confirm`, `test_reclaimed_candidate_invalidates_on_exact_frozen_protected_break`, `test_ema_recross_alone_does_not_invalidate_reclaimed_candidate` |
| Continuation expiry and precedence, configuration, terminal/replay | `test_sixtieth_subsequent_bar_expires_after_reclaim_opportunity`, `test_reclaimed_candidate_expires_and_protected_break_preempts_expiry`, `test_reclaim_on_expiry_bar_without_break_expires_on_that_bar`, `test_age_limit_configuration_changes_hash_and_expiry`, `test_reset_replay_is_byte_identical_and_terminal_rearms` |
| Fake second version and persistence restart | `test_two_registered_versions_execute_over_one_frozen_fixture` has separately authored v1/v2 expected histories; `test_typed_context_survives_restart_for_planned_detector_shapes` and `test_restarted_next_transition_matches_uninterrupted_execution` exercise the database-backed typed-context/next-transition boundary without an external database service. |

The cross-detector fixture does not claim to replace SCRUM-104's full
walkthrough/autonomous driver parity: it verifies the shared detector semantic
boundary using `process_bar` and `process_frame` on the same inputs.
