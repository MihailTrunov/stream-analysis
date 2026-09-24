# Market Analysis Research and Replay

A research environment for defining observable market behaviour, replaying historical data, inspecting detector evidence, and evaluating subsequent outcomes.

## Language

**EMA-cross segment**: A raw directional interval between opposing completed-bar EMA crossings, distinct from a TrendLeg.

**TrendLeg**: A directional market move that can survive pullbacks across EMA and ends on a confirmed close-break of its protected opposite swing.

**Protected swing**: The confirmed opposite-side swing reference whose close-break terminates a TrendLeg.

**PatternDefinition**: A versioned definition of observable market behaviour, with explicit rules and supported parameters.

**Detector parameter**: A supported setting of an existing PatternDefinition, such as the number of consecutive bars required to confirm compression.

**DetectionAnalysisConfig**: The resolved configuration of market-state components and detectors used by a replay or evaluation run.

**EvaluationPlan**: The downstream research configuration specifying context capture, outcomes and segmentation independently of detector behaviour.

**Configuration preset**: A named, reusable, instrument-specific configuration with revisions that preserve previously saved settings.

**Dataset revision**: An identifiable version of canonical historical bars whose content becomes immutable once used by a run.

**Warm-up history**: Historical bars before the selected interval, processed to initialize indicators, structural state, and detectors before visible replay and research event selection begin.

**Manual validation**: A researcher's judgment of whether a detection matches the intended market pattern using evidence available at detection time, independently of its subsequent outcome.

**Missed-pattern annotation**: A separate research record identifying an expected pattern on a chart interval within an exact dataset revision where the researcher considers a detection to be missing.

## Relationships

- A **PatternDefinition** declares its supported **detector parameters**.
- A **DetectionAnalysisConfig** records the selected detector versions and parameter values.
- An **EvaluationPlan** references the detection configuration whose events it evaluates.
- A **configuration preset** has revisions; a run retains the exact resolved configuration and preset revision it used.
- A run references an exact **dataset revision**; corrected data creates a new revision without changing the bars available to earlier runs.
- A run records the **warm-up history** it processed as part of its reproducible inputs.

## Confirmed MVP scope

