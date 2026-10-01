# SCRUM-66 — run until a detector event

Date: 2026-10-01. Scope: sequential backend replay control over the shared
`ReplayPipeline`. UI event-list presentation remains SCRUM-89.

## Decision

Add `run_until_event` to `ReplayPipeline` so each iteration calls its existing
`step()` path. That path advances the causal `ReplayCursor`, executes the
shared detector runtime and persists the cursor before returning only the
newly emitted events for the completed bar. A pre-scan of the full run/event
ledger would leak future information. A separate detector execution path
would risk divergent state. Neither is acceptable for this control.

A frozen `DetectorEventFilter` supplies optional exact-match fields for
`pattern_id`, `pattern_version`, `instance_id`, `trigger_id` (the transition
reason/event type), `from_state`, and `to_state`. Unset fields mean any value;
all set fields combine with AND. The all-unset filter means any detector event.
The first matching event in canonical within-bar order is returned. All
events on the bar are already committed atomically before the control pauses;
the cursor never stops mid-bar.

The operation starts a CREATED run, resumes a PAUSED run with its existing
in-memory analytical state, or continues a RUNNING run. It rejects terminal
runs and invalid filters before stepping. It processes warm-up bars normally
but only visible-interval events can stop the walkthrough; warm-up state and
events still exist in the runtime. On a match it transitions the ReplayRun
to PAUSED at the event bar. A later call resumes from the next unprocessed
bar. If no event matches, it exhausts the selected range and marks the run
COMPLETED. A match on the final bar still pauses for inspection; a subsequent
resume completes without another bar. Analytical failures retain the
existing FAILED/latch behavior. A failed pause write must also latch/mark
FAILED so a caller cannot silently step beyond the matched event.

## Verification

Use small seeded fixtures to cover generic and filtered matches, nonmatching
earlier events, multiple same-bar events, no-match completion, warm-up event
handling, final-bar match, resume parity with uninterrupted execution, and
failed analytical or lifecycle writes. Assert the persisted cursor index and
status match the in-memory cursor after each stop.
