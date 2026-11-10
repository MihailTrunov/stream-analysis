# Fix for the Claude transaction-boundary review

This records implementation verification for the P2 in the
[independent Claude review](2026-11-08-claude-transaction-boundaries-review.md).
It is author-side fix verification, not a new independent verdict.

## Change

`atomic_transaction` is now the only sanctioned engine-level write boundary.

- Migrated all remaining plain `engine.begin()` boundaries to
  `atomic_transaction`: 33 sites across `api/app.py` (8),
  `application/worker.py` (6), `application/browser_replay.py` (7),
  `application/live_analysis.py` (3), `application/live_ingress.py` (1),
  `application/live_retention.py` (3), `application/analysis_sessions.py` (2),
  `application/historical_import.py` (2) and
  `application/provider_analysis.py` (1 — added by the committed provider-chart
  work after the review's HEAD, caught by re-deriving the site list). This
  covers every vulnerable-class and likely-class row in the review's
  classification table, including the chart-session step, browser replay
  launch/step, live retention and import/annotation API paths.
- The helper itself is unchanged: it no-ops on PostgreSQL and issues
  `BEGIN IMMEDIATE` on SQLite only when legacy DBAPI control has not begun a
  transaction, so PostgreSQL behavior is byte-identical and existing evaluation
  boundaries are unaffected.
- New architecture rule in `tests/unit/test_architecture_boundaries.py`:
  `engine.begin()` may appear only in `persistence/transactions.py`, so a future
  boundary cannot silently reintroduce the leak class.
- New regressions in `tests/unit/test_transaction_boundaries.py`, one per
  affected family, each reproducing the review's first-write-then-failure
  composition on migrated SQLite and asserting zero partial state:
  instrument registration (the review's confirmed reproduction; also asserts
  the boundary still commits normally), missed-pattern annotation creation,
  live-minute retention (failure injected immediately after the nested feed
  registration), and replay-run launch snapshot creation.

## Verification

New regressions and the architecture rule: 6 passed. Full Python suite:
**1,398 passed, 26 PostgreSQL-dependent skipped, 0 failures** (junit-counted
run, exit 0; the PostgreSQL cases skip as usual without
`STREAM_ANALYSIS_TEST_DATABASE_URL`). `ruff check src tests` passed after
import ordering; `mypy` passed with no issues in 131 source files. The
review's probe script reproduced the leak before the change and retains zero
rows through the migrated boundary after it.

No installed database, provider credentials or live services were used.