- The browser must support parameter editing. Confirmed during the implementation-readiness interview.
- Configuration changes require stopping the current replay first; a paused replay is not sufficient. Do not apply edits to an active run or automatically restart it when parameters change.
- After editing configuration, the next replay starts as a new run from the beginning of the selected interval, with analytical state initialized for the new configuration; it does not resume at the stopped cursor.
- The browser must support launching batch evaluations and viewing their outcome statistics in the MVP.
- The browser must support a basic side-by-side comparison table for two evaluation runs showing parameter differences, event counts, outcome statistics, and valid sample sizes. Show a warning when the datasets or outcome definitions differ.
- Configurations can be saved as named, reusable presets. Saving edits to an existing preset creates a new revision; previous runs retain their original configuration.
- Historical datasets become immutable once used by a run. Provider corrections create a new dataset revision, while existing runs remain reproducible against the original prices.
- Replay processes a defined, recorded period of warm-up history before the selected interval. Visible replay and research event selection begin at the selected interval start, not at the warm-up start.
- If the dataset lacks the required warm-up history, block the run with a clear explanation and offer a later start time or importing more history; do not silently shorten warm-up.
- Calculate minimum warm-up from the selected indicators' and detectors' documented requirements. Show the required bar count before launch and allow the researcher to increase, but not reduce, it.
- Meeting the required warm-up history permits replay even if structural state is still unavailable. Detectors requiring that structure remain inactive and visibly marked as waiting for structure until their prerequisites become available; other detectors operate normally.
- Controlled comparisons use the same warm-up start for both runs, satisfying the larger configuration-dependent requirement. Comparing existing runs with different warm-up histories shows a warning without changing either run.
- Select research events by detection_time within the selected interval. Carry detector state across the interval start without resetting it: a pattern begun during warm-up can produce an included confirmation inside the interval, retaining its earlier history. Events detected entirely during warm-up remain outside the selected results.
- Outcome evaluation for selected events may extend beyond the selected interval end, within the declared observation horizon and session policy; event selection remains restricted to the selected interval. If required later bars are unavailable, report INSUFFICIENT_DATA rather than zero.
- For the intraday MVP, outcome windows may cross intraday session/time buckets but must not cross the configured trading-day boundary. A horizon requiring next-trading-day bars returns INSUFFICIENT_DATA rather than incorporating overnight moves.
- An unexpected missing bar inside a required outcome window makes that outcome INSUFFICIENT_DATA with a missing-bar reason. Do not synthesize a replacement candle or extend the window to collect additional available bars. Distinguish scheduled closures from unexpected gaps using the instrument's calendar.
- Unexpected gaps during warm-up or the selected event-detection interval block launch, with affected timestamps shown. The researcher must import corrected data or choose a clean interval; automatic gap reset/recovery is outside v1. Gaps confined to the later outcome-only window invalidate affected outcomes without blocking detection.
- The MVP browser editor exposes supported settings for indicators, structural components, detectors, and outcome horizons, with validation and documented defaults. New formulas or rule combinations require tested code/version changes rather than browser authoring.
- Track competing hypotheses independently: the same market movement may produce both continuation and reversal candidates. Show each candidate's lifecycle and evidence; each confirms, invalidates, or expires under its own rules, without selecting a winning interpretation or suppressing the other hypothesis.
- Default outcome statistics use one sample per pattern occurrence at its first confirmation, grouped by pattern and version. Candidate, reclaimed, invalidated, and expired lifecycle events remain inspectable but are not mixed into confirmation-based outcome samples; multiple transitions of one occurrence must not inflate the sample count.
- The MVP results table supports drilling from an aggregate into its underlying eligible events, including unavailable-outcome reasons, and opening an event's chart context and detection-time evidence without requiring an export.
- Completed evaluations have a clearly labelled Review mode showing subsequent bars within the outcome window alongside original detection-time evidence. Review is separate from progressive replay and does not change its cursor or analytical state; progressive replay continues to hide future bars.
- Closing the browser does not stop a background batch evaluation. If the backend stops during evaluation, mark the interrupted run failed and preserve its partial output as incomplete; offer a new run from the beginning with the same pinned inputs. Automatic evaluation checkpoint recovery is outside MVP scope.
- The routine evaluation workload target is one year of one-minute bars per instrument. Three initial detector implementations are not a platform limit; more detectors may be added through the versioned detector contracts.
- The first MVP delivery commits to reversal, compression, and continuation detectors. Additional code-defined, versioned detectors use the same framework; adding one to the delivery scope requires its own specification and test fixtures.
- The MVP evaluation turnaround target is at most five minutes per instrument for one year of one-minute data with the three initial detectors and default outcomes, measured from launch to persisted results and excluding data download. This is a target to validate on agreed reference hardware, not a measured performance claim.
- The researcher's current development Mac is the reference machine for the MVP evaluation benchmark; its exact hardware specifications must be recorded before measurement.
- MVP supports a UI-managed queue of autonomous evaluation runs. Researchers may schedule multiple evaluations from the browser; queued runs execute in sequence, with exactly one evaluation running at a time. Completed-result review remains available while the queue runs. Parallel evaluation execution is deferred.
- Retain all available OANDA trading hours in historical datasets rather than restricting acquisition to research sessions. Apply research-session filters when selecting events, preserving earlier history for analytical state and retaining the agreed trading-day cutoff for outcomes.
- US30 and DAX have separate configuration presets, even when initial parameter values match. Point-based thresholds are explicit per instrument; editing one instrument's preset does not change the other's settings.
- DAX research defaults were provisionally copied from the US30 30-bar / 70-point / 35-point baseline and are explicitly unvalidated for DAX. Subsequent structural-TrendLeg decisions retain the 30-bar and 70-point qualification gates; retracement is recorded evidence and an optional experiment filter rather than a default permanent qualification veto. Experiments use explicit preset revisions.
- Indicators, structural state, and active detector candidates carry across trading-day boundaries and scheduled market closures. Do not reset solely because a new day begins; candidates remain governed by their normal detector rules. The same-day cutoff limits outcome measurement, not analytical state continuity.
- For MVP, outcome trading-day boundaries follow OANDA's instrument-specific trading session, represented by a versioned, timezone-aware calendar rather than the browser's local midnight. Verify exact boundaries and holiday exceptions before implementation.
- Manual review labels express agreement with the intended market pattern, not favourable/unfavourable outcomes or profitability. A correct detection can have an unfavourable outcome; this clarifies the existing research intent rather than adding scope.
- MVP supports marking missed patterns directly on a chart interval, tied to the dataset revision, interval, and expected pattern. These are separate research annotations, not invented DetectorEvents, and do not enter detector-event counts.
- Missed-pattern annotations support manual review and export in MVP. Automatic annotation-to-detection matching and recall scoring are deferred; occasional annotations are not treated as an exhaustive reference set.
- Before implementing each detector, obtain the researcher's approval of its rule table and worked examples covering confirmation, invalidation, expiry, and simultaneous-condition precedence. Derive these from existing specifications and surface genuine ambiguities rather than reopening established principles.
- Separate raw EMA-cross segments from the longer-lived TrendLeg. An opposing EMA cross alone does not automatically end the TrendLeg; a pullback across EMA followed by continuation can belong to the same leg.
- End an UP TrendLeg on a confirmed close-break below its protected swing low; end a DOWN TrendLeg on a confirmed close-break above its protected swing high. EMA crossings or wick-only breaches do not terminate the leg.
- Advance an UP TrendLeg's protected low only after a confirmed higher low is followed by a confirmed higher high; symmetrically, advance a DOWN TrendLeg's protected high only after a confirmed lower high is followed by a confirmed lower low. Retain the previous protection until that subsequent directional swing is confirmed, without backdating; protection must never move backwards.
- Ending a TrendLeg does not automatically start a leg in the opposite direction. Allow a period with no active TrendLeg until a new leg meets its own establishment criteria; failure of the previous direction alone is insufficient.
- Establish an UP TrendLeg from a confirmed higher low followed by a confirmed higher high, using that higher low as its initial protected swing. Establish a DOWN TrendLeg from a confirmed lower high followed by a confirmed lower low, using that lower high as its initial protection. The leg becomes observable only when the second swing is confirmed; structural establishment is distinct from duration/magnitude qualification.
- Retain a minimum of 30 completed one-minute bars and 70 points of directional movement as configurable provisional TrendLeg qualification gates for reversal/continuation detector context. A structural leg may exist without qualifying; these gates do not replace its protected-swing termination rule. Measure duration from the initial protected swing's `event_time` and movement in the leg's direction from that swing's price to the current completed close. The leg qualifies on the first completed bar satisfying both gates; neither qualification nor its source metrics are backdated.
- Once a TrendLeg earns qualification, retain that qualification while it remains active, even if a subsequent pullback reduces its current directional movement below the qualification threshold. Do not remove established trend context merely because the leg retraces; protected-swing termination remains authoritative.
- Reversal and continuation candidates may be created only while their source TrendLeg is active and qualified. For reversal, that leg is the established directional context: a counter-trend condition may open a candidate before termination, and a protected-swing break may confirm or invalidate that already-open candidate while ending the source leg. A break cannot seed a new candidate from the leg it just ended.
- On a completed bar that both closes through the current protected swing and would otherwise allow a newer protected-swing update, evaluate the break first. The break ends the TrendLeg and discards the same-bar protection advance; a failed leg is not retroactively rescued by later structural processing of that bar.
- Once a TrendLeg ends, do not establish its opposite on the same completed bar, even if same-bar structural events could otherwise appear to satisfy the opposite pattern. Begin a fresh opposite sequence after termination: its first corrective swing and subsequent confirming directional swing must be observed after the termination.
- TrendLeg structural comparisons are strict: equal-valued swings are neither higher nor lower and therefore cannot establish or advance a TrendLeg. Existing protection remains in force across equal highs or lows.
- The 35-point retracement is an always-recorded TrendLeg evidence field and, when used, a detector-specific experimental eligibility filter. It never changes structural TrendLeg validity or revokes qualification already earned. Define its exact reference point and per-detector action in the experiment's configuration/rule table.
- For reversal v1, an opposing completed-bar EMA cross opens a reversal candidate only when its source TrendLeg is active and qualified (UP-to-below for bearish; DOWN-to-above for bullish). The cross is early warning only; a confirmed close-break of the protected swing remains the reversal confirmation.
- Invalidate an open reversal candidate when a newly confirmed same-direction swing extreme appears in its source TrendLeg (for example, a confirmed higher high for a bearish candidate from an UP leg, with the mirror rule for bullish). A wick or unconfirmed extreme does not invalidate it.
- An unconfirmed reversal candidate expires after a configurable 60 completed bars measured from its triggering opposing EMA cross. Expiry is neither confirmation nor structural invalidation and emits no pattern event.
- After expiry or structural invalidation, a later opposing completed-bar EMA cross may open a new reversal candidate within the same still-active, qualified TrendLeg. Remaining on the opposing EMA side is not a new trigger; a new cross is required.
- Continuation v1 eligibility requires both an active, qualified TrendLeg and matching confirmed SwingStructure: UP with BULLISH for bullish continuation, and DOWN with BEARISH for bearish continuation. A disagreement is ineligible.
- At continuation-candidate creation, freeze the prior directional swing reference and opposite protected swing reference for the entire candidate lifetime. Do not advance either reference as newer swings appear; any dynamic-reference behavior is a new semantic version.
- A single opposing EMA cross may open independent reversal and continuation candidates when each detector's eligibility conditions hold. They are competing research hypotheses and do not suppress one another.

