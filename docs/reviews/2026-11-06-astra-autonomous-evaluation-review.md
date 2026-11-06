# Astra review: outcome framework and autonomous evaluation

Reviewer: GPT-6 Astra, **high reasoning**, independent review agent.

Reviewed range: `bfd68d7..d4c6cb9` on master. This includes SCRUM-120's outcome framework, SCRUM-95's EvaluationRun/EventSet models, SCRUM-96's production executor, PostgreSQL fixture corrections and SCRUM-110's actual autonomous parity adapter. The preceding [Astra review](2026-11-06-astra-parity-context-review.md) covered earlier work only.

## Verdict

**Two confirmed findings: one P1 and one P2. Fix both before proceeding to SCRUM-123.** Passing existing tests does not establish the claimed cross-mode identity and SQLite chunk-rollback guarantees. No implementation fixes are included in this review.

The review inspected outcome windows/availability/calendars, frozen run/source/build/configuration/definition inputs, capture-before-selection and X-only filters, deterministic ordering, failure/cancellation/completion behavior, worker integration, migrations and parity blind spots. Production outcome formulas, full queue scheduling/recovery and result UI remain explicitly deferred scope.

## P1 — Production chart and autonomous execution do not normalize settings consistently

Locations: `src/market_analysis/application/autonomous_evaluation.py:137`, `src/market_analysis/application/analysis_sessions.py:427`, and the masking input transformation in `tests/regression/test_cross_mode_parity.py:144`.

The autonomous worker reconstructs settings from canonical JSON, while chart sessions pass resolved settings directly into DetectorRuntime. Canonical serialization changes Decimal scales such as `2.0` to `2` and `0.10` to `0.1`. These differences propagate into scale-sensitive evidence hashing, so they affect durable analytical identity rather than only display formatting.

The reviewer independently executed the existing curated continuation fixture through actual replay, history, autonomous and captured-live drivers, removing only the new test helper's input canonicalization. Replay and autonomous retained the identical DetectionConfigHash:

```text
14cfaf0f78a10154c8dcc34eeabd3e435ffa1f3790d3bcb0ae605740681591b2
```

Nevertheless, SQL-loaded evidence differed:

- The same detector semantic event `185dbb35…` had snapshot semantic hashes `b3cb8db3…` and `437bfa2e…`.
- Persisted occurrence `reaction_swing_ref`: `53c9b20a…` versus `b5c11127…`.
- Detector rationale `source_market_event_refs[0]`: `134a0cff…` versus `89808de5…`.

The parity helper currently canonicalizes every mode's inputs before invoking production, removing the triggering inputs. Earlier implementation evidence describing this as a harmless representation difference was incorrect: exact source references and persisted context identities can change under an unchanged detection configuration hash.

Required correction: normalize equivalent settings consistently at the production execution boundaries, preserve existing versioned identity/audit contracts, and retain parity coverage for raw/default settings. Do not fix this by deleting evidence references or relaxing their comparison.

Local reproduction retained for follow-up:

```sh
.venv/bin/python /tmp/astra_raw_parity_probe.py
```

Script: `/tmp/astra_raw_parity_probe.py`; evidence: `/tmp/astra_raw_parity_probe.out`. These are temporary review artifacts, not committed regression tests. The triggering fixture is the existing curated continuation case in `tests/regression/test_cross_mode_parity.py`, executed without its input canonicalization block.

## P2 — SQLite can retain a detector transition from a rolled-back chunk

Locations: `src/market_analysis/application/autonomous_evaluation.py:211` and the final dispatch transaction at line 245.

Under SQLite's default legacy transaction control, SQLAlchemy's `engine.begin()` does not issue a database BEGIN before the first write. If a chunk first creates or advances a pattern instance, the repository's nested SAVEPOINT becomes the actual transaction. Releasing it commits detector writes before the chunk's remaining work. A later exception can roll back context, population membership and progress while leaving that detector transition stored.

The reviewer reproduced this on a fresh SQLite database migrated through `20261106_18`, with chunk size 1 and an injected failure immediately after the first emitted event:

| Observation | Persisted result |
| --- | --- |
| Failing candle | 2026-01-05 12:08 UTC, source index 8, CANDIDATE |
| Run status/progress | FAILED; committed cursor 7; availability 12:08 UTC |
| Pattern instances | 1 |
| Detector transitions | 1, from the failed chunk |
| Detection-time snapshots | 0 |
| EventSet memberships | 0 |
| Stream progress | 8 completed bars; last availability 12:08 UTC |

Durable run reread succeeds and the public event view is empty, while a detector event exists beyond the committed prefix. The failed run remains intentionally non-retryable; a fresh run completes. This violates the new atomic-chunk contract. The explicit SQLite transaction in `migrations/env.py` covers migration DDL only.

This specific mechanism concerns supported SQLite development/test execution. PostgreSQL is the canonical MVP store and does not have this legacy SQLite SAVEPOINT behavior.

Required correction: establish a real SQLite transaction before nested repository writes in both ordinary and final-dispatch chunks. Add a regression where the first event in a chunk is followed by failure, checking the detector table as well as contexts, population and progress.

Local reproduction retained for follow-up:

```sh
.venv/bin/python /tmp/astra_chunk_rollback_probe.py --migrated
```

Script: `/tmp/astra_chunk_rollback_probe.py`; evidence: `/tmp/astra_chunk_rollback_migrated.out`. These are temporary review artifacts, not committed regression tests.

## Independent verification

```sh
.venv/bin/pytest -q \
  tests/unit/test_outcomes.py \
  tests/unit/test_evaluation_runs.py \
  tests/unit/test_autonomous_evaluation.py \
  tests/regression/test_autonomous_plan_parity.py \
  tests/regression/test_cross_mode_parity.py
```

**78 passed, 5 PostgreSQL-dependent skipped**, exit code 0; collection confirmed 83 cases. Both targeted reproduction scripts also completed successfully. The reviewed commit-range diff check passed. The reviewer made no tracked-file, implementation, commit, Jira or installed-database changes.

No additional actionable defect was found in the inspected outcome windows/reference contracts, canonical M1 horizon handling, calendar breaks/day boundaries, typed results, source/build validation, immutable manifests, context-before-selection, approved X predicates, deterministic dispatch, completion gates, explicit worker integration or migration guards. BarSequence verifies actual canonical checksums and the source loader authenticates physical files against persisted metadata.

## Verification limits and next step

PostgreSQL concurrency/migrations/rollback were inspected but not independently executed; the disposable database service was stopped. Browser tests and the full Python suite were not repeated. Earlier author-reported broad test results remain separate implementation evidence. The parity reference shares detector/aggregation implementations, so it verifies orchestration parity rather than independently proving numerical formulas.

Resolve P1 before accepting SCRUM-110 as the basis for SCRUM-123. Resolve P2 and add the first-event rollback regression before relying on the SQLite rollback evidence. Then rerun the affected suites and obtain a focused review of the fixes.
