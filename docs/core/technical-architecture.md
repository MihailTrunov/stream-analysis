<!-- Migrated from Google Docs on 2026-09-27. Historical source document ID: 1RIGUQQHJCGvepXnlZc4Pm7fuoDiAR-REWVAgZZQwFGA. Source revision at migration: ANLCKQmosOxOeFYa3YYLYx1p6bP9pwM0K4OuBDZ6lUJktKNmNxXMZA-9zT3TwukZeOe8-HCsrAcwS0LdVB--NCusVmaM0lhkbbBHc29oMNU. -->

# Technical Architecture & Engineering Principles — Research & Replay MVP (Draft)

## Document Status

Status: Approved MVP implementation baseline, reconciled 2026-09-24 with the implementation-readiness decisions recorded in the stream-analysis repository. Repository/CI host is GitHub: MihailTrunov/stream-analysis. This document must be read consistently with Product Scope, Core Domain Model, and MVP Scope & Boundaries.

Purpose: convert the established product/domain constraints into a concrete software architecture, technology baseline, module ownership model, storage model, interface boundaries, test strategy, and repository layout.

Authority: this document is the canonical technical-architecture reference for the Research & Replay MVP. Jira Stories remain the immediate implementation contracts and must restate implementation-critical behavior and acceptance criteria.

Source alignment: the architecture preserves the established principles of one analytical runtime across replay/batch/live, strict no-look-ahead semantics, deterministic replay, explicit event_time versus detection_time, versioned detector/configuration lineage, and separation of detection from outcome evaluation and strategy simulation.

## Architecture Goals

Make deterministic analytical behavior the primary architectural constraint.

Allow the same market-state and detector code to run in historical replay, autonomous batch research, and later live mode without duplicated implementations.

Keep provider, persistence, API, UI, and export concerns outside the analytical core.

Make every persisted analytical result reproducible from dataset identity, resolved configuration, detector version, and code revision.

Make the implementation straightforward for a small codebase and AI coding agents: explicit package ownership, narrow interfaces, typed contracts, small fixtures, and conventional test commands.

Prefer a modular monolith over distributed services for the MVP.

## Explicit Non-Goals

No microservice decomposition, Kafka, Redis, distributed event bus, service mesh, or production high-availability architecture.

No brokerage order execution or order-management architecture.

No predictive ML platform or model-serving layer.

No large-universe ingestion architecture.

No generic plugin framework or end-user detector DSL in the MVP.

No requirement to preserve the current exploratory script structure as production architecture.

## Technology Baseline

Language/runtime: Python 3.13 for backend, analytical runtime, persistence, adapters, batch research, and tests.

Python packaging: a single pyproject.toml-based project using a src/ layout. Dependency and virtual-environment management should use uv unless the repository already has an approved equivalent before implementation begins.

Domain/config validation: Pydantic v2 models at external/application boundaries. Core analytical types may use frozen dataclasses where lower overhead and simpler semantics are preferable.

Database: PostgreSQL 16 is the MVP transactional relational store for instruments, dataset-revision metadata/checksums/provenance, jobs/queue state, configurations, runs, detector events, outcomes, annotations, and aggregates. Canonical normalized minute bars live in immutable versioned Parquet dataset revisions under the local data root.

ORM/data access: SQLAlchemy 2.x with explicit repository classes/functions. Domain and analytical modules must not issue SQL directly.

Database migrations: Alembic. All schema changes are version-controlled migrations; application startup must not mutate schema implicitly.

API: FastAPI. The API is an application boundary over use cases and repositories; it must not contain analytical formulas.

Front end: React + TypeScript + Vite. Chart rendering should use TradingView Lightweight Charts or an equivalently lightweight open-source candlestick library selected once during UI bootstrap; analytical calculations remain server-side.

Backend tests: pytest. Front-end unit/component tests: Vitest. Browser-level replay/UI tests: Playwright.

Static quality: Ruff for Python lint/format, mypy or Pyright for type checking, ESLint/TypeScript compiler for front-end type/lint checks.

Local MVP environment: Docker Compose is the supported startup/runtime topology for PostgreSQL, API, autonomous-evaluation worker, import worker, and browser UI on the researcher's Mac.

CI baseline: one repository CI workflow that installs dependencies, runs Python lint/type checks/tests, runs front-end checks/tests, and runs selected integration tests against PostgreSQL.

## Repository and Package Layout

The repository should become a modular monolith with one backend package and one front-end application.

Proposed top-level structure:

pyproject.toml

