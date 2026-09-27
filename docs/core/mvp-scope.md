<!-- Migrated from Google Docs on 2026-09-27. Historical source document ID: 17g2A3xgo83vWHQmMjOgQJTOuM6eQmhP9pKxTiF1gfRE. Source revision at migration: ANLCKQnuQJdKqyAkaeGv6Y53y9HdJpTCHLeljixh8mraeyFKvRO4wAGTW30iaPlXHV6gJKWKgOsVyjBv5CYTwFKbOtjnnJBH3LMXCuJFlEw. -->

# MVP Scope & Boundaries — Research and Replay MVP

## MVP Objective

- Prove the core research loop: define → replay → inspect → modify → batch-test → compare.

- Demonstrate that the same deterministic market-state and pattern runtime can operate in historical replay and later in live mode.

- Prioritize analytical correctness, transparency, and usefulness over instrument scale, alerting infrastructure, or production operations.

## Included in the MVP

- Historical 1-minute OHLC loading for a small instrument set.

- Initial focus on US30 and DAX, subject to available data.

- Deterministic bar-by-bar replay engine.

- Interactive candlestick chart with progressive drawing.

- Play, pause, step-one-bar, playback speed, jump-to-time, and jump-to-next-event controls.

- EMA-45 calculation and existing trend-leg detector.

- ATR and basic volatility context.

- Swing-point / swing-structure detection with explicit detection-time semantics.

- Provisional RangeState with independent choppiness/range-likeness and relative compression axes; persistent structurally bounded range construction is post-MVP.

- Support for three manually defined pattern families:

  • trend reversal

  • range compression / breakout preparation

  • trend continuation

- Stateful pattern lifecycle and visual annotations.

- Event log showing timestamp, detector, state transition, rationale, and version.

- Batch execution of the same detectors over selected historical periods.

- DetectionTimeContextSnapshot capture, typed OutcomeDefinitions/OutcomeObservations, EventStudyAggregates, and basic segmentation.

- Detector/version lineage plus separate DetectionAnalysisConfig/DetectionConfigHash and EvaluationPlan/EvaluationPlanHash lineage.

- Manual validation tags on detected events where practical.

## Minimum Visualisation Requirements

- Candlestick chart.

- EMA-45 overlay.

- Trend-leg state overlay or marker.

- Swing markers.

- Range/compression band or shading.

- Pattern candidate/confirmation/invalidation annotations.

- Current market-state summary.

- Detector-event timeline or list.

- Click/select an event to inspect why it fired.

- Session/time markers sufficient to distinguish relevant intraday windows.

## Minimum Autonomous Evaluation

- Pattern occurrence count.

- Forward returns at configurable horizons such as 5, 15, 30, and 60 bars.

- MFE and MAE for configurable horizons.

- Time to positive/negative threshold where useful.

- Subsequent swing or structure-break outcome where defined.

- Breakdown by instrument and session.

- Minimum sample-size display for every aggregate.

- Exportable or persisted leakage-safe research data containing source DetectorEvents, DetectionTimeContextSnapshots, OutcomeObservations and required lineage.

## Required Correctness Properties

- No detector can access future bars relative to the replay cursor.

- Structures that require confirmation from later bars must preserve both event_time and detection_time.

- Replaying the same dataset with the same configuration must produce identical results.

- Visual annotations must be generated from the same DetectorEvents used in batch statistics.

- PatternDefinition and parameters must be versioned.

- Research outputs must preserve source-data lineage, DetectionConfigHash, EvaluationPlanHash where applicable, detector/outcome versions and code revision needed for reproduction.

- Detection, outcome evaluation, and strategy simulation must remain separate concepts.

## Initial Pattern Scope

- Trend reversal: deterministic state-based formula using measurable context such as preceding directional state, rejection/structure change, EMA interaction, swing transition, or S/R proximity.

- Range compression: provisional detector built from observable RangeState choppiness/range-likeness plus relative compression, with explicit persistence and hysteresis. Persistent structural range boundaries are excluded from this v1 detector.

