# Claude review: structural-outcome inventory re-derivation

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-structural-inventory.md).

Reviewed state: current master (post `19874aa`; the evaluation-stage files are
unchanged since the SCRUM-101 delivery). The uncommitted-at-review-time
provider work is now committed and does not touch the scoped files.

## Verdict

**No confirmed findings.** All five brief probes resolve in the
implementation's favor, with measured numbers for the memory question. One
minor coverage observation is recorded at the end.

## Probe results

1. **Missing-open-minute break semantics — correct.** The dedicated fixture
   `test_inventory_reads_future_but_stops_at_missing_open_minute_and_honors_cancellation`
   (tests/unit/test_structural_outcomes.py:383) removes one expected-open
   minute from a real loaded sequence and asserts the truncated inventory
   equals exactly the full inventory filtered to
   `available_at <= missing_minute` — events before the gap are retained
   byte-identically, everything after is dropped. Windows crossing the gap are
   handled by the framework gate:
   `test_structural_window_insufficiency_precedes_event_result` (:363,
   parametrized `missing=True`) pins INSUFFICIENT_DATA precedence over any
   event result. An outcome whose window lies entirely before the gap
   therefore evaluates from the retained prefix; evaluation-from-inventory is
   separately pinned by `test_first_canonical_event_and_exact_original_reference`
   (:111). See the observation below on end-to-end coverage.
2. **Divergence between detection-phase and inventory-phase state — fails
   loudly.** `MarketStateAggregator` follows the verified-pin pattern
   (`indicators/market_state.py:252-269`): the supplied `pinned_config_hash`
   must equal the recomputed resolved detection config hash and construction
   is rejected otherwise; component versions and parameters are inside that
   hash. The inventory constructs the aggregator from the stream's frozen
   `config_json` with `pinned_config_hash=stream.detection_config_hash`
   (`structural_event_inventory.py:96-105`), so a drifted config fails before
   any event is produced. The SCRUM-81 caveat in the aggregator docstring
   (executed detector binding set is pinned separately) does not apply here:
   the inventory runs no detectors. Equality with the detection-phase feed is
   anchored by `test_m5_inventory_matches_full_detector_runtime_canonical_feed`
   (:535), which compares the inventory against the actual DetectorRuntime's
   canonical event feed on a higher-timeframe stream.
3. **Memory bound — measured, negligible.** Measured on the seeded multi-hour
   dataset with the full component chain (measurement script retained in the
   reviewer's session temp directory): 300 source minutes produced 82
   inventory events at ~733 bytes per serialized reference (evidence JSON
   dominating) — ~27 events per 100 minutes. Extrapolated to a full
   1,440-minute M1 trading day: ≈400 events ≈ **0.3 MB per stream**. Even a
   pathological day an order of magnitude denser stays a few MB per stream;
   the retained full `BarSequence` (≈1,440 Decimal OHLC bars) dominates the
   pass's footprint by comparison. The source-day envelope is confirmed as
   the only bound, and it is not a practical constraint.
4. **Cancellation and lease coverage — sound.** `check_stop` fires per candle
   inside `consume` and per source bar in the advance loop; the same :383
   fixture asserts an inventory stop raises through. Heartbeats are
   independent of processing (scheduler thread at 5 s; executor heartbeat at
   5 s) against a 60 s lease, and a single bar's aggregator work is
   microsecond-scale, so the inventory pass cannot silently expire a healthy
   lease.
5. **Day-envelope edges — per contract.** The envelope derives from the last
   execution bar's trading-day bounds (`structural_event_inventory.py:91-94`);
   outcome windows come from the framework's expected-minute gate over the
   full sequence, so a horizon crossing the day boundary is INSUFFICIENT_DATA
   regardless of inventory content, and events whose availability equals a
   window end remain eligible under the SCRUM-120 observable order — pinned by
   `test_framework_preserves_same_close_phase_and_later_detector_ordinal`
   (:487).

## Independent verification

```sh
.venv/bin/pytest -q tests/unit/test_structural_outcomes.py tests/unit/test_outcomes.py
```

Passed within the full-suite run on current master (exit 0). Disposable
SQLite only; no installed database, provider or credentials were accessed; no
tracked files were modified by the reviewer beyond this verdict.

## Verification limits

- The memory extrapolation uses the synthetic demo's event density; real
  US30/DAX data may differ, though structural events (confirmed swings, leg
  transitions) are inherently sparse relative to bars.
- No multi-day source was executed end-to-end (the same-trading-day envelope
  makes one day the design maximum per run).

## Observation (not a finding)

Probe 1's "window entirely before the gap evaluates correctly" property is
covered by composition (prefix retention + evaluation-from-inventory tests)
rather than one end-to-end fixture that evaluates an outcome on the gapped
source. The mechanism is deterministic and the composition is narrow; noted
only in case a future refactor of the inventory return shape wants a single
pinning test.