src/market_analysis/domain — canonical domain types and value objects with no infrastructure imports.

src/market_analysis/config — DetectionAnalysisConfig, EvaluationPlan, canonical serialization, DetectionConfigHash/EvaluationPlanHash, and typed detector/evaluation configuration schemas.

src/market_analysis/market_data — DataSource interfaces, canonical normalization, dataset acquisition, validation, session/calendar support.

src/market_analysis/simulation — ReplayRun, SimulationClock, ReplayCursor, replay controls and run orchestration.

src/market_analysis/indicators — incremental EMA, ATR and other numeric primitives.

src/market_analysis/structure — TrendLeg, SwingPoint/SwingStructure, RangeState and MarketState assembly.

src/market_analysis/patterns — PatternDefinition, lifecycle, Detector interface, PatternInstance, concrete detector implementations.

src/market_analysis/evaluation — EvaluationRun, OutcomeDefinition/OutcomeObservation evaluation, segmentation, aggregates, research-dataset assembly and parity comparison.

src/market_analysis/persistence — SQLAlchemy models, repositories, unit-of-work/transaction helpers, migrations integration.

src/market_analysis/adapters — OANDA and later external-provider adapters.

src/market_analysis/api — FastAPI routes, request/response schemas and application services.

src/market_analysis/application — orchestration/use-case services that connect repositories to analytical runtime.

tests/unit — pure deterministic tests.

tests/fixtures — canonical hand-reviewed fixtures and expected outputs.

tests/integration — PostgreSQL/repository/adapter/application integration tests.

tests/regression — replay parity, no-look-ahead, detector lifecycle and golden-fixture regression tests.

web/ — React/TypeScript application and UI tests.

research/legacy/ — current exploratory scripts retained for reference only.

Import direction is inward: API/adapters/persistence/application may depend on domain and analytical modules; domain/indicators/structure/patterns must not import FastAPI, SQLAlchemy, React concerns, or provider SDK objects.

## Treatment of Existing Exploratory Scripts

Existing exploratory Python scripts are research evidence, not production modules.

Move or retain them under research/legacy/ with a short README describing their historical purpose.

Production modules must not import code from research/legacy/.

Useful formulas, assumptions, or sample datasets may be ported deliberately into canonical tests/fixtures after review; copying an exploratory implementation does not make its semantics authoritative.

Where an old script conflicts with approved Jira/domain rules, the approved specification wins.

## Core Execution Architecture

All historical and future live processing uses the same sequential analytical pipeline:

canonical Bar → SimulationClock/live bar boundary → incremental market-state components → immutable/read-only MarketState → registered Detectors → DetectorEvents.

Replay, batch, and later live modes differ only in how Bars are supplied and how quickly the pipeline advances.

The analytical pipeline is synchronous and deterministic. Async I/O may be used at API/provider boundaries, but indicator/structure/detector update methods must not depend on task scheduling, wall-clock timing, or external mutable state.

At replay index i, analytical code may observe only bars 0..i and state derived from them.

Outcome evaluators are downstream consumers of already-emitted DetectorEvents. They may inspect future bars only inside an explicit outcome window and can never participate in detector confirmation.

## Public Analytical Interfaces

HistoricalDataSource.get_bars(instrument, timeframe, start, end) returns canonical completed Bars plus source metadata. Provider-native models do not cross this boundary.

IncrementalComponent.update(bar, context) updates one component from one newly observable Bar. Components expose reset() and a deterministic snapshot/debug representation.

MarketStateAssembler.update(bar) returns the MarketState observable at that bar after all prerequisite primitives update in a fixed documented order.

Detector.update(market_state) returns zero or more immutable DetectorEvents and updates only its own PatternInstance state.

OutcomeDefinition.evaluate(event, context_snapshot, bar_sequence, context) returns an immutable typed OutcomeObservation; it does not mutate the event, context snapshot, or detector state.

Repository interfaces operate on domain/application records and isolate SQLAlchemy session details from analytical code.

Application services own transactions and orchestration. Domain objects do not open database sessions or call repositories.

## Persistence and Data Model

PostgreSQL is the canonical MVP store. Plain PostgreSQL tables and indexes are sufficient; TimescaleDB is not required.

Canonical bars are stored in a bars table keyed by instrument_id + timeframe + timestamp, with a unique constraint and ordered range index.

Dataset identity is separate from individual bars. A source_datasets record stores provider, instrument, timeframe, requested/actual range, canonical bar count, validation status, checksum algorithm/version, checksum, and acquisition metadata.