## Possible post-MVP scope

- MVP deployment is single-user and local to the researcher's Mac. A remotely operated service that ingests, computes, or streams live analysis without the local application running is explicitly post-MVP; keep boundaries suitable for it, but do not introduce cloud hosting, authentication, tenancy, or always-on operations into MVP delivery.
- MVP requires network access only to import OANDA history. Replays and evaluations operate entirely from locally stored, immutable dataset revisions and remain usable offline after import.
- MVP has one supported local startup command using Docker Compose. It launches the database, API, worker, and browser UI as a coherent stack rather than requiring separately managed manual processes.
- Autonomous evaluations run in a dedicated local worker service. The API creates and monitors the evaluation job; the worker performs the long-running work independently of the browser, preserving the path to later remote execution. Autonomous evaluation does not block the rest of the UI.
- While a pattern evaluation is in progress, the evaluated pattern/configuration cannot be changed into a new version; it must be stopped first. Autonomous evaluation submissions pin their dataset revision and configuration snapshot, and may be placed in a user-defined queue that runs one evaluation at a time in that order. The UI shows the active evaluation and the queue; it does not allow mutation of the active pattern into a new version.
- A live walkthrough is an interactive, bar-by-bar UI session. Only one live walkthrough may run in that UI at once; starting another is unavailable until the current walkthrough stops. This limitation does not make an autonomous evaluation block ordinary UI interaction.
- Autonomous evaluation must not block a concurrent bar-by-bar walkthrough over the smaller, several-hour interval the researcher is inspecting in the UI. Design the API/service boundary so interactive walkthrough work remains available while the background evaluation queue runs.
- The autonomous evaluator runs in bounded chunks at lower CPU priority, while interactive walkthrough state is served separately. This protects local UI/API responsiveness without changing deterministic evaluation results.
- The UI may reorder or remove queued evaluations that have not started. These queue operations never change the already-frozen dataset revision or configuration snapshot; running jobs remain immutable. MVP browser target is the latest Chrome on macOS; Safari and other browsers are outside the supported scope.
- Scheduled autonomous evaluations auto-run one after another by default. A user-initiated stop completes the current bounded chunk, marks that evaluation `CANCELLED` (not `FAILED`), preserves its partial output as incomplete, and disables queue auto-run until the user explicitly re-enables it. The user may manually start any scheduled evaluation; queue ordering and any skipped evaluations are their responsibility.
- The evaluation queue is persisted in PostgreSQL, not browser-local state. Queue order and frozen job snapshots remain visible after refresh or stack restart, although auto-run remains disabled after an unexpected restart.
- PostgreSQL's durable job table plus transactional job claiming implements the MVP evaluation/import queue. Do not add Redis, Celery, RabbitMQ, or another message broker; a later remote worker system may replace the implementation behind the same job interface.
- If the app/backend stops unexpectedly, mark the interrupted evaluation `FAILED` with partial output preserved as incomplete. Keep queue auto-run disabled after restart until the user explicitly re-enables it; the restarted stack must not unexpectedly begin queued work.
- When auto-run is disabled, manually starting a queued evaluation runs only that selected item and then leaves auto-run disabled. Re-enabling auto-run is the explicit action that resumes automatic progression through subsequent queued items.
- For MVP, each detector is an explicitly registered, versioned code implementation. Configuration may enable and parameterize registered detectors, but cannot load detector logic or formulas from the browser.
- The API enforces one active autonomous evaluation and one active import transactionally in PostgreSQL. UI disabled states reflect these server-side guarantees; stale tabs or direct requests cannot start conflicting work.
- Imports automatically retry transient network/provider failures with bounded exponential backoff and visible attempts. Once the limit is reached, the job fails cleanly without publishing a dataset revision.
- The first architecture-proving vertical slice uses small seeded canonical bar fixtures, not OANDA import. The first usable user-facing alpha follows with OANDA import for real research datasets; automated tests continue using local fixtures.
- Autonomous evaluation reads long datasets incrementally in bounded chunks, retaining only active analytical state and the rolling look-ahead needed for outcome horizons. It does not load the full dataset or every intermediate state into memory.
- Each MVP evaluation processes bars in one deterministic order and gives every registered detector the same immutable per-bar MarketState. Optimize measured bottlenecks before adding parallel detector execution; event ordering and replay/evaluation parity take precedence.
- Live walkthrough and autonomous evaluation invoke the same deterministic analytical pipeline. They differ only in driver, pace, and persistence; they never use separate EMA, structural-state, or detector implementations.
- A live walkthrough is an ephemeral browser session, not a persisted background job. Persist selected range/cursor in browser-local state for refresh recovery, then recompute from immutable dataset and configuration rather than maintaining a second durable walkthrough lifecycle.
- The MVP browser observes evaluation status and progress by polling the API. Do not add WebSockets or server-sent events for the single-user local evaluation workflow.
- OANDA credentials exist only in the local `.env` / Docker Compose environment. The browser never accepts or stores them, and PostgreSQL never persists them. Centralized secret provisioning is post-MVP.
- MVP provides documented manual backup and restore commands. Backup creates a timestamped portable archive containing a consistent PostgreSQL dump, immutable dataset revisions and checksums, evaluation artifacts/exports, and a versioned manifest. It excludes `.env`, credentials, source code, images, and local environments; restore verifies checksums before data becomes usable. Automatic cloud backup is post-MVP.
- Alembic migrations are explicit, named, version-controlled scripts reviewed with the persistence-model change they support; Alembic never decides or invents mutations. Generated diffs are drafts requiring human review. Every migration is tested on an empty database and on a restored populated backup; irreversible or data-transforming migrations require that restored-backup test before release. Startup applies only these already-approved migrations in order.
- Every saved configuration preset and immutable run snapshot carries an explicit configuration-schema version and calendar version. Future code reads prior versions through compatibility adapters; historical snapshots are never rewritten, and editing produces a new preset revision/configuration snapshot.
- The MVP API is an internal, typed, documented, and tested contract between the local React UI and local Python services. It has no public-client compatibility promise, external authentication surface, or formal public API versioning until post-MVP.
- The React client's API types are generated from FastAPI's OpenAPI schema. CI detects contract drift rather than allowing manually duplicated TypeScript models to silently diverge from Python request/response contracts.
- OANDA imports use the same observable, retryable background-job lifecycle as evaluations. Permit one import and one autonomous evaluation concurrently, each with its own visible UI status; imports cannot alter a dataset revision already pinned by an evaluation. Do not permit a second concurrent import.
- An import publishes a selectable immutable dataset revision only after complete fetch, normalization, gap validation, and checksum validation succeed. Failed or partial imports remain temporary worker state and are never selectable; retry begins a fresh revision attempt.
- MVP automated tests use deterministic local fixtures for domain logic and replay parity, API/integration tests against an isolated PostgreSQL instance, and a small Playwright Chromium browser smoke suite. OANDA responses are represented by recorded fixtures; live-provider checks are manual import verification rather than flaky automated tests.
- Routine CI uses GitHub-hosted `ubuntu-latest` runners, not self-hosted machines or charged larger/macOS runners. Keep artifacts small and short-lived, and do not upload historical datasets to GitHub Actions.
- Each pull request and direct push to `master` runs linting, type checks, unit tests, PostgreSQL integration tests, and browser smoke tests on the routine CI path. One-year performance benchmarks run manually or as explicit release benchmarks, not on every change.
- MVP uses structured local logs and persists a concise job failure summary plus relevant artifact paths. The UI exposes the actionable failure reason; full diagnostics are available through Docker Compose. Do not add hosted telemetry or monitoring services.
- MVP omits browser-based deletion of datasets, runs, and configurations. Preserve evidence lineage by default; disk cleanup is an explicit documented local maintenance operation performed only after backup.
- The API is authoritative for configuration normalization and validation before ConfigHash calculation and run creation. The browser provides immediate convenience validation only. Invalid requests create no run and return structured field-level errors; valid requests freeze the normalized, schema-versioned snapshot used for the hash.
- The API exposes registered configuration schemas—supported fields, defaults, allowed ranges, and cross-field constraints—so the browser renders parameter editors from the same contract rather than duplicating detector-specific form rules. This does not permit browser-defined formulas or logic.
- Each enabled indicator, structural component, and detector declares its own warm-up requirement. The API calculates the launch requirement as the maximum plus any declared structural-initialization buffer, then displays it before launch; no fragile global warm-up constant is used.
- The one-year-per-instrument performance target is a transparent manual benchmark on a named reference Mac. Record machine details, dataset revision, configuration, detector count, and conditions. Measure the acceptance run after a fresh worker/startup (cold-ish; OS caches cannot be perfectly reset) and report an immediate repeated warm run separately.
- The one-year performance check is an Nx benchmark target. It runs the fixed evaluation and writes a local result artifact containing elapsed time, machine/OS/Python metadata, peak memory where available, dataset/configuration/detector/code lineage, and cold/warm mode; it is manual or an explicit release check, not per-commit CI.
- MVP installation and updates use `git clone` / `git pull` followed by a local Docker Compose build. Do not publish Docker images or use a container registry; each local stack is tied directly to a reviewed Git commit. Nx is the project task runner and exposes the supported start, stop, backup, restore, test, and CI commands rather than a Makefile.
- The Nx workspace uses `pnpm` for Node/React dependencies and `uv` for Python dependencies. Pin both tool versions in the repository for reproducible local and CI installs.
- Nx caches only safe deterministic lint, type-check, unit-test, and build tasks. PostgreSQL integration tests, browser smoke tests, migrations, and performance benchmarks always execute rather than relying on cached results.
- MVP uses only local Nx cache plus standard GitHub Actions dependency caches. Do not add Nx Cloud or another remote task-cache service unless later build-time evidence justifies it.
- A fresh MVP install includes a small seeded demo dataset and preset so the local stack, walkthrough, and diagnostics can be verified offline before OANDA credentials are configured. Demo data is clearly non-research-grade.
- Python and frontend dependencies are lockfile-pinned. CI fails when declared dependencies and their lockfiles disagree, so local Compose builds and GitHub Actions resolve the same dependency graph.
- MVP targets Python 3.13. It is a mature supported release already present in the legacy environment; pin the container and tooling to it. This supersedes the older Python 3.12 reference in the architecture document, which is now security-fixes-only.
- The Compose stack supports both Apple-silicon and Intel Macs through multi-architecture base images. Avoid architecture-locked images and native dependencies unless necessary; routine CI remains on Linux.
- The stack starts and permits offline replay/evaluation without OANDA credentials. Only history import is unavailable until valid local credentials are supplied; credentials are not an application-start prerequisite.
- MVP includes a simple local diagnostics screen/API showing app and database-schema versions, database health, worker availability, OANDA-import availability without exposing secrets, and local artifact/storage paths. It is a supportability aid, not hosted monitoring.
- The browser requests bars, overlays, and events by visible time range/window from the API. Do not load a full year of one-minute history into the chart; replay/navigation scale with the viewport rather than total dataset size.
- When a chart viewport would contain more than an implementation-measured bar cap, the API returns deterministic display-only higher-timeframe OHLC aggregates. Drilling into a region returns canonical one-minute bars; analytical state, detector decisions, and event evidence always remain one-minute.
- The UI and API bind to localhost only by default, and PostgreSQL remains inaccessible outside the Docker Compose network. MVP has no authentication, so remote/LAN access is explicitly post-MVP.
- MVP retains canonical normalized bars plus immutable import provenance: provider, request parameters, retrieval time, normalization version, and source/dataset checksums. It does not archive raw OANDA response payloads; raw-payload retention is post-MVP if audit needs require it.
- Any post-MVP live feed enters through the same canonical bar-normalization, validation, and provenance interface as OANDA imports. It must not feed detectors directly or bypass historical data-quality controls.
- Immutable normalized minute-bar dataset revisions live as Parquet files. PostgreSQL stores their metadata/checksums plus jobs, configurations, events, outcomes, and annotations; CSV is only a convenience export. Backup includes the Parquet files and PostgreSQL dump.
- Each Parquet dataset manifest carries an explicit dataset-format version. The app refuses an unknown newer format and reads older supported formats through compatibility code; Parquet-file evolution is separate from Alembic/PostgreSQL migration.
- All generated datasets, artifacts, and PostgreSQL volume data live under one configurable, Git-ignored local data root rather than tracked source directories. Backup/restore targets that root explicitly.
- Imports preserve OANDA-provided UTC bar timestamps exactly and never synthesize missing bars. Gaps are recorded and handled by validation rather than filled, preserving source provenance and preventing invented analytical evidence.
- An import fails validation when it contains conflicting duplicate bars for the same instrument, timeframe, and timestamp. Exact duplicates collapse deterministically; the system never silently chooses between conflicting provider values.
- Each running import and evaluation persists a periodic worker heartbeat/lease. On recovery, startup identifies a stale lease and reliably marks that job failed with its preserved partial output, independent of browser state.
- MVP autonomous imports/evaluations require the Mac to remain awake. Sleep pauses local work; only a process/backend stop invokes failed-job recovery. Always-on remote execution is post-MVP.
- MVP exports a selected evaluation's event-level results and aggregate outcome statistics, accompanied by a machine-readable manifest of exact dataset/configuration/version lineage. A general report builder—templates, arbitrary chart composition, PDF/scheduling/sharing—is post-MVP.
- Application logs rotate with bounded retention in the local data root. Concise failure summaries remain persisted with their jobs, so routine diagnostics do not consume disk indefinitely.
- The API permits browser requests only from its configured local UI origin, not a wildcard CORS policy. This is defense in depth alongside localhost-only binding; it is not authentication.
- Every replay, evaluation, and export manifest records the app Git commit/build identifier, detector-definition versions, configuration-schema version, and dataset revision. ConfigHash alone is insufficient to identify implementation changes.
- Durable autonomous evaluations are blocked when their source checkout/build is dirty; they require a committed build identity. Live walkthrough and developer tests may still run with local modifications.
- Evaluation queueing runs a single preflight before accepting a job: clean build identity, current schema, valid/frozen configuration, available immutable dataset revision with verified checksum, and sufficient warm-up/gap conditions. Rejections report the specific unmet prerequisite rather than failing later in a worker.
- Saving a changed named preset always creates a new immutable revision. The browser sends the revision it edited, and the API rejects stale overwrite attempts; runs retain their exact preset revision/configuration snapshot.
- Enforce architecture with automated import-boundary tests: domain, structure, and detector logic remain independent of FastAPI, SQLAlchemy, OANDA, and browser concerns; application code orchestrates use cases; API, persistence, and provider adapters depend inward. This is a CI guardrail, not a runtime service.
- Store bar, event, and job timestamps as UTC instants. Derive analytical trading-day and session boundaries only from versioned IANA timezone calendars, never the Mac or browser clock. The UI may render a chosen display timezone, which cannot change analytical semantics.
- Instrument trading-session calendars are versioned files/configuration shipped with the app, not fetched dynamically at runtime. They define instrument-specific IANA timezone, local session/cutoff times, and holiday/exception rules; named zones such as `America/New_York` apply daylight-saving changes automatically when deriving UTC boundaries. Verify exact OANDA/instrument rules before implementation.
- Import verifies the provider/account environment and exact instrument identifier against a known versioned calendar mapping. If no exact mapping exists, the app rejects session/outcome processing rather than silently applying generic market hours.
- Browser authoring of new formulas or rule combinations may be needed later; it is not an MVP requirement or a committed future feature.

