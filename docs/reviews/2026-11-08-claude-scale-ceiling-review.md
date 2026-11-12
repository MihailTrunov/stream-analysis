# Claude review: evaluation-stage scale ceiling

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-scale-ceiling.md). This is an
analysis-and-decision brief; no defect was presumed.

Reviewed state: current master (`cc7609b`).

## Verdict

**No findings. The current design is comfortably within its intended scale.**
Measured numbers, the first binding constraint, and the recommended accepted
ceiling follow.

## Measurements

Hardware: the development Mac (Apple silicon), Python 3.13.7, disposable
SQLite, synthetic demo data. Scripts retained in the reviewer's session temp
directory (`measure_scale.py`, `measure_sequence.py`,
`measure_inventory.py`).

1. **Representative execution** (demo multihour source: 300 M1 minutes, two
   streams M1+M5, 11 registered outcome selections, chunk 128):
   preflight/create 0.11 s; full `execute_evaluation` (detection + inventory +
   outcomes) **4.47 s**; 132 observations persisted; process high-water RSS
   ≈ **110 MB** (dominated by interpreter/SQLAlchemy/import baseline, not
   analytical data).
2. **Sequence materialization** (tracemalloc around `load_sequence`):
   **≈1.1 KB per bar**. Extrapolations: 30 trading days (43,200 M1 minutes)
   ≈ 46 MB; one trading year (≈374,400 minutes) ≈ **400 MB** for the
   `BarSequence` alone, plus the ≈110 MB baseline and small per-stream
   aggregator/runtime state.
3. **Structural inventory pass** (full component chain, from the structural
   review's measurement): 0.25 s per 300 minutes and ≈60 KB of event
   references — negligible at every foreseeable scale.
4. **Throughput slope**: ≈14.9 s per 1,000 source minutes for the measured
   configuration → one full M1 trading day ≈ 21 s; 30 days ≈ 10.5 min; a
   trading year ≈ 93 min of serial execution.

## Extrapolated ceiling and first binding constraint

At weeks-scale ranges (the current intended use), nothing binds: tens of
seconds, well under 200 MB. The **first constraint to bind at multi-month
scale is serial wall time** (the queue executes one job at a time by design),
with sequence RSS second (~400 MB at one year, still fine on this hardware
but no longer trivial). Outcome-phase cost scales with events × selections
and stays linear in committed batches; read-path payloads are capped
(500-event pages with inline context/outcomes — a few MB per page worst
case, acceptable locally).

## Probe-4 recommendation

- **Accepted ceiling to document**: runs selecting up to **a few months of
  M1** per evaluation are safe by measurement and extrapolation (≤ ~15 min
  serial, < 150 MB sequence). Year-scale single runs are technically feasible
  on this machine but hold the serial queue for ~1.5 h and reach ~0.5 GB —
  measure a real-data run before any UI makes such ranges one click away.
- **No monitoring hook now**: the existing structured logs already carry
  source_minutes and chunk sizes; a duration/RSS log line per phase would be
  nice-to-have, not warranted before SCRUM-102.
- **SCRUM-102 guidance**: keep aggregate reads paged (its contract already
  says so); nothing in the measured numbers forces batching changes in the
  outcome executor before aggregate statistics land.

Record this ceiling in `docs/core/technical-architecture.md` (implementation-
baseline section) when convenient.

## Verification limits

- Synthetic demo data: real US30/DAX event density and Decimal prices may
  shift constants (not shape); the wall-time slope was measured with the demo
  component set (ATR+RangeState) — the full component chain's detector cost
  was exercised in the structural review's parity fixtures at similar scale.
- Single run of each measurement (variance not characterized); SQLite only
  (PostgreSQL persistence cost not re-measured; prior full-suite timings
  bound it).