ReplayRun records reference source_dataset_id and the fully resolved DetectionAnalysisConfig/DetectionConfigHash. EvaluationRun records reference the same resolved detection configuration plus the resolved EvaluationPlan/EvaluationPlanHash.

DetectorEvents are immutable append-only analytical records. Corrections require a new run/version rather than in-place semantic mutation.

PatternInstances persist lifecycle identity/state and references to emitted events. Persisted database UUIDs are run-local identities, not cross-run equality keys.

Manual validation annotations are separate mutable/auditable records referencing immutable detector events/instances.

Event-study persistence stores immutable event-level OutcomeObservations, aggregate results, and optional DetectionTimeContextSnapshots with dataset/config/pattern/outcome-definition/code lineage.

MVP does not archive raw OANDA response payloads. Preserve normalized canonical bars plus immutable import provenance/checksums; raw-payload retention is post-MVP if a later audit requirement justifies it.

Transactions are explicit at application-service boundaries. A failed run must not be marked complete when only partial analytical output has been persisted.

## Configuration and ConfigHash

DetectionAnalysisConfig and EvaluationPlan are separate immutable, versioned Pydantic schemas. Each has an explicit schema_version and is persisted with the run that owns it.

A run always stores fully resolved configuration: defaults are expanded before hashing and execution. ReplayRun stores DetectionAnalysisConfig; EvaluationRun stores DetectionAnalysisConfig plus EvaluationPlan.

DetectionAnalysisConfig contains only settings that can change MarketState, PatternInstance or DetectorEvent output: market-state parameters/versions, detector IDs/versions/parameters, and analytical session/calendar settings. EvaluationPlan references DetectionConfigHash and contains context-capture policy/schema, OutcomeDefinitions, SegmentDefinitions and other downstream evaluation settings. UI-only preferences are excluded from both.

Canonical serialization for hashing is UTF-8 JSON with recursively sorted object keys, no insignificant whitespace, enum values serialized as their stable string values, booleans/null in JSON form, timestamps normalized to UTC ISO-8601 with explicit Z, and no binary floating-point formatting dependence for values that require exact decimal semantics.

Decimal/price/configuration values requiring exact representation are serialized as normalized decimal strings under their typed schema rather than arbitrary Python float repr.

DetectionConfigHash is lowercase SHA-256 over `detection-config-v1\n` plus canonical DetectionAnalysisConfig JSON bytes. EvaluationPlanHash is lowercase SHA-256 over `evaluation-plan-v1\n` plus canonical EvaluationPlan JSON bytes.

Any detection-behavior field, component/detector version, or detector parameter change changes DetectionConfigHash. Outcome, segmentation or context-capture changes leave DetectionConfigHash unchanged and change EvaluationPlanHash when the resolved evaluation plan changes. Presentation-only settings affect neither hash.

The canonical JSON payload and hash algorithm/version must be persisted or reproducible from the stored resolved config.

Compatibility terminology: older Story/document text written before the configuration split may use unqualified `AnalysisConfig` or `ConfigHash`. In a MarketState, detector, PatternInstance or DetectorEvent context these terms mean `DetectionAnalysisConfig` and `DetectionConfigHash`. Evaluation-only settings always belong to `EvaluationPlan` / `EvaluationPlanHash`. New implementation code, schemas, fixtures and documentation must use the explicit names.

## Dataset Checksum

Dataset checksum is computed from canonical bar content in strict chronological order after normalization.

The checksum input must include stable instrument identity, timeframe, timestamp, OHLC values, optional volume representation, and canonical quality/completion fields that affect analysis.

Use SHA-256 with an explicit checksum format/version prefix such as `bar-sequence-v1`.

Provider response ordering, JSON formatting, pagination, or storage row order must not affect the checksum.

## Time and Numeric Conventions

Canonical timestamps are timezone-aware UTC instants. Database storage uses PostgreSQL timestamptz. API serialization uses ISO-8601 UTC with Z.

Session/local-time derivation uses IANA timezone identifiers and configured SessionCalendar rules; daylight-saving behavior is never implemented with fixed UTC offsets.

event_time identifies when the underlying market event occurred. detection_time identifies the earliest instant the runtime could know it. Visibility to downstream detectors/UI is governed by detection_time.

Prices and price-derived persisted values should use Decimal-compatible database numeric types where exact point/tick semantics matter. Indicator calculations may use IEEE-754 float where the Story defines an accepted numeric tolerance and deterministic reference fixtures.

