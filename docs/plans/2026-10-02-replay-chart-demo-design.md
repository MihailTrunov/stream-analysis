# Replay chart and offline demo design (2026-10-02)

## Scope

SCRUM-125 and SCRUM-126 complete the initial browser replay surface after
SCRUM-94. The chart remains a presentation of canonical, cursor-bounded API
data; it never computes market state or detector results. The offline sample
remains synthetic and non-research-grade.

## Chart

Use TradingView Lightweight Charts as specified in
`docs/core/technical-architecture.md`. The alternative of adding scales and
navigation to the custom SVG would require maintaining a second chart engine
without improving the analytical boundary. A candlestick series provides the
price and UTC time scales, crosshair, and user-controlled viewport. Dedicated
series/markers render available EMA, swings, structure, range, trend-leg,
session and detector observations. Chart clicks select visible bars for missed
reviews; selecting a timeline event focuses and highlights its detection-time
marker. The chart never receives observations or bars beyond the active cursor.
The surrounding text timeline, inspector and review controls remain usable
without pointer interaction with the canvas.

## Seed

Publish a new immutable v2 revision for US30 and DAX. Keep the historical v1
14-bar revision for old runs and regression fixtures, but select v2 by default
in the browser. Generate deterministic minute OHLC across at least four
contiguous open-session hours plus warm-up. Shape several regimes—directional
move, pullback, tight oscillation and release—without treating synthetic
values as provider history. The generator uses fixed arithmetic, stable
provenance and checksums. Reseeding is idempotent and does not replace a
published revision. The UI labels the synthetic source prominently.

## Failure behavior and verification

If the chart cannot initialize, show an actionable error while preserving the
timeline and replay controls. Invalid or non-finite prices must not enter a
chart series. The API continues to reject insufficient warm-up and open-session
gaps. Unit tests cover seed continuity, deterministic identity/checksum and
chart-to-event mapping; browser smoke tests cover scales, interactions,
progression and no-future-data behavior. The sample must produce an observable
default detector lifecycle without credentials or network access.
