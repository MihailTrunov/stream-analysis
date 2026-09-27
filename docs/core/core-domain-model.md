<!-- Migrated from Google Docs on 2026-09-27. Historical source document ID: 1DXooBOXpjOis-iq3aWtU7xI6M6_X_Z-0ApmzfOSvfIU. Source revision at migration: ANLCKQm3mVj87c8YCALlkphzNcqVMM44ZzwMzGNLj9r3uMafa-uhA_unDqRunCnplzND-F0TZheAodmzXodcDWPzS1RhsE0z0NI6V52CI6s. -->

# Core Domain Model — Intraday Market Analysis Platform

## Purpose

- Define the core concepts, relationships, lifecycle states, and terminology used across historical replay, batch research, and live analysis.

- Keep analytical logic independent from the data source and user interface.

- Provide a stable model that future implementation plans and sprints can reference.

## 1. Market Data

- Instrument: uniquely identifies the traded market or provider symbol; includes display name, venue/provider mapping, timezone, tick/point conventions, and session configuration.

- Bar: canonical OHLCV record with instrument, timeframe, timestamp, open, high, low, close, optional volume, source, and data-quality flags.

- BarSequence: ordered bars for an instrument/timeframe over a defined interval.

- SessionCalendar: versioned instrument/account-specific IANA-timezone configuration defining trading sessions, market opens/closes, analytical trading-day boundaries, scheduled breaks, holidays/exceptions, and deterministic UTC/local conversion.

- DataSource: adapter that provides historical or live bars. Examples include OANDA, CSV/Parquet, broker API, or another vendor.

## 2. Simulation and Replay

- ReplayRun: deterministic processing of a historical BarSequence under a selected configuration.

- SimulationClock: controls which bar is currently observable and advances one bar at a time or at accelerated speed.

- ReplayCursor: current position within a ReplayRun.

- ReplayControl: play, pause, step, jump, speed, and stop-on-condition behaviour.

- Snapshot: optional persisted state at a replay point for debugging or verified seek optimization. MVP correctness does not depend on persisted per-bar state, and automatic autonomous-evaluation checkpoint resume is post-MVP.

- Rule: no analytical component may access bars beyond the current SimulationClock position.

## 3. Market-State Primitives

- IndicatorValue: derived numeric value such as EMA, ATR, slope, rolling range, or volatility.

- EMA-cross segment: raw directional interval between opposing completed-bar EMA crossings; detector evidence only and distinct from TrendLeg. TrendLeg: structurally protected directional state established from confirmed swing progression, able to survive EMA pullbacks, and terminated only by a confirmed close-break of its protected opposite swing.

- SwingPoint: local structural high/low with both event_time and detection_time where confirmation requires future bars.

- SwingStructure: current HH/HL/LH/LL state and structure-break information.

- SupportResistanceZone: price band with centre, width, source evidence, interaction statistics, and optional score.

- RangeState: observable rolling market-state primitive with independent choppiness/range-likeness and compression axes. Range-like or compressed state does not by itself assert a persistent structurally bounded trading range; persistent BoundedRangeState is a separate post-MVP concept.

- VolatilityState: contextual volatility classification or score derived from ATR or related measures.

- SessionState: current session, time bucket, opening-range context, and other intraday timing features.

- MarketState: immutable or reproducible aggregate of all currently observable primitives at a bar, plus a deterministic ordered `market_events_this_bar` feed of canonical events such as confirmed swings, structure breaks and TrendLeg transitions.

## 4. Pattern Definitions

- PatternDefinition: deterministic specification of a market behaviour to detect.

- PatternId: stable semantic identifier such as range_compression or trend_reversal.

- PatternVersion: immutable version of a PatternDefinition.

- PatternParameters: configurable thresholds, windows, tolerances, and feature settings.

- ContextCondition: conditions that establish where a pattern is meaningful, such as active trend leg, S/R proximity, session, or volatility.

- FormationCondition: conditions indicating that the pattern is developing.

- ConfirmationCondition: conditions required to mark the pattern as confirmed.

- InvalidationCondition: conditions that cancel or fail the developing pattern.

- CompletionCondition: conditions that mark an active pattern as finished.

- ExplanationRule: structured rationale attached to detector events.

## 5. Pattern Runtime and Lifecycle

- Detector: runtime component that applies one PatternDefinition to sequential MarketState updates.

- PatternInstance: one occurrence of a pattern being tracked through time; includes run-local persistence identity, deterministic `instance_semantic_key`, lifecycle state, and typed/versioned detector-specific instance context.

