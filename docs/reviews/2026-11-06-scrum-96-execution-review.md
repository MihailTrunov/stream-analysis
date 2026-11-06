# SCRUM-96 implementation review

This is implementation/self-review evidence, not an independent Astra review. The earlier Astra verdict covered the preceding parity/context work.

The review traced immutable input/build/source checks, completed-bar ordering, shared processing and persistence, capture-before-population selection, lease ownership, transactional progress, cancellation/failure and completion gates. Runtime and bridge instances are discarded after any failed chunk; uncommitted in-memory state cannot be reused for an implicit resume. SQL outputs remain tied to the pinned child snapshots and parent evaluation.

Focused checks execute real CANDIDATE/ACTIVE/COMPLETED transitions at chunk sizes 1, 4 and 128; inspect durable event/context/population records; reject future prefix access; force mid-chunk rollback and cancellation; and execute M1/M5 together. A gated background execution permits a real interactive replay to launch and advance against the same database. Dedicated worker subprocess tests verify both successful execution and corrupted-source failure with run/dataset/component log attribution. Separate migrated PostgreSQL tests cover successful completion and forced partial failure.

The complete queue/reorder/recovery scheduler remains SCRUM-123. Explicit worker execution is available; the no-ID service does not claim jobs. Formula implementations remain SCRUM-97–100 and are not invoked here. No installed data, provider connection or installed schema is changed by these checks.

Verification: **1,102 Python passed (18 PostgreSQL-dependent skips), 56 browser passed, 49 web unit tests passed**. Web lint/build, Ruff, mypy (104 source files) and diff checks passed. Separate actual PostgreSQL executor success/failure checks both passed. The final combined PostgreSQL-targeted selection passed **19 checks** after the fixture-isolation follow-up described below. No provider or installed database was used.

## CI fixture follow-up

Running the new outcome/evaluation PostgreSQL checks together reproduced a test-data collision: both fixtures used instrument ID US30 with different synthetic calendars. The outcome migration fixture now uses a unique disposable schema, cleaned after the check. Existing PostgreSQL integration head assertions also still expected `20261002_10`; they now require the actual head `20261106_18`. All 19 PostgreSQL-targeted checks passed together against the owned test database. The follow-up outcome suite passed **27 tests (1 PostgreSQL skip)**. These are test corrections; production immutability/conflict detection remains intact.
