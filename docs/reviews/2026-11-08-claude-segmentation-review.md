# Claude review: SCRUM-101 detection-time segmentation

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per the
[review brief](2026-11-08-review-brief-scrum-101-segmentation.md).

Reviewed range: commit `bc66b30` ("Implement frozen detection-time instrument and
session partitions") on master, audited at HEAD `c4e65bd`. Verified via `git log
--follow` that no later commit touched the scoped segmentation files; the commits
after `bc66b30` concern startup seeding and chart navigation.

Process note: this review was started by an Opus-tier subagent that was
interrupted by a provider usage limit before verification; the main-session
reviewer completed it independently, reusing nothing from the subagent except a
focused-suite log that was superseded by a fresh run.

## Verdict

**No confirmed findings.** All six brief probes are covered by traced code and
existing committed fixtures; the focused suites pass. One observation (not a
defect) is recorded at the end.

## Probe results

1. **Watermark coherence under concurrent execution — sound.**
   `evaluation_segments` captures `through = max(emission_order)` and uses that
   single watermark for both the per-stream count query and the paged membership
   reads (`segmentation.py:157-238`). Emission orders are assigned monotonically
   in dispatch order within a run, so any chunk committing after the watermark
   read carries orders strictly greater than `through` and is excluded from both
   counts and groups symmetrically; rows at or below `through` are already
   committed and immutable. The reconciliation check (`:217-220`) is therefore a
   defensive invariant, not the correctness mechanism. The committed fixture
   `test_partial_groups_and_explicit_watermark_do_not_include_later_events`
   (tests/unit/test_segmentation.py:419) exercises exactly this: it reads the
   group view from `on_commit` during a live scheduler run and asserts the
   captured prefix excludes later-committed events with consistent counts.
2. **Multi-stream reconciliation with unequal selections — covered.**
   `test_selected_stream_scope_has_per_definition_denominators` (:384) builds an
   M1 stream selecting BY_INSTRUMENT and BY_SESSION plus an M5 stream selecting
   only BY_SESSION, then asserts each definition's denominator equals its
   per-definition membership count (BY_SESSION spanning both runs, BY_INSTRUMENT
   only the M1 run). The self-review's claim is exercised by a real fixture, not
   just equal selections.
3. **Unavailable-bucket semantics — correct and tested.** `SegmentValue`
   enforces value/availability pairing and `semantic_key` digests the full bucket
   including availability (`domain/segments.py`), so a captured session literally
   named `UNAVAILABLE` yields a distinct AVAILABLE bucket from the missing-state
   UNAVAILABLE bucket. Dedicated fixture at :126 creates exactly that collision
   and asserts distinct semantic keys; whitespace-only names and non-mapping
   payloads normalize to the unavailable bucket via `assign_segment`
   (`segmentation.py:24-48`); instrument mismatch raises.
4. **Manifest byte compatibility — covered.** Legacy unsegmented manifests
   (optional descriptor omitted) and selection-order invariance are pinned by
   `test_unsegmented_legacy_manifest_and_event_set_identity_preserved` (:261)
   and `test_selection_order_does_not_change_pinned_input_semantics` (:461);
   execution-time fingerprint checks reject changed descriptors (:347).
5. **No future/outcome/timezone influence — covered.**
   `test_captured_values_ignore_outcomes_future_state_and_display_timezone` (:86)
   and the session-boundary fixture (:103) drive the negative cases; membership
   reads only the persisted instrument and the captured snapshot.
6. **API surface — matches contract.** `/segment-definitions` is read-only over
   the registry; the run segments view labels partial runs
   (`complete: false`), reports `through_emission_order`, and returns group
   sizes with pinned lineage only. Covered by :327 and the PostgreSQL case :363.

## Independent verification

```sh
.venv/bin/pytest -q tests/unit/test_segmentation.py tests/unit/test_evaluation_runs.py
```

Passed within the combined evaluation baseline run executed for this review
batch (exit 0; PostgreSQL-dependent cases skipped without
`STREAM_ANALYSIS_TEST_DATABASE_URL`). Ruff and the committed-suite run were part
of the same combined baseline.

## Verification limits

- The PostgreSQL-specific queued/segments case (:363) and the two-worker
  concurrency cases were not re-executed in this review (no disposable
  PostgreSQL was started); their prior author evidence plus the SQLite coverage
  above stand. PostgreSQL group-count behavior was previously exercised in the
  author's final combined PostgreSQL run.
- Probes were traced statically plus through the committed fixtures; no new
  adversarial fixture was authored beyond the existing set.

## Observation (not a finding)

`assign_segment` treats a whitespace-only session name as missing
(`name.strip()` check at `segmentation.py:32`). If a provider ever emits a
padded session label, it would be reported as UNAVAILABLE rather than as its
trimmed value. Current OANDA calendars do not emit padded labels; preserving
captured values exactly (no trimming of real values) is the contract, so this is
notified for future calendar extensions only.