- Trend continuation: formula using active directional context, pullback/reaction behaviour, and renewed structure/momentum confirmation.

- Exact formulas are expected to evolve; the MVP proves the framework and review workflow rather than declaring final market definitions.

## Technical Boundary

- Prefer a modular monolith with clear internal module contracts.

- Suggested modules: market_data, simulation, indicators, structure, patterns, evaluation, API/UI.

- No requirement for independent microservices, Redis, Kafka, or distributed orchestration in the MVP.

- Storage may be relational/time-series plus local/object files as appropriate; technology choice should support reproducible runs rather than optimize prematurely for very large scale.

- Data-source adapters must be replaceable.

- The pattern engine must be UI-independent.

- Replay speed may exceed real time; live-latency SLOs are not an MVP acceptance criterion.

## Explicitly Excluded from the MVP

- Real-money order execution.

- Broker order management.

- Production live alert delivery.

- Large 100-instrument universe.

- ML prediction or ranking.

- Automated parameter optimization.

- Genetic algorithms or exhaustive grid search.

- Full portfolio analytics.

- Mobile application.

- Production-grade high-availability infrastructure.

- Complex user/account/permission management.

- Broad catalogue of traditional named candlestick/chart patterns.

- Claiming a detector is a profitable strategy without a separate strategy simulation.

## MVP Acceptance Criteria

- User can select instrument and historical interval and start a replay.

- Chart advances progressively without exposing future bars to detectors.

- EMA-45 leg logic is visible and produces reproducible events.

- At least one implementation of each initial pattern family can progress through explicit states and display its rationale.

- User can pause at an event and inspect the conditions that caused the current detector state.

- Same detector definitions can run in autonomous batch mode over a larger historical period.

- Batch results preserve event-level OutcomeObservations and aggregate counts plus forward-return, MFE and MAE measures, with explicit missing/ambiguous status counts.

- Results can be segmented at minimum by instrument and session.

- Detector version and DetectionConfigHash are attached to persisted detection artifacts; evaluation results additionally preserve EvaluationPlanHash and OutcomeDefinition lineage.

- A regression test demonstrates identical detector output for the same ordered bar sequence across repeated runs.

- Basic manual chart review can be recorded against detected events.

- No known future-data leakage remains in accepted detector logic.

## MVP Completion Decision

- The MVP is successful if the define/replay/inspect/batch-test loop is useful enough to support repeated pattern-research iterations.

- Success is not defined by achieving a fixed alert win rate or PPV target.

- If the research loop is not useful, live alerting and production scaling should not proceed until the analytical workflow is corrected.

## Post-MVP Sequence

- Add richer S/R, persistent BoundedRangeState with structural boundaries, and multi-timeframe context.

- Expand detector library and event-study metrics.

- Add strategy simulation with explicit entries/exits, costs, and walk-forward testing.

- Add live data mode using the same runtime.

- Compare captured live events with replay regression.

- Add optional alert policies and delivery.

- Expand instruments only after detector and data semantics are stable.

- Consider a pattern-definition DSL after enough detectors exist to reveal the real abstraction requirements.

- Consider ML/adaptive ranking only after a sufficiently large, versioned, and validated event dataset exist.s

Approved MVP reconciliation — 2026-09-24

Execution and UI

- MVP is single-user and local to the researcher's Mac. Latest Chrome on macOS is the supported browser target.

- One Docker Compose startup launches PostgreSQL, API, autonomous-evaluation worker, import worker and browser UI. API/UI bind to localhost; PostgreSQL is not exposed outside the Compose network.

- Network access is required only for OANDA historical import. Replay and evaluation operate offline from local immutable dataset revisions after import.

- Browser parameter editing is schema-driven and server-authoritatively validated. Saving a changed named preset creates a new immutable revision; formulas/rule combinations are code-defined/versioned rather than authored in the browser.

Evaluation scheduling

- The UI can schedule multiple autonomous evaluation runs into a durable PostgreSQL-backed queue.