- Shared lifecycle vocabulary supports INACTIVE/INELIGIBLE where used, ELIGIBLE, CANDIDATE, FORMING, CONFIRMED, ACTIVE, COMPLETED, INVALIDATED and EXPIRED, with explicitly declared detector-specific intermediate states such as RECLAIMED.

- Not every pattern uses every state. Each PatternDefinition declares its exact state subset, allowed transitions and same-bar precedence; multiple transitions on one completed bar are permitted only when explicitly defined and must be emitted in deterministic order.

- DetectorEvent: immutable record emitted when a detector materially changes a PatternInstance; includes a deterministic semantic key, within-bar ordinal, structured rationale/evidence, source canonical market-event references where applicable, and version/configuration lineage.

- event_time: market time at which the underlying behaviour occurred.

- detection_time: earliest time at which the system had sufficient observable information to emit the event.

- rationale: conditions and feature values supporting the transition.

- detector_version/DetectionConfigHash: detection lineage required for reproducibility. Downstream evaluation artifacts additionally carry EvaluationPlanHash where applicable.

## 6. Evaluation and Research

- EvaluationRun: autonomous historical processing of one or more PatternDefinitions over a dataset.

- EventSet: collection of DetectorEvents or confirmed PatternInstances selected for analysis.

- DetectionTimeContextSnapshot: immutable versioned snapshot of information observable at a source DetectorEvent detection_time; the canonical X-side record for conditional research and future leakage-safe prediction.

- OutcomeDefinition: immutable versioned specification of a post-event observation, including typed configuration, output type, reference mode, observation window, eligibility/status semantics, units/normalization and lineage. Numeric OutcomeMetric is one subtype/category rather than the only outcome form.

- OutcomeObservation: immutable typed event-level result linking a source DetectorEvent to the exact OutcomeDefinition/version/configuration used, with explicit AVAILABLE / INSUFFICIENT_DATA / NOT_APPLICABLE / AMBIGUOUS status semantics.

- Segment: conditioning dimension such as instrument, session, weekday, volatility regime, S/R context, trend context, or pattern version.

- EventStudyAggregate: derived statistics over OutcomeObservations for an EventSet/Segment. Every aggregate preserves eligible sample size plus explicit outcome-status counts; aggregates never replace event-level observations.

- ResearchDataset: leakage-safe event-level dataset containing DetectorEvent, DetectionTimeContextSnapshot, OutcomeObservation(s), and complete data/config/detector/outcome/code lineage. ResearchComparison compares versions, parameters, instruments, periods or segments without automatically declaring one universally superior.

## 7. Strategy Simulation

- StrategyRule: optional mapping from selected detector events to explicit entry, stop, target, exit, sizing, and management logic.

- SimulatedPosition: position created by StrategyRule in historical simulation.

- SimulatedTrade: closed lifecycle of one or more simulated positions.

- CostModel: spread, commission, slippage, and other execution assumptions.

- StrategyEvaluation: EV, drawdown, trade-level MAE/MFE, return distribution, and other performance measures.

- Strategy simulation is downstream of pattern detection and must not be required for pattern research.

## 8. Live Analysis

- LiveRun: processes incoming bars through the same MarketState and Detector runtime used by replay.

- LiveEvent: DetectorEvent generated during LiveRun; structurally equivalent to replay events.

- Alert: optional downstream notification derived from selected LiveEvents.

- AlertPolicy: controls thresholding, throttling, cooldown, routing, and session restrictions.

- Alert generation must not alter detector state or research semantics.

## 9. Versioning and Lineage

- DetectionAnalysisConfig: fully resolved immutable configuration containing only settings that can change MarketState, PatternInstance or DetectorEvent output. Replay/live detection and the detector stage of EvaluationRun use this same contract.

- DetectionConfigHash: immutable fingerprint of resolved DetectionAnalysisConfig. EvaluationPlan is the separate immutable downstream research configuration; EvaluationPlanHash fingerprints it and includes its DetectionConfigHash reference.

- SourceDatasetId: identifies historical input data and revision where possible.

- CodeVersion: implementation revision associated with an analytical run.

- Every persisted detection artifact should be traceable to Instrument, timeframe, source dataset/feed, PatternId, PatternVersion, DetectionAnalysisConfig/DetectionConfigHash, CodeVersion and run identity. Evaluation artifacts additionally preserve EvaluationPlan/EvaluationPlanHash, context schema and OutcomeDefinition lineage.

## 10. Key Relationships

- DataSource provides Bars.

