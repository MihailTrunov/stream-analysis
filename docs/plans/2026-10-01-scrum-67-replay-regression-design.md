# SCRUM-67 — replay determinism and future-data regression suite

Date: 2026-10-01. Scope: release-blocking tests for the existing completed-bar
replay interfaces. This Story does not introduce a new replay execution path.

## Decision

Use the small, seeded real-component/detector chain already exercised by
`test_replay_pipeline.py`. Compare complete serialized frames, committed
detector events and occurrence state across fresh runs and these control
paths: `step_one`, mixed `step_n`, `run_to_end`, paced playback at two rates,
maximum-speed playback, and backward/forward seeks. Include a persisted
`ReplayPipeline` pass to prove the headless walkthrough and persisted replay
share analytical output. Exclude only the deliberately fresh `run_id` from
cross-run comparisons; all other pinned lineage and output stays in scope.

Parity alone cannot detect a regression common to every path. Therefore keep
a short, hand-reviewed golden trace of selected warm-up-boundary and detector
transition facts: bar positions, ordered event types/states, and a few
component values/availability statuses. Avoid opaque full-frame snapshots;
full frames are still compared between execution paths.

On every step, probe the observable view for the next bar by index and
timestamp, and assert that its history equals the completed prefix. Retain
an early view and check it remains frozen after later steps and seeks. Detector
inputs must reference only the current finalized frame; their event times
cannot exceed the current bar. Such negative checks make an accidental public
future-data escape hatch fail in normal CI, while acknowledging that no API
can prevent deliberately importing an unrelated global future dataset.

## Verification

The suite runs under the existing `uv run pytest` / Nx CI target and uses
small in-memory fixtures. No performance benchmark or snapshot optimization
is implemented here. If snapshots are introduced later, their restore path
must be added to the same parity matrix before use.
