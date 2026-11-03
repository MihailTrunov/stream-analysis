# SCRUM-132 — initial chart workspace increment

Status: implemented initial layout increment; full workspace acceptance remains
open until dependent analytical-session/live capabilities are integrated.

## Delivered

- Viewport-sized chart with instrument/chart-timeframe controls at top left and
  navigation/playback plus Patterns/Detector settings at right.
- Local published history visible before replay. A bounded price-only endpoint
  reads one checksum-verified immutable revision, at most 500 returned candles;
  it does not create or advance a replay or contact a provider.
- Pre-start context remains visible during replay. Cached exploration candles
  are filtered out beyond the analytical cursor before rendering a replay.
- Browser-restored instrument/source/timezone/input/configuration/viewport tabs. Only the
  focused tab mounts chart DOM. Changing tabs pauses running historical replay;
  returning or reloading does not automatically resume playback.
- Explicit zoom, fit and return-to-latest controls, retained viewport on tab
  switching/Stop, and UK display/picker time with an optional UTC setting.
- A single date/time picker distinguishes viewport-only Move to from fresh
  replay entry. The existing backend's created state is paused until Play/Step.
- Explicit Apply/restore-default settings and server validation. Read-only replay
  state now returns its pinned detection configuration for faithful restoration.
- Pattern selection focuses the chart; a separate info action previews/pins
  recorded evidence, including keyboard access. Existing manual reviews and
  missed-pattern audit/export remain available in the panel.
- Diagnostics and the two-bar installation check are accessible in a dialog.

The existing application-wide table styles also affected TradingView's internal
table, adding cell padding/margins and clipping the chart's time axis. Scope
those styles away from chart internals; retain normal research/diagnostic tables.
Normalize transport date spellings for causal comparison without rewriting
recorded rationale/evidence.

## Current boundaries

The existing backend permits one active replay. Multiple browsing tabs work,
but another replay must be stopped before launching one in a different tab.
The UI explains that restriction. Local live feeds, higher chart/analytical
timeframes, on-demand/automatic historical detection, grouped as-of occurrence
projections and Runs/results management remain their separate backend/UI stories;
their controls are unavailable rather than simulating those capabilities.

History access is to an existing local revision; full source-range coverage,
provider fetching and indexing remain SCRUM-128. The current checksum-verifying
reader scans a revision with bounded memory; this is not a claim of indexed
year-scale range-read performance. Replay warm-up is still processed by the
existing runtime on advancement; full initialization-before-first-display and
multi-timeframe scheduling remain SCRUM-130/133 work.

For UK date inputs, nonexistent clock-change times and duplicated fall-back times
are rejected explicitly, with UTC available to identify the intended instant.
No source timestamps or analytical bucket anchors change with display timezone.

## Verification

- Python suite: 805 passed, 10 database-dependent tests skipped locally.
- Web unit tests: 16 passed; ESLint, TypeScript/Vite production build passed.
- Targeted Python Ruff/mypy and whitespace checks passed.
- Chrome smoke suite: 11 cases covering chart layout/zoom, initial history,
  replay-future isolation, tab restoration and focus pause, configuration guards,
  evidence inspection, playback/navigation and audited manual annotations.
- An additional targeted Chrome regression verifies that ambiguous UK
  clock-change inputs can be resolved explicitly by switching the picker to UTC.
- Visual inspection at 1440×900 confirmed the main chart layout and visible
  time/price axes. This is local verification, not owner visual acceptance or
  a hosted CI/PostgreSQL result.

## Follow-up

Continue SCRUM-127 contract resolution before SCRUM-129/130. Integrate remaining
SCRUM-132 live/session acceptance with SCRUM-137, then perform the broader
SCRUM-140 supported-browser and recovery acceptance. Existing In Review baseline
stories are not marked Done by this increment.