- Bars advance the SimulationClock or LiveRun.

- Bars produce MarketState primitives.

- MarketState is consumed by Detectors.

- Detectors instantiate PatternInstances and emit DetectorEvents.

- DetectorEvents are rendered by the chart and consumed by EvaluationRuns.

- EvaluationRuns capture DetectionTimeContextSnapshots, form EventSets, produce OutcomeObservations under an EvaluationPlan, and derive EventStudyAggregates.

- Optional StrategyRules consume eligible DetectorEvents and create SimulatedTrades.

- Optional AlertPolicies consume eligible live DetectorEvents and create Alerts.

## 11. Terminology Rules

- Pattern: observable market-behaviour definition; it does not imply profitability.

- Signal: avoid as a generic synonym for pattern; reserve it for a decision-oriented downstream event if needed.

- Detection: identification using information observable at detection_time.

- Label: retrospective classification that may use future information; labels must never be confused with real-time detections.

- Context: surrounding market state that conditions interpretation but does not necessarily trigger the pattern.

- Outcome: what happened after a detected event.

- Strategy: explicit trading rules applied to selected events.

- Replay: bar-by-bar historical simulation using an information boundary identical to live processing.

Approved MVP domain reconciliation — 2026-09-24

DatasetRevision

- An immutable version of canonical normalized bars used by replay/evaluation. Once referenced by a run, its content never changes; corrected provider data creates a new revision.

- A revision carries dataset-format version, canonical content checksum, source checksum/provenance, provider request metadata, retrieval time, normalization version, instrument/timeframe/range, and validation state.

- Canonical normalized minute bars are stored as Parquet; PostgreSQL stores revision metadata, lineage and analytical records.

ConfigurationPresetRevision

- A named instrument-specific configuration preset is versioned. Saving changed settings creates a new immutable preset revision; runs retain their exact resolved configuration and preset revision.

- Browser configuration authoring is limited to registered schema fields and server-side validation; browser-authored formulas are outside MVP.

EvaluationJob and queue

- Autonomous evaluation is represented by a durable job with queued/running/completed/cancelled/failed state, pinned dataset/configuration/build lineage and heartbeat/lease state.

- Multiple evaluation jobs may be scheduled from the UI, but exactly one evaluation executes at a time. Queue order is durable in PostgreSQL. Parallel evaluation execution is post-MVP.

- Manual stop produces CANCELLED and preserves incomplete output. Stale heartbeat recovery produces FAILED; automatic checkpoint resume is post-MVP.

Run identity and calendars

- Replay/evaluation lineage includes dataset revision, DetectionAnalysisConfig/DetectionConfigHash, EvaluationPlan/EvaluationPlanHash where applicable, detector versions, configuration-schema version, calendar version and app Git/build identity.

- Bar, event and job timestamps are UTC instants. Session/trading-day derivation uses versioned instrument/account-specific IANA timezone calendars, including scheduled breaks and holiday exceptions.

TrendLeg authority

- EMA-cross segment and TrendLeg are distinct concepts. EMA-cross segment is a raw interval between opposing completed-bar EMA crossings.

- TrendLeg is structurally established and protected: UP from confirmed higher-low then higher-high, DOWN from confirmed lower-high then lower-low. It ends only on a confirmed close-break of its protected opposite swing; EMA crossing or wick-only breach does not end it.

- Protected swing advances only after the corresponding corrective swing and subsequent directional swing are confirmed, without backdating. Equal-valued swings do not establish or advance a leg.

- Structural establishment is distinct from qualification. The provisional MVP qualification gates remain configurable at 30 completed one-minute bars and 70 points of directional movement. Once earned, qualification persists until structural termination.

Repo reconciliation update — 2026-09-26

TrendLeg retracement evidence

- A TrendLeg records its furthest favorable completed-bar price extreme: highest high for UP and lowest low for DOWN.

- Close-to-extreme retracement depth is recorded in points: UP = highest high minus current close; DOWN = current close minus lowest low.

- When a detector uses the provisional 35-point retracement filter, the candidate captures the source leg's furthest favorable extreme reached before the retracement trigger as its reference. The reference is neither the EMA-cross price nor the protected swing.

- The 35-point filter is disabled by default. Its detector-specific action belongs to that detector's approved rule table; it never changes structural TrendLeg validity or revokes qualification already earned.

Configuration parameter contract

- PatternDefinition/configuration schemas expose only parameters declared by the registered implementation. Optional detector filters such as the 35-point retracement rule exist only for detectors whose versioned rule table defines their effect.