Bar interval boundary semantics must be defined once in market_data and reused by adapters, persistence queries, replay and evaluation.

## Pattern and Detector Runtime

PatternDefinition is immutable and versioned. Pattern behavior changes require a new PatternVersion.

The lifecycle framework supports INACTIVE/INELIGIBLE where used, ELIGIBLE, CANDIDATE, FORMING, CONFIRMED, ACTIVE, COMPLETED, INVALIDATED and EXPIRED, plus explicitly declared detector-specific intermediate states such as RECLAIMED. A PatternDefinition declares the exact subset and deterministic transition table it uses; the framework does not force every detector through every state.

Detector registration is explicit Python registration/configuration, not dynamic plugin discovery in the MVP.

Detector execution order is deterministic. Detectors receive the same read-only MarketState and cannot mutate shared state or each other.

Detector rationale is structured evidence captured at transition time: condition identifier, pass/fail, measured value, threshold/reference and supporting features.

Exact v1 formulas and transition tables for TrendLeg, Swing, RangeState, reversal, compression and continuation remain detector/Story-level specifications; this architecture defines where they live and how they execute, not their market definitions.

## Replay and Batch Orchestration

ReplayRun owns one dataset, one resolved DetectionAnalysisConfig/DetectionConfigHash and one ReplayCursor/SimulationClock.

Seeking is semantically equivalent to replaying to the target from a valid origin. Snapshot/restore may be introduced only as an optimization with parity tests proving equivalence.

Playback speed is a UI/orchestration concern and cannot alter analytical output.

EvaluationRun invokes the same bar-by-bar detector pipeline under the same DetectionAnalysisConfig/DetectionConfigHash, captures immutable DetectionTimeContextSnapshots, applies EventSet eligibility/filtering, then evaluates configured OutcomeDefinitions from the EvaluationPlan after detection.

Batch processing should initially run in-process. Multiprocessing/distributed execution is deferred until correctness is established.

## Cross-Run Identity and Parity Semantics

Database UUIDs for ReplayRun, EvaluationRun, PatternInstance and DetectorEvent are unique persistence identities and are not expected to match across independent reruns.

Parity tests compare semantic output, not UUID equality.

DetectorEvent semantic comparison consists of: pattern_id, pattern_version, instrument_id, transition old/new state, event_time, detection_time, deterministic within-bar event ordinal, normalized rationale/evidence, and relevant direction/context fields.

PatternInstance semantic comparison consists of: pattern_id, pattern_version, instrument_id, lifecycle transition sequence and its event/detection times, plus deterministic occurrence ordinal derived from emission order for otherwise identical same-bar instances.

Run IDs, database primary keys, created_at timestamps and other operational metadata are excluded from semantic parity.

Parity mismatch reporting must identify the first divergent bar/event and show the normalized semantic records being compared.

## Outcome Observation & Evaluation Architecture

Outcome evaluation is a first-class analytical layer downstream of DetectorEvents. It answers what happened after an observable event without changing what the detector observed or how the detector was defined.

The canonical flow is: DetectorEvent → DetectionTimeContextSnapshot → OutcomeObservation(s) → aggregate research statistics → optional future predictive models.

Detector execution and outcome evaluation are strictly separated. A detector may use only information observable at detection_time. Outcome evaluation begins only after the DetectorEvent exists and may inspect later bars solely within the declared observation window.

Outcome information must never feed back into the detector execution that produced the event. Re-running the detector over the same observable history must therefore be independent of any stored or newly calculated outcomes.

The architecture distinguishes three concepts: DetectorEvent = what became observable; OutcomeObservation = what subsequently happened; Prediction = an estimate, made from detection-time-observable inputs, of what may happen afterwards.

An observable event should be named for what is known at detection time rather than for a future interpretation. For example, DOWN_LEG_CLOSE_ABOVE_EMA45 is an observable event; BULLISH_REVERSAL is an outcome/higher-level interpretation unless its confirmation conditions are themselves already observable.

## Detection-Time Context Snapshots

Each research-worthy DetectorEvent may be associated with an immutable DetectionTimeContextSnapshot containing the market information that was observable when the event was detected.

The snapshot is the canonical feature-side record for later conditional analysis and possible supervised prediction. It must not contain values derived from bars after detection_time.

The snapshot should reference, rather than ambiguously reconstruct, the MarketState and relevant component outputs used by the detector: instrument/session, detector/pattern identity, TrendLeg state, EMA/ATR values, swing/structure state, RangeState, volatility/session context, and selected detector evidence.

