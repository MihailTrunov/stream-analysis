# SCRUM-63 — ReplayCursor failure recovery

Date: 2026-09-29. Scope: finish the deterministic cursor contract after
SCRUM-61 connected it to the shared detector runtime. The Story's no-skipped-bar
rule and existing reset/replay behavior remain authoritative.

## Decision

Keep the current `SimulationClock` plus `AnalyticalPipeline` composition and
the existing `step_one`, `step_n`, `run_to_end`, and `reset` APIs. Three recovery
approaches were considered. Leaving recovery solely to callers would let a
direct cursor user step past a bar whose analytical processing failed. Rolling
the clock back would be misleading because the pipeline may have partially
mutated components or emitted effects before raising. Instead, latch the
cursor on a pipeline exception. Preserve the original exception and the
clock's failed index for diagnosis, but reject every further step or run call
until `reset` succeeds. Reset clears the pipeline first, then rewinds the
clock, then clears the latch. If reset itself fails, the cursor remains latched.

This is a domain-level safety rule, not a change to persisted replay recovery.
`ReplayPipeline` still marks a failed run terminal; its recovery path creates a
fresh run ID over the same pinned inputs. `step_n` retains its documented
prevalidation for an oversized request, so only valid-count batches are
equivalent to repeated `step_one` calls. An end-of-sequence call remains an
explicit non-mutating error rather than an analytical failure.

## Verification

Exercise failure on `step_one`, midway through `step_n`, and in `run_to_end`.
Assert that the failed index is observable, later bars are not processed, and
all stepping variants reject continuation. Then reset and replay the entire
sequence, checking event/state parity with an uninterrupted fresh cursor.
Also test a failed reset, a zero-count call while latched, and the unchanged
successful end-of-sequence behavior. Run cursor and application replay tests,
then the full local quality suite. No seek or playback-speed behavior belongs
in this Story.
