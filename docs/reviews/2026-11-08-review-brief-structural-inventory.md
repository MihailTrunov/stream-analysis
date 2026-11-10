# Review brief: structural-outcome inventory re-derivation

Status: reviewed
Reviewed by: [Claude structural-inventory review](2026-11-08-claude-structural-inventory-review.md) — no findings; memory bound measured (~0.3 MB/stream/day)
Verdict: `2026-11-08-claude-structural-inventory-review.md`
Priority: medium — the SCRUM-99/100 Astra review found no structural defect,
but lifecycle concerns (gap handling, memory, divergence) were not its focus.

## Scope

Horizontal review of current master. Primary files:

- `src/market_analysis/application/structural_event_inventory.py` — the
  downstream producer pass and its canonical references.
- `src/market_analysis/application/structural_outcomes.py` — definitions and
  algorithms consuming the inventory.
- `src/market_analysis/domain/outcomes.py` — `OutcomeEventReference`,
  observable order.
- `src/market_analysis/application/outcome_evaluation.py` — event bounding and
  order filtering (`bounded_events`).

## Contracts to review against

- [SCRUM-100 plan](../plans/2026-11-07-scrum-100-structural-outcomes.md) —
  same pinned producer, downstream-only future consumption, stop on first
  missing open minute, no detector execution.
- [Implementation verification](2026-11-07-scrum-100-structural-outcomes.md)
  (author self-review) and the structural portion of the
  [Astra threshold/structural review](2026-11-07-astra-threshold-structural-review.md).
- `docs/core/technical-architecture.md` — structural outcomes must consume the
  same confirmed/versioned structural events rather than recomputing
  retrospectively.

## Areas to probe (hypotheses, possibly unfounded)

1. **Missing-open-minute break semantics.** The pass silently `break`s at an
   unexpected gap (`structural_event_inventory.py:126-136`), retaining events
   before it and dropping all later bars. Verify every outcome whose window
   crosses the gap reports INSUFFICIENT_DATA via the framework's
   expected-minute gate, and that an outcome window lying entirely *before*
   the gap still evaluates correctly from retained events.
2. **Divergence between detection-phase and inventory-phase state.** The
   inventory re-runs `MarketStateAggregator` from the same initialization
   origin using `stream.config_json`. Confirm component versions, config hash
   and calendar pinning are checked identically to the detector runtime, and
   that semantic references bind to the same input identity. Characterize the
   failure mode if a definition drifts: loud failure (preferred) vs silent
   divergence.
3. **Memory bound.** Per-stream event lists are materialized for the whole
   source-day envelope (`events: list[OutcomeEventReference]`). Estimate the
   footprint for a full trading day of M1 with all structural components
   enabled across several streams; confirm the day envelope is the only bound
   and document the ceiling.
4. **Cancellation and lease coverage.** `check_stop` fires per candle inside
   `consume`; the scheduler heartbeat runs on its own thread. Verify a slow
   inventory pass cannot exceed the 60-second lease with a live heartbeat,
   and that stop requests surface promptly.
5. **Day-envelope edge cases.** The envelope limit derives from the last
   execution bar (`execution_bar_count - 1`, `:91-94`). Verify outcomes whose
   window extends past the envelope are INSUFFICIENT_DATA, and that an event
   whose availability equals a window end is handled per the SCRUM-120
   observable-order contract.

## Constraints for the reviewer

- Review only; no tracked-file, commit, Jira or installed-database changes.
- Disposable SQLite; disposable PostgreSQL only if a dialect-specific behavior
  is probed. No installed database, providers or live services.
- Hypotheses may be unfounded; report honestly when nothing reproduces.

## Verification baseline

```sh
uv run pytest -q tests/unit/test_structural_outcomes.py tests/unit/test_outcomes.py
uv run ruff check src tests
uv run mypy
```

## Expected output

A verdict in the existing Astra format. Findings carry `path:line` locations,
reproduction evidence and verification limits; probe 3 expects a documented
estimate even with no finding. Link back to this brief.