Snapshot content is versioned through a context_schema_version. Adding or changing feature semantics requires an explicit schema/version decision.

Snapshots should store stable analytical values or normalized structured evidence, not arbitrary UI state or raw Python object serialization.

A snapshot may include fields not used by the triggering detector if they were observable at detection_time and are intentionally approved as research context. This allows later conditional analysis without contaminating the event definition.

Future predictive datasets use X = DetectionTimeContextSnapshot and Y = one or more OutcomeObservations. Outcome-side data is never copied into X.

## OutcomeDefinition Contract

OutcomeDefinition is the parent abstraction for all post-event observations. OutcomeMetric is a subtype/category for numeric outcomes rather than the only supported form.

Every OutcomeDefinition has a stable outcome_id, outcome_version, typed configuration, output_type, reference_mode, observation-window semantics, eligibility rules, result-status semantics, and lineage.

Supported MVP output types are: NUMERIC, BOOLEAN, CATEGORICAL, and EVENT_REFERENCE. Structured compound results may be introduced only when one of these forms cannot represent the research question without loss.

reference_mode defaults to FROM_DETECTION_TIME. FROM_EVENT_TIME may be supported for explicitly retrospective/descriptive research but must be labelled as such because event_time may precede observability.

Every definition specifies its reference price where applicable, direction convention, normalization, observation-window start/end inclusivity, session-boundary policy, required future data, and units.

A result is an immutable OutcomeObservation linked to the source DetectorEvent and the exact OutcomeDefinition version/configuration that produced it.

Missing or unavailable results are represented with explicit statuses such as AVAILABLE, INSUFFICIENT_DATA, NOT_APPLICABLE, or AMBIGUOUS; they are never encoded as zero.

Semantic identity for an OutcomeObservation is derived from the source event semantic key plus outcome_id, outcome_version, and normalized outcome configuration, not from database UUID equality.

## Outcome Result Types and Ambiguity Semantics

Numeric outcomes include forward return, MFE, MAE, bars-to-threshold, ATR-normalized excursion, or other scalar measurements.

Boolean outcomes answer questions such as whether a bullish structure break occurred within N bars or whether a range formed within the declared window.

Categorical outcomes include values such as next TrendLeg direction, next confirmed swing classification, or one of several mutually defined market-state outcomes.

Event-reference outcomes point to a later canonical analytical event, such as the next confirmed SwingPoint, first structure break, next TrendLeg transition, or next detector event of a specified family.

Event-reference outcomes should store the referenced semantic identity/time plus the elapsed bars/time from the source event where relevant.

OHLC data does not reveal intrabar path. If both positive and negative thresholds are touched within the same bar and ordering cannot be inferred, the result must be AMBIGUOUS or a specific SAME_BAR_AMBIGUOUS category rather than inventing an order.

Structural outcomes must consume the same confirmed/versioned structural events produced by the analytical runtime rather than recomputing a retrospective alternative solely for outcome scoring.

Outcome windows that cross the end of a dataset or an enforced session boundary produce INSUFFICIENT_DATA unless the OutcomeDefinition explicitly permits truncation.

## Initial MVP Outcome Families

Forward return: configurable horizons such as +5, +15, +30 and +60 bars, with exact reference-price and direction semantics defined in the corresponding Story.

Excursion: MFE and MAE over configurable horizons, with optional ATR normalization using only the ATR value observable at the chosen reference time.

Threshold sequence: time/bars to configured positive and negative thresholds, including explicit same-bar ambiguity handling.

Structural progression: next confirmed swing, first structure break, next TrendLeg direction/transition, and selected state transitions where the prerequisite primitive exists.

State formation: boolean/categorical observations such as range formed, compression resolved, or no qualifying structural transition within the observation window where these are explicitly defined.

Every aggregate must report eligible event count, AVAILABLE count, INSUFFICIENT_DATA count, NOT_APPLICABLE count and AMBIGUOUS count where relevant.

## Research Aggregation and Conditional Analysis

Event-level OutcomeObservations are the source research records. Aggregate statistics are derived views and must not replace or mutate them.

Aggregate outputs may include count, mean, median, standard deviation, quantiles, positive-rate, threshold-hit rate, categorical distribution, and event-transition frequencies as appropriate to the output type.

Aggregation always preserves sample size and missing/ambiguous counts. A detector with 200 events and 37 valid +60-bar observations must not present N=200 for that metric.

