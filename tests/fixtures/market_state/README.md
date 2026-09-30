# SCRUM-111 market-state reference fixtures

These v1 JSON files are checked-in, hand-reviewed expectations. The test harness
reads them but never produces or updates them from implementation output. A
semantic change requires review of both the component version and the affected
fixture values; do not blindly re-record a passing implementation.

| Fixture | Contract pinned |
| --- | --- |
| `ema_v1.json` | Period-3 SMA seed, unavailable prefix, then completed-close EMA. |
| `atr_v1.json` | First-bar and prior-close price-jump true range, period-2 SMA seed, then Wilder smoothing. |
| `session_v1.json` | Pinned London calendar, winter/summer local time, exact window boundaries, break and holiday. |
| `range_v1.json` | Hand-calculated CHOP and population bandwidth; compression unavailable without reference history. |
| `range_warmup_v1.json` | Independent 14/20/120 readiness, strict empirical percentile and ties. |
| `integrated_chain_v1.json` | SwingPoint delayed confirmation; SwingStructure classification and close break; structural TrendLeg and qualification; ordered MarketState event stream, timestamps, lineage and stable serialization. |

The integrated chain has no session calendar so its component order remains
isolated. The session fixture tests the calendar through the MarketState
aggregator with a pinned version. The in-memory frame retains a local-time
`session.local_timestamp`; `debug_json()` canonicalizes datetime
values to UTC, so the session fixture checks the in-memory local timestamp.

The compact catalog is complemented by explicit boundary fixtures in the
focused tests: `test_ema.py` (period-1 and seed boundaries), `test_atr.py`
(gap and period-1), `test_session_calendar.py` (overnight trading dates and DST
invalidity), `test_swing_point.py` (ties, alternating extrema and missing ATR),
`test_swing_structure.py` (equal-level tolerance, exact break threshold and
one-shot events), `test_trend_leg.py` (structural versus EMA-cross evidence and
protected-swing termination), `test_trend_leg_qualification.py` (duration/move
gates), `test_range_state.py` (degenerate windows and percentile boundaries),
and `test_market_state.py` (reset/replay parity and immutable frames). These
cases are authored literals, not snapshots generated during test execution.
