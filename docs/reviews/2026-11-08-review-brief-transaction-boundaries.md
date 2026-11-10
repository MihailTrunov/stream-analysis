# Review brief: SQLite transaction-boundary audit

Status: reviewed
Reviewed by: [Claude transaction-boundaries review](2026-11-08-claude-transaction-boundaries-review.md) — one P2 confirmed
Verdict: `2026-11-08-claude-transaction-boundaries-review.md`
Priority: high — same mechanism as a previously confirmed P2; cheap to audit;
several call sites were never migrated to the shared helper.

## Scope

Horizontal audit of current master. Anchor:

- `src/market_analysis/persistence/transactions.py:10-18` — `atomic_transaction`,
  the shared helper that explicitly begins SQLite's real transaction before
  nested writes.

Known plain `engine.begin()` call sites that wrap repository functions using
`connection.begin_nested()` (verify against current master; lists from
2026-11-08):

- `src/market_analysis/api/app.py:317, 700, 808, 872, 908, 977, 992, 1048`
- `src/market_analysis/application/worker.py:67, 124, 128, 132, 146, 158`
- `src/market_analysis/application/live_analysis.py:96, 260, 490`

Nested-savepoint repositories reachable from those transactions include
`persistence/market_data.py:235,301,405`, `persistence/history_ranges.py:101`,
`persistence/import_jobs.py:292,402`,
`persistence/validation_annotations.py:277,539`,
`persistence/evaluations.py:262`, `persistence/dataset_store.py:305`,
`persistence/pattern_instances.py:417,688`, and
`application/evaluation_runs.py:180`.

## Background contract

The [Astra autonomous review P2](2026-11-06-astra-autonomous-evaluation-review.md)
confirmed: under SQLite's legacy transaction control, `engine.begin()` does not
issue a database BEGIN before the first write, so a repository's nested
SAVEPOINT can become the actual transaction; releasing it commits early and a
later exception leaves a partial commit. The
[fix](2026-11-06-astra-autonomous-fixes.md) (commit `0d13e71`) migrated the
evaluation claim/chunk/dispatch/termination boundaries and the evaluation API
transaction to `atomic_transaction`. Other write paths kept plain
`engine.begin()`. Production's canonical store is PostgreSQL (unaffected);
SQLite serves development, tests and browser smoke runs.

## Areas to probe (hypotheses, possibly unfounded)

1. **Classify every plain `engine.begin()` site.** For each: does it
   transitively call a `begin_nested()` repository function, does any write
   precede the nested call (which would start the real transaction and
   neutralize the issue), and can an exception after the savepoint release
   leave a partial commit on SQLite? Produce a table: site → nested-write
   path → vulnerable / not-nested / neutralized / read-only.
2. **Reproduce at least one confirmed case**, if any, on a fresh disposable
   SQLite database migrated to head, following the original P2's pattern:
   first write succeeds, injected failure after the savepoint, assert partial
   state survives.
3. **Recommend the smallest correction** if vulnerabilities are confirmed:
   migrate the affected boundaries to `atomic_transaction`, or prove an
   alternative (for example explicit DBAPI transaction control for SQLite
   engines) that preserves PostgreSQL behavior unchanged.
4. **Regression gap.** If confirmed, specify the regressions that should be
   committed (first-write-then-failure per affected path), mirroring the
   evaluation first-event rollback regressions.

Rate findings with the deployment reality in mind: SQLite is not the canonical
production store, so confirmed leaks are P2/P3 rather than P1 unless a smoke
or test path silently depends on them.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
- Disposable SQLite databases only for this audit; PostgreSQL is unaffected by
  the mechanism and needs no probe.
- The site lists above are from grep on 2026-11-08 and may drift; re-derive
  them from current master rather than trusting the line numbers.

## Verification baseline

```sh
uv run pytest -q
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict in the existing Astra format with the classification table, any
confirmed findings (with reproduction), the recommended correction, and
verification limits. Link back to this brief.