Segmentation conditions are drawn from DetectionTimeContextSnapshot or other information observable at detection_time: instrument, session, volatility state, preceding TrendLeg properties, swing structure, range score, detector version and approved context fields.

Conditional research asks how the distribution of Y changes given subsets of X; it does not modify the original detector event or retroactively change its label.

## Research Dataset and Future Prediction Boundary

The persisted research dataset conceptually contains: source DetectorEvent, DetectionTimeContextSnapshot, one or more OutcomeObservations, and complete dataset/config/detector/outcome/code lineage.

This structure is intentionally suitable for future prediction while remaining useful without machine learning.

Future predictive models may estimate P(Y | X) or another outcome distribution using only fields available in DetectionTimeContextSnapshot at prediction time.

Predictive model outputs are separate analytical artifacts with their own model/version/training lineage. They do not redefine DetectorEvent truth, PatternDefinition semantics, or historical OutcomeObservations.

Model training, feature selection, optimization, validation and serving remain post-MVP. The MVP requirement is to preserve leakage-safe X/Y data so later prediction is technically possible without redesigning the event/outcome boundary.

Any later predictive workflow must enforce temporal train/validation/test separation and must not use outcome-derived features or future-observable fields as model inputs.

## API Boundary

FastAPI exposes application use cases, not raw database tables.

Initial API capabilities: list instruments/datasets/configurations; create/read/control ReplayRun; stream or poll observable replay state/events; create/read EvaluationRun; retrieve DetectorEvents, DetectionTimeContextSnapshots, OutcomeObservations and aggregate event-study results; create/update manual validation annotations.

API response models use explicit versioned schemas where persistence/replay clients depend on stable structure.

The API never allows a replay client to request future analytical state beyond the current cursor as part of an active replay.

Autonomous evaluations run as durable PostgreSQL-backed queued jobs in a dedicated local worker. The browser may schedule multiple evaluations; exactly one evaluation executes at a time in queue order.

## UI Boundary

React/TypeScript is a presentation client over API/runtime outputs.

The UI renders canonical bars, MarketState overlays, PatternInstance/DetectorEvent lifecycle and rationale. It does not calculate EMA, ATR, swings, range scores, detector rules or outcomes.

During progressive replay, the server/API must not expose future analytical records. The UI should not rely on merely hiding preloaded future detector results.

Event inspection reads the rationale persisted/emitted by the detector rather than reconstructing conditions in TypeScript.

Manual validation is stored as separate annotation data and cannot mutate DetectorEvents.

## Export Contract

Canonical machine-readable export for event-level and evaluation datasets is Apache Parquet.

CSV may be offered as a convenience export for aggregate/small tabular results but is not the canonical reproducibility format.

Export metadata must include or accompany source_dataset_id/checksum, DetectionConfigHash, EvaluationPlanHash where evaluation artifacts are exported, detector versions, context_schema_version, outcome-definition versions/configuration, code revision and run identity.

Export must preserve timestamps in UTC and document units; no locale-dependent formatting.

## Testing and Fixture Strategy

`pytest` from repository root is the canonical backend test entry point; CI may split suites but must preserve the same semantics.

Pure analytical tests are fast unit/regression tests and must not require PostgreSQL, network access or the UI.

Canonical fixtures live under tests/fixtures with checked-in input bars/configuration and hand-reviewed expected outputs. Expected values must not be regenerated automatically by the implementation under test.

Integration tests exercise Alembic migrations, PostgreSQL repositories, application transactions and API behavior.

Adapter tests use recorded/synthetic provider payloads and mocked transport; routine CI does not require live OANDA access.

Playwright tests cover the minimum replay flow: select session, progressive bars, control actions, event annotation/inspection and future-data visibility boundary.

Mandatory regression categories: deterministic repeated replay; step-vs-run parity; replay-vs-batch event parity; look-ahead leakage; event_time/detection_time visibility; detector lifecycle/version fixtures; DetectionConfigHash and EvaluationPlanHash golden fixtures; dataset checksum golden fixtures.

A Story changing analytical semantics must update/add explicit fixtures and, where behavior changes materially, update the detector/version rather than silently rewriting golden expectations.

## Error and Result Model

Expected domain/application failures use typed exceptions or result types with stable error codes at the API boundary.

Distinguish validation/configuration errors, unavailable data, provider/transient I/O failures, persistence failures, invalid replay operations and analytical invariant violations.

Analytical invariant violations should fail loudly and mark the run failed; they must not be downgraded to missing detector events.