- Exactly one evaluation executes at a time in queue order. Parallel evaluation execution is post-MVP.

- A manual stop marks the active job CANCELLED, preserves incomplete output and pauses automatic queue progression until explicitly re-enabled. A stale-heartbeat recovery marks a job FAILED; automatic checkpoint resume is post-MVP.

- Evaluation may run concurrently with an interactive live walkthrough; autonomous work must not make the walkthrough unavailable.

Data/reproducibility

- Historical datasets are immutable revisions of normalized one-minute bars stored as versioned Parquet. PostgreSQL stores manifests/checksums/provenance and analytical/job/configuration records.

- Canonical timestamps are UTC. Exact duplicate bars collapse deterministically; conflicting duplicates fail validation; missing bars are never synthesized.

- Unexpected gaps during warm-up or the selected detection interval block launch. Outcome-only gaps produce INSUFFICIENT_DATA for affected outcomes rather than extending the window.

- Instrument/account-specific trading-day boundaries use versioned IANA timezone calendars with scheduled breaks and holiday exceptions.

- Every replay/evaluation/export records dataset revision, resolved configuration hashes, detector/outcome versions, configuration-schema and calendar versions, and app Git/build identity.

Replay/research behavior

- Required warm-up is calculated from registered component requirements plus structural initialization buffer. Required history is shown before launch and cannot be reduced below the calculated minimum.

- Event selection uses detection_time inside the selected interval while analytical state carries through warm-up. Outcome observation may extend beyond interval end only within the configured horizon and same trading-day policy.

- Completed evaluations provide Review mode, which may show later outcome-window bars without altering progressive-replay causal visibility.

- Results support aggregate-to-event drill-down, unavailable-outcome reasons, chart/evidence inspection, event-level and aggregate export with lineage, and side-by-side comparison of two evaluation runs.

TrendLeg

- The approved MVP TrendLeg is the structural protected-swing model. EMA-cross segments are separate raw intervals and do not terminate TrendLegs by themselves.

- Structural establishment/termination, protected-swing advancement, strict swing comparisons, 30-bar/70-point provisional qualification and qualification persistence follow the canonical Core Domain Model and Jira contracts.

Repo reconciliation update — 2026-09-26

Browser configuration surface

- Every registered detector exposes an enable/disable control, its code version, and only thresholds, bar counts, expiry values or filter switches declared by that detector implementation.

- Shared editable groups include EMA and ATR periods, SwingPoint confirmation widths, SwingStructure hysteresis, TrendLeg qualification bars/points, and outcome horizons.

- The optional 35-point retracement filter is disabled by default and appears only for detectors whose approved rule table defines its effect. Protected-swing termination and detector formulas are not browser-editable.

Provisional warm-up policy

- Each enabled indicator, structural component and detector declares a warm-up requirement in its versioned configuration schema.

- EMA requires 5 × period completed bars. ATR requires 5 × period + 1 completed bars. Other enabled components contribute their largest declared historical lookback.

- Take the maximum base requirement, then add 2 × (left + right + 1) completed bars when SwingPoint confirmation widths are enabled; otherwise add zero.

- The API shows this minimum before launch. The researcher may increase but not reduce it. This is a provisional experimental starting policy, not a guarantee that structural state has formed; policy changes apply only to new versioned configurations/runs.

Instrument research defaults

- US30 and DAX retain separate presets. DAX's provisional 30-bar / 70-point / 35-point baseline is copied from US30 and remains explicitly unvalidated for DAX.

- The 35-point value is recorded evidence and a disabled-by-default experimental detector filter, not a permanent TrendLeg qualification veto.

Implementation-time decisions

- Exact OANDA trading-day/calendar values and daylight-saving/holiday fixtures are verified during calendar/import/outcome implementation.

- The chart viewport bar cap and display-aggregation bucket/alignment rules are chosen from measured Chrome/Mac performance during chart implementation.

- Benchmark detector/configuration/outcome versions, dataset revision and reference-Mac hardware are pinned during benchmark implementation. These details do not block unrelated MVP work.
