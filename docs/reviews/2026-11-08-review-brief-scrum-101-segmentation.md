# Review brief: SCRUM-101 detection-time segmentation

Status: reviewed
Reviewed by: [Claude segmentation review](2026-11-08-claude-segmentation-review.md) — no confirmed findings
Verdict: `2026-11-08-claude-segmentation-review.md`
Priority: high — the only completed evaluation-stage delivery without an
independent review; SCRUM-102 aggregate statistics consumes its membership
iterator directly.

## Scope

Commit: `bc66b30` ("Implement frozen detection-time instrument and session
partitions"), the single delivery commit. Review the change against current
master (no later commit is expected to have touched these files; verify).

Primary files:

- `src/market_analysis/application/segmentation.py` — bucket assignment,
  pinned-descriptor validation, paged membership iterator, group projection.
- `src/market_analysis/application/segment_registry.py`
- `src/market_analysis/domain/segments.py`
- `src/market_analysis/api/segments.py` and the `/evaluation-runs/{id}/segments`
  route.
- `src/market_analysis/application/evaluation_runs.py` — preflight/manifest
  pinning of segment descriptors and plan-hash integration.
- Tests: `tests/unit/test_segmentation.py`, plus segmentation assertions in
  `tests/unit/test_evaluation_runs.py`.

## Contracts to review against

- [SCRUM-101 plan](../plans/2026-11-07-scrum-101-segmentation.md) — the
  approved contract.
- [Implementation verification](2026-11-07-scrum-101-segmentation.md) — author
  self-review; an input claiming coverage, not a verdict.
- `docs/core/technical-architecture.md` — "Research Aggregation and
  Conditional Analysis" (segmentation conditions drawn only from
  detection-time-observable information).

## Areas to probe (hypotheses, possibly unfounded)

1. **Watermark coherence under concurrent execution.** `segment_memberships`
   pages rows by `emission_order <= through` where `through` is a previously
   captured `max(emission_order)`; `evaluation_segments` recomputes counts from
   the same rows and reconciles. Can a chunk committing between watermark
   capture and paged reads produce groups inconsistent with the reported
   watermark, or does the reconciliation always catch it? Check both SQLite and
   PostgreSQL read behavior.
2. **Multi-stream reconciliation with unequal selections.** Descriptor
   `eligible_event_count` sums per-run native counts. Verify with a plan where
   different timeframes select *different* segment definitions, and where one
   stream selects none. The self-review claims this is covered; confirm the
   fixture exercises unequal selections rather than only identical ones.
3. **Unavailable-bucket semantics.** `assign_segment` distinguishes missing
   session state from a real session value. Probe: a session literally named
   `UNAVAILABLE`, whitespace-only names, non-mapping component payloads, and
   that instrument_id can never be unavailable.
4. **Manifest byte compatibility.** The optional descriptor field omitted for
   empty selections must preserve legacy manifest bytes, hashes and identities;
   descriptors are frozen per stream at preflight and re-validated at execution
   (`pinned_segments(for_execution=True)` fingerprint checks).
5. **No future/outcome influence.** Buckets derive only from the persisted
   instrument and captured snapshot. Mutation negatives: later occurrence
   state, outcome values, current calendar evaluation, browser timezone — none
   may change assignment for an already-captured event.
6. **API surface.** `/segment-definitions` is read-only; the run segments view
   labels partial runs incomplete and never implies statistics; pagination and
   payload bounds are explicit.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
- Disposable SQLite, and disposable PostgreSQL via
  `STREAM_ANALYSIS_TEST_DATABASE_URL` where concurrency/dialect behavior
  matters. No installed database, provider credentials or live services.
- The hypotheses above are probes. Verify independently; report honestly when
  nothing reproduces. Where code and the linked contract conflict, that
  conflict is itself a finding.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_segmentation.py tests/unit/test_evaluation_runs.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict in the existing Astra format: reviewer identity, reviewed range,
verdict, confirmed P1/P2/P3 findings with `path:line` locations, reproduction
evidence and commands, verification limits. Link back to this brief.