Provider retry policy belongs in adapters. Analytical modules never retry or perform network I/O.

User-facing API errors must not expose provider credentials, SQL details or stack traces.

## Logging and Auditability

Use structured JSON logging on the backend.

Include run_id, dataset_id where available, instrument, component/detector identity and error code in relevant records.

Logs are diagnostic only; persisted domain events/results are the source of analytical truth.

Secrets and provider tokens must be redacted and must not appear in logs, exceptions returned to clients, fixtures or exported research data.

## Dependency Injection and Ownership

Use explicit constructor/function injection. A general dependency-injection framework is not required.

Application services construct/receive repositories, data sources, clocks, component registries and detectors.

Infrastructure implementations satisfy small interfaces owned by the consuming core/application module.

Cross-module calls should use typed domain/application contracts rather than importing ORM models as shared domain objects.

## Migrations and Schema Evolution

Alembic migrations are forward-versioned and reviewed with code.

Analytical records should favor append-only/versioned evolution over destructive rewriting.

Schema changes that alter serialized configuration, detector evidence, event semantics or export contracts require explicit schema/version migration decisions.

The MVP does not require automated migration of all historical exploratory outputs.

## Development Workflow for Codex

A coding task begins from one Jira Story plus its linked canonical Google Docs.

The Story is the immediate implementation contract; this architecture supplies shared technology/module conventions; Product Scope, Core Domain Model and MVP Scope define product/domain boundaries.

Codex should implement inside the module owned by the Story, add/update the required tests/fixtures, run the canonical checks, and avoid introducing new frameworks or cross-layer dependencies unless the Story explicitly requires an architecture change.

If a Story conflicts with this architecture or needs a new cross-cutting decision, update/approve the architecture decision before implementation rather than allowing an agent to invent a local convention.

## Architecture Decision Rules

Prefer the smallest design that preserves deterministic replay and future live reuse.

Prefer explicit typed contracts over implicit dictionaries and script-level globals.

Prefer immutable/versioned analytical records over mutable hidden state.

Prefer repository/application boundaries over direct database access from analytics.

Prefer one implementation with multiple execution modes over replay/batch/live forks.

Do not add infrastructure solely for anticipated future scale without an observed MVP need.

## Implementation Baseline Decisions

Approved MVP implementation stack: Python 3.13 + FastAPI + PostgreSQL 16 + SQLAlchemy 2.x/Alembic + React/TypeScript/Vite, with Nx as task runner and pinned uv/pnpm toolchains.

Initial replay-chart baseline: TradingView Lightweight Charts. A functionally equivalent lightweight library may replace it only through an explicit architecture decision before UI implementation.

Repository dependency/environment baseline: uv, unless an already-existing repository has a deliberate conflicting standard that is reviewed before implementation.

Local PostgreSQL baseline: Docker Compose for reproducible development/test setup. PostgreSQL semantics remain authoritative; developers may use an equivalent local PostgreSQL instance if integration tests target the same supported version/behavior.

Repository/CI host is GitHub repository MihailTrunov/stream-analysis. GitHub Actions runs the canonical lint/type/test/integration checks; performance benchmarks remain explicit task-runner targets rather than routine CI gates.

Exact analytical v1 formulas and metric conventions remain Story-level contracts. The current MVP analytical Stories contain provisional deterministic v1 definitions; material future semantic changes require explicit definition/version changes rather than silent fixture rewrites.

## Relationship to Canonical Project Documents

Product Scope — defines what the product is and is not.

Core Domain Model — defines canonical concepts, terminology and relationships.

MVP Scope & Boundaries — defines the release boundary and correctness expectations.

This document — defines the technical architecture and engineering conventions used to implement that MVP.

Jira Epics/Stories — define implementable capability and behavior contracts, including exact v1 formulas, acceptance criteria and dependencies.

Repository code/tests/migrations — are the executable implementation of the approved contracts.

Approved MVP reconciliation — 2026-09-24

The following decisions are authoritative for MVP implementation and supersede earlier conflicting wording in this document:

- Runtime/tooling: Python 3.13; pinned uv and pnpm toolchains; Nx is the repository task runner; GitHub Actions is the CI host for github.com/MihailTrunov/stream-analysis.

- Local topology: one supported Docker Compose startup launches PostgreSQL, API, autonomous-evaluation worker, import worker, and browser UI. API/UI bind to localhost by default; PostgreSQL remains Compose-internal. No MVP authentication, cloud hosting, Redis, Celery, distributed workers, or Nx Cloud.

