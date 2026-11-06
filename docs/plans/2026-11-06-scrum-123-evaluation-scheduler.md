# SCRUM-123: durable local evaluation scheduler

This implements the approved serial local scheduler contract. SCRUM-138 owns the workspace Runs interface and SCRUM-139 owns review tabs; this story supplies their backend operations and the real default worker. Existing immutable EvaluationRun inputs and the shared detector executor remain authoritative.

## Ownership and persistence

Migration `20261106_19` adds a singleton queue-control row and durable jobs linked uniquely to immutable EvaluationRuns. The queue starts paused. A monotonically allocated position defines waiting order; reordering validates the complete current waiting set and changes only job positions. Removing an unstarted entry retains CANCELLED history and its frozen analytical inputs.

Every queue mutation locks the singleton through an UPDATE; PostgreSQL row locking serializes claims/reorders/cancellation, while an actual SQLite transaction supplies equivalent local atomicity. Claiming a queued job also claims its EvaluationRun in the same transaction. A unique active slot and token guard exactly one execution, including competition with explicit-run workers. The executor accepts an already-owned run only with the matching token. Frozen inputs are validated again before claim; physical source/build checks remain at execution.

Evaluation IDs are canonicalized before ownership checks, so equivalent uppercase or compact UUID text cannot bypass queued-run controls. Enqueue locks the analytical lifecycle row as well as the queue; a direct run claim cannot race a job into an inconsistent ownership state.

Jobs expose QUEUED, RUNNING, COMPLETED, CANCELLED and FAILED, plus an explicit stop-request flag while a running worker is reaching a safe boundary. No successful job completion is recorded unless the underlying EvaluationRun is COMPLETED. Current submission supports detector-only plans because production outcome algorithms are separate pending stories; unsupported downstream execution is rejected explicitly, rather than claiming it ran.

## Progress, stop and recovery

The evaluation service polls the durable queue, runs one claimed evaluation at lower process priority, and updates service/job/run heartbeats periodically. Detector work still commits bounded chunks through the shared executor. Queue controls never advance or take over an interactive chart.

Stopping a running job requests cancellation and pauses queue progression in the same transaction. The worker rolls back unfinished work and records CANCELLED before releasing ownership. Pausing queue progression alone permits the current job to finish. A normal execution failure also pauses progression with a concise attributable reason.

Stale owned jobs and orphaned explicit executions are fenced using their current run token, marked FAILED with partial output retained, and leave automatic progression disabled. Recovery never resumes a detector checkpoint or silently launches the next job. A user must explicitly enable progression again. Backend/browser closure does not erase the queue or frozen inputs.

If analytical execution had already completed before a worker died while recording its final job receipt, recovery preserves COMPLETED and pauses progression without rerunning it. PostgreSQL recovery locks and rechecks the heartbeat predicate, so a heartbeat that commits while recovery waits does not falsely expire a healthy worker. Queue status remains readable with an explicit analytical-input error if frozen manifest validation fails.

## Transport and verification

Provide durable submit/list/read/cancel operations, queue state/start/pause and complete waiting-order replacement. Submission reuses existing source/config/build/warm-up/gap preflight atomically with queue insertion. The default no-ID evaluation process becomes the real scheduler consumer; explicit `--run-id` remains available. A one-poll CLI option supports owned offline integration checks without requiring provider access.

Verify deterministic ordering, copied-input isolation, reorder/remove, cancel/pause, stale fencing, restart persistence, preflight failure atomicity, two-worker claim races on real PostgreSQL, token ownership, actual worker output/failure summaries and interactive chart concurrency. Use disposable SQLite/PostgreSQL and immutable offline Parquet only. Installed databases/providers are not altered by implementation checks.

Routes: `POST /evaluation-jobs`, `GET /evaluation-jobs/{job_id}`, `POST /evaluation-jobs/{job_id}/cancel`, `GET /evaluation-queue`, `POST /evaluation-queue/start`, `POST /evaluation-queue/pause`, and `PUT /evaluation-queue/order` with the complete waiting `job_ids` list. Operational responses omit lease tokens. `GET /evaluation-runs/{evaluation_id}/events` supplies the existing durable evidence view. History is bounded and waiting submissions are capped at 1,000.

The default `python -m market_analysis.application.worker evaluation` consumes this queue. `--once` performs one recovery/claim/execution cycle for offline verification. SQL guards retain job history and prevent changing job/source links; used-data downgrade requires restoring a pre-queue backup. Existing rows are never silently relabelled or re-executed.
