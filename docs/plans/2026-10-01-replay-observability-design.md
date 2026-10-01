# Replay observability: SCRUM-90–92

The browser walkthrough already exposes bounded completed candles. Add a single
cursor-clamped `/replay/{run_id}/view` read that returns the same candle viewport
plus MarketState observations and emitted DetectorEvents from committed
`ReplayPipeline.steps`. The manager lock makes the response a coherent snapshot.
Keep `/bars` for existing clients.

Two alternatives were considered: separate overlay/event requests (can display
different cursor snapshots during playback) and browser-side recomputation of
indicators (breaks canonical rules and detection-time causality). A combined
read-only projection is the least complex consistent contract. No replay state
is advanced by a view request.

SCRUM-90: for each visible bar, project enabled component observations from
its `MarketStateFrame`: EMA instances and period/value, active TrendLeg and
transitions, confirmed SwingPoint/SwingStructure market events, RangeState
scores/regimes and session facts. Preserve event and detection timestamps.
Render values only when present; the small seeded compression demo intentionally
does not select EMA or the structural chain. Structural overlays therefore
appear with an appropriate configuration/dataset, not fabricated demo output.
Toggles are local browser display settings.

SCRUM-91: project emitted DetectorEvents with run-local instance identity,
sequence, lifecycle transition, trigger, event/detection times and rationale.
Annotations are emitted only at/after detection time; the chart can locate an
older event-time candle only after the event becomes visible. Selecting an
annotation opens the exact event payload, not a reconstructed pattern state.

SCRUM-92: use the same event records in a deterministic timeline ordered by
completed-bar processing order then within-bar emission order. Use
`instance_id + sequence` as event identity within a run. Pattern/state filters
are local, non-mutating projections. Selecting a timeline row highlights its
annotation and candle context without seeking or advancing the replay cursor;
the selected event remains inspectable if its event-time candle is outside the
current 100-bar viewport. Reset/seek replace the run and clear selection.

Validation: API fixture tests for no future data, event order, swing detection
timing, viewport bounds and seek parity; browser tests for overlay toggles,
annotation inspection, same-bar ordering/filtering and chart synchronization.
The README keeps the launch and demo behavior current.