- Data storage: immutable normalized one-minute dataset revisions are stored as versioned Parquet under one configurable Git-ignored local data root. PostgreSQL stores dataset manifests/checksums/provenance plus jobs, configurations, events, outcomes, annotations, and queue state. CSV is convenience export only.

- Dataset correctness: canonical timestamps are UTC; no synthetic gap fill; exact duplicate bars collapse deterministically; conflicting duplicates fail validation. Dataset revisions used by runs are immutable.

- Calendars: instrument/account mappings use versioned IANA timezone calendars with session boundaries, scheduled breaks, holidays, and exceptions. Analytical boundaries never depend on the Mac/browser local clock.

- Shared deterministic pipeline: live walkthrough and autonomous evaluation use the same completed-bar MarketState/detector pipeline. Intermediate per-bar state is normally recomputed rather than persisted.

- TrendLeg: the structural protected-swing lifecycle documented in the Core Domain Model/approved project context is authoritative. EMA-cross segments are separate raw intervals; an opposing EMA cross does not itself end a TrendLeg.

- Configuration: browser editing is schema-driven and server-validated. Named presets are immutable revisions with stale-write rejection. Runs pin resolved configuration, schema version, dataset revision, detector versions, calendar version, and app Git/build identity.

- Evaluation scheduling: the browser may queue multiple autonomous evaluation runs. Exactly one evaluation executes at a time in queue order. The durable queue is PostgreSQL-backed with transactional claim/heartbeat semantics. Parallel evaluation execution is post-MVP.

- Evaluation recovery: a manual stop records CANCELLED and preserves incomplete output; stale-heartbeat recovery records FAILED. Automatic checkpoint resume is post-MVP.

- UI/data access: chart APIs are visible-range/viewport based; deterministic display-only OHLC aggregation may be used for overly wide views, while all analysis remains on canonical one-minute bars.

- Operations: MVP includes local diagnostics, bounded log rotation, manual backup/restore, a seeded non-research demo dataset/preset, and lineage-aware exports.

- Build lineage: every replay, evaluation and export records app Git/build identity in addition to dataset/config/detector lineage. Durable autonomous evaluations require a committed build identity.

Repo reconciliation update — 2026-09-26

Configuration-schema contract

- Every registered detector schema exposes an enable/disable control, detector code version, and only parameters actually declared by that implementation. Do not create universal detector fields without semantic meaning for that detector.

- Shared editable groups include EMA and ATR periods, SwingPoint confirmation widths, SwingStructure hysteresis, TrendLeg qualification bars/points, and outcome horizons.

- Optional filters such as the provisional 35-point retracement rule appear only for detector versions whose approved rule table defines their effect and are disabled by default. Protected-swing termination and detector formulas remain code-defined, not browser-editable.

Warm-up computation contract

- Every enabled indicator, structural component and detector declares its warm-up requirement through the versioned configuration schema.

- The provisional MVP base requirement is 5 × period for EMA, 5 × period + 1 completed bars for ATR, and the largest declared historical lookback for other enabled components.

- The launch minimum is the maximum base requirement plus 2 × (left + right + 1) completed bars when SwingPoint confirmation widths are enabled; otherwise the structural buffer is zero.

- The API computes and exposes the minimum before launch and rejects a lower requested value. This policy initializes sufficient history for the configured components but does not guarantee that structural state has formed.

- Warm-up-policy changes are versioned and affect only new configuration snapshots/runs; historical runs retain their original resolved policy and lineage.

TrendLeg evidence contract

- TrendLeg state exposes the furthest favorable completed-bar extreme and close-to-extreme retracement depth in points as reproducible analytical evidence.

- A detector using the provisional 35-point filter freezes the source leg's furthest favorable extreme reached before the retracement trigger as its candidate reference. This reference is neither the EMA-cross price nor the protected swing.

- Detector-specific use of retracement evidence belongs to the detector rule table and cannot alter structural TrendLeg validity or revoke qualification once earned.

Implementation-time choices

- Exact provider trading-day boundaries, IANA-zone mappings, scheduled breaks, holiday exceptions and DST fixtures are verified when calendar/import/outcome work begins.

- The chart viewport bar cap plus display-aggregation bucket size/alignment/partial-bucket rules are chosen from measured supported-browser performance during chart implementation; analytical computation remains one-minute.

- Benchmark detector/configuration/outcome versions, dataset revision and reference-Mac hardware are pinned when the benchmark is implemented. These choices do not block unrelated architecture or MVP work.