## Example dialogue

> **Researcher:** "I want to edit parameters through the browser."
> **Developer:** "The MVP must include browser parameter editing; the remaining experiment workflow still needs clarification."
>
> **Researcher:** "Stop replay if configuration needs to change."
> **Developer:** "Configuration editing requires stopping first, even when replay is paused."
>
> **Researcher:** "A leg can retrace across EMA and then continue."
> **Developer:** "The EMA-cross segment ends at the opposing cross, but that alone does not end the longer-lived TrendLeg."

## Open decisions

- Do not continue pre-implementation interrogation of individual detector formulas. Refine their remaining exact rules alongside their implementation and fixtures; resume the current review with architecture, integration, operational, and delivery gaps instead.
- Define the optional 35-point retracement experiment's reference point and per-detector action. Revisit how the provisional 30/70/35 thresholds apply under this definition. The earlier proposal to qualify the just-ended EMA-cross leg before candidate creation is not approved.
- Define the optional 35-point retracement experiment precisely if enabled. The proposal to remove all mandatory qualification gates was not approved; 30 bars and 70 points are retained to filter short episodic structures, and qualification persists once earned while the leg remains active.
- Specify the exact editable fields, allowed values, and cross-field validation for the agreed browser parameter groups before implementation.
- Specify the exact configuration-dependent warm-up calculation before implementation.
- Verify and specify each instrument's provider trading-day boundary, timezone, scheduled breaks, and holiday exceptions for the supported historical range.
- Pin the benchmark's exact detector versions/configurations, default outcomes, dataset revision, and development Mac hardware specifications for the agreed one-year/five-minute target.

## Implementation implications

- The approved TrendLeg/EMA-cross-segment distinction supersedes the crossing-ends-TrendLeg interpretation in SCRUM-72/73. The structural protected-swing TrendLeg lifecycle defined in this document is authoritative for MVP implementation. Update SCRUM-72/73, dependent detector stories, fixtures, dependency ordering, and the canonical Drive specifications to match it before TrendLeg-dependent implementation begins.
- Align SCRUM-58 persistence/upsert semantics and SCRUM-60 dataset identity with immutable used dataset revisions; shared mutable bar rows must not change the contents of a revision referenced by an earlier run. This decision is recorded locally and has not yet been applied to Jira or the architecture document.

## Sources

- [Technical architecture](https://docs.google.com/document/d/1RIGUQQHJCGvepXnlZc4Pm7fuoDiAR-REWVAgZZQwFGA/edit)
- [Configuration contracts — SCRUM-105](https://mihailtrunov.atlassian.net/browse/SCRUM-105)
- User decisions from the implementation-readiness interview.
