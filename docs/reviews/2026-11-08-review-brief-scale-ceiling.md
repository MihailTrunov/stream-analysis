# Review brief: evaluation-stage scale ceiling

Status: unassigned
Reviewed by: —
Expected verdict: `2026-11-XX-astra-scale-ceiling-review.md`
Priority: medium — analysis and decision brief; no defect is presumed. It
should produce an accepted ceiling before SCRUM-102 makes long source ranges
attractive to select.

## Scope

Horizontal analysis of current master. Primary files:

- `src/market_analysis/application/autonomous_evaluation.py` — full-source
  load, per-stream aggregators and runtimes held in memory, chunked commits.
- `src/market_analysis/persistence/dataset_store.py` — `load_sequence`
  materialization of the complete M1 source.
- `src/market_analysis/application/structural_event_inventory.py` — second
  full-source pass with materialized event lists.
- `src/market_analysis/application/evaluation_outcomes.py` — per-event task
  derivation and instance loads.
- `src/market_analysis/application/evaluation_runs.py` (`evaluation_events`)
  and `src/market_analysis/api/evaluations.py` — read-path payload shapes.

## Contracts to review against

- [SCRUM-96](../plans/2026-11-06-scrum-96-autonomous-execution.md): chunking
  "is not a claim of constant-memory streaming" — the documented honesty about
  this limitation.
- `docs/core/technical-architecture.md` — local single-user MVP; no
  infrastructure for anticipated future scale without an observed need.
- `docs/plans/2026-11-06-autonomous-evaluation-sequence.md` — stage scope.

## Areas to probe (hypotheses, possibly unfounded)

1. **Measure a representative run.** On disposable data (seeded or synthetic
   revision), measure wall time and peak RSS for: a multi-day M1 revision with
   M1+M5 streams, full component set, structural outcomes selected, chunk size
   128. Record detection phase, inventory pass and outcome phase separately.
2. **Extrapolate the ceiling.** Given preflight constraints
   (`selected_end <= lineage.requested_end`, warm-up rules), what is the
   largest selectable revision today, and what does the measured slope predict
   for it? Identify the first binding constraint (memory, per-event outcome
   cost, API payload, or none at MVP scale).
3. **Read-path payloads.** `evaluation_events` inlines each event's full
   context snapshot and all its observations; pagination caps at 500. Estimate
   response sizes at the ceiling and flag if SCRUM-102's UI will need
   field-limited views before aggregate statistics land.
4. **Decision request.** Recommend: an accepted scale ceiling (bars × streams)
   to document in `technical-architecture.md`; whether any monitoring hook
   (log line, diagnostic field) is warranted now; and whether SCRUM-102 should
   include batching/pagination of aggregate reads by design. Recommend only —
   this brief expects an analysis record, not code changes.

## Constraints for the reviewer

- Review/analysis only; no tracked-file, commit, Jira or installed-database
  changes. Synthetic/disposable data only; no provider access.
- Measurements are estimates for a decision, not benchmarks: record hardware,
  Python version and dataset identity alongside numbers.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_autonomous_evaluation.py
```

Functional suite only as a sanity check; the deliverable is measurement.

## Expected output

A verdict-format document containing the measurements, the extrapolated
ceiling, the first binding constraint, and the probe-4 recommendation.
Link back to this brief.
