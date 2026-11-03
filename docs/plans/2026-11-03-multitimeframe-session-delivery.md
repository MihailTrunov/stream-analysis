# Multi-timeframe session delivery

Implemented on `master` after the owner's instruction to merge the chart branch
and continue without feature branches. The previous branch was fast-forwarded
and pushed to master before this work began.

## Delivered four-task increment

1. **SCRUM-127 contract:** UTC intraday anchors, calendar trading-day daily bounds,
   close availability separate from candle labels, explicit boundary shortening,
   missing-open-minute rejection, pinned initialization origin, stream ordering
   and preservation of existing outcome/qualification version semantics. See the
   [implementation contract](2026-11-03-multitimeframe-session-implementation.md).
2. **SCRUM-129 aggregation:** shared completed-M1 aggregation for
   1m/5m/15m/30m/1h/4h/1d; exact Decimal OHLC and source coverage; M1 bars retain
   their original canonical identity. A revision beginning part-way through a
   bucket skips that first incomplete bucket rather than treating it as complete.
3. **SCRUM-130 local sessions:** independent tab-local replay cursors and runtimes,
   initialized paused replay, every-source-minute scheduling, chart-timeframe
   independence, per-stream persisted run/config/source/build attribution and an
   immutable orchestration manifest. Historical calculations use the same runtime
   and origin; they do not advance another tab's replay or create an evaluation.
4. **SCRUM-131/134/135 local integration:** explicit analytical timeframe selection,
   per-detector enablement/parameters/Live–Manual–Always-on policies, explicit
   Apply, individual Calculate current view, automatic history calculation and
   compatible result reuse. Patterns are occurrence rows in Developing / Active /
   Completed, independently newest first, with retained transitions, timeframe
   badges, as-of projection, separate evidence actions and audited reviews.

An ordinary source or display filter does not enable detectors elsewhere.
Reversal and continuation v1 retain their M1 qualification restriction; higher
analytical timeframes expose compression and reject unsupported configurations.
Existing tab settings migrate to workspace version 3 without promoting dirty
configuration drafts. Missing registered detectors can be added explicitly
without discarding existing component parameters.

## Implementation boundaries

- `domain/aggregation.py` is provider-independent. The session dispatcher has a
  tested finalized-live policy boundary, but actual provider live connections,
  durable live retention and live Apply integration remain SCRUM-136/137.
- New `/analysis` endpoints orchestrate sessions. Legacy `/replay` behavior remains
  compatible. Recorded detector evidence is persisted through the existing
  ReplayEventStore and remains available to the existing audit/export endpoints.
- Migration **20261103_11** adds `analysis_session_revisions`; its manifest pins
  resolved stream hashes, source checksum, calendar/aggregation/build, execution
  identities and initialization origin. Reuse identity excludes viewport/display
  choices; dirty builds do not claim stable cross-session reuse.
- The normal Compose migrate service applies the migration before starting the
  application. Direct local deployments must run `alembic upgrade head` with their
  configured database before using the new endpoints.
- Browser refresh restores paused sessions while the same API process remains
  alive. Full process-restart cursor reconstruction is not implemented. Session
  manifests, detector evidence and annotations remain durable; restoring browser
  settings after an API restart requires a fresh analysis launch.
- History is limited to already retained immutable revisions and bounded returned
  chart windows. Indexed source reads, missing-range provider fetches, continuous
  leftward history loading and year-scale work budgets remain SCRUM-128/133/140.
  Backend initialization still loads the pinned source sequence. Event responses
  include causal transition history; this is not a claim of bounded year-scale
  event-stream memory or payloads.
- Existing verified calendar coverage is unchanged. Later dates require a newly
  verified calendar version. Background Runs/results and numeric outcomes remain
  their existing evaluation delivery stories.

The five-hour synthetic sample has enough preceding data for default M1 and 5m
compression at its suggested start. Longer analytical warm-up needs a later
start or more retained history. A partial 4h/daily sample is not fabricated into
an apparently complete candle. Structural M1 detectors also require their own
longer registered initialization.

## Verification

- Full Python run: **831 passed, 10 skipped** (database-dependent integration
  tests). After the additional shortened-boundary test and final policy check,
  the focused aggregation/session suite passed all **27** cases. The subsequent
  dependency-aware Apply preview regression passed with all **15** session cases.
- Web unit suite: **25 passed**, including as-of projection, pre-window active
  occurrences, terminal reasons and higher-candle availability and selected off-timeframe annotation gating.
- ESLint with Hooks dependencies, TypeScript/Vite build and targeted Python
  Ruff/mypy passed.
- Chrome final full acceptance: **24 passed**, covering prior replay/annotation
  flows, independent tab restoration, five-minute Step, slow/failing reads,
  individual and automatic historical calculation, display-only result reuse and
  migrated detector additions. A grouped-pattern screenshot was visually inspected.

## Jira checkpoint

SCRUM-127 and SCRUM-129 are In Review. SCRUM-130/131/134/135 are In Progress
with the local increment implemented and broader provider/range/integration
acceptance still open. No story has been marked Done by this increment.


This records local automated verification. Provider live-account acceptance,
PostgreSQL integration and full restart recovery are not claimed.
