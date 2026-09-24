# MVP architecture reconciliation plan

Status: approved reconciliation plan — pending propagation to Google Drive specifications and Jira.

## Purpose

Reconcile the architecture and implementation-readiness decisions captured in `CONTEXT.md` with the Charts documentation and Jira Stories `SCRUM-54` through `SCRUM-113`, plus `SCRUM-119` and `SCRUM-120`.

Use the **Confirmed MVP behavior and research scope** and **Confirmed MVP architecture** sections of `CONTEXT.md` as committed requirements. Its **Deferred / post-MVP** section is excluded from MVP delivery; its **Open decisions** section identifies details to verify or specify at the relevant implementation step.

The detector-rule review is deliberately bounded. The structural protected-swing TrendLeg lifecycle in `CONTEXT.md` is approved and authoritative for MVP implementation; propagate it through architecture, Jira contracts, fixtures, and dependency ordering now. Complete any remaining detector-specific rule tables with their implementation fixtures.

## Source-of-truth changes

### Technical Architecture & Engineering Principles

Update the implementation baseline to state:

- Python 3.13; Nx task runner; pinned `pnpm` and `uv` toolchains.
- Local, single-user macOS MVP. Latest Chrome on macOS is the supported browser. API/UI bind to localhost; PostgreSQL is Compose-internal; no authentication, hosted deployment, registry, Redis, Celery, or Nx Cloud.
- Docker Compose runs PostgreSQL, API, autonomous-evaluation worker, import worker, and UI. Nx exposes lifecycle, test, backup/restore, benchmark, and CI targets.
- PostgreSQL is the transactional store for metadata, jobs, configurations, events, outcomes, annotations, and queue state. Immutable normalized bars are versioned Parquet revisions under a configurable Git-ignored data root.
- The durable queue is PostgreSQL-backed. One autonomous evaluation runs at a time; queued evaluations run serially. One import may run concurrently with the evaluation. The interactive walkthrough remains separately responsive.
- Automated checks use lockfiles, import-boundary tests, deterministic fixtures, PostgreSQL integration tests, Chromium smoke tests, and GitHub-hosted Ubuntu CI. Performance runs are explicit Nx benchmarks.

### Core Domain Model

Add or clarify:

- UTC instants are canonical for bars, events, and jobs. Instrument calendars are versioned, account/instrument-specific IANA-zone configurations; their version is part of run identity.
- Dataset revisions are immutable: Parquet manifest format version, source and dataset checksums, provider/request/retrieval/normalization provenance, and no synthetic gap fill. Exact duplicate bars collapse; conflicting duplicates fail validation.
- Configurations, presets, and runs are immutable/versioned. A run captures configuration-schema and calendar versions, dataset revision, detector versions, Git commit/build identity, and the clean-build gate.
- Both walkthrough and autonomous evaluation drive the same deterministic per-bar pipeline and immutable `MarketState`. Intermediate per-bar state is recomputed, not normally persisted.
- Update the TrendLeg definition to the approved model that distinguishes an EMA-cross segment from the longer-lived structurally protected TrendLeg. Treat the protected-swing lifecycle, qualification anchors, strict comparisons, and causal ordering recorded in `CONTEXT.md` as authoritative. Keep remaining detector-specific rule-table detail out of this document.

### MVP Scope & Boundaries / Product Scope

Clarify:

- Network access is required only for OANDA import. Local immutable datasets support offline replay/evaluation.
- Browser parameter editing is schema-driven and server-authoritatively validated; it cannot author formulas. Changed presets create a new revision.
- Autonomous evaluations are durable queued jobs scheduled from the UI. Multiple runs may be queued, but exactly one evaluation executes at a time in queue order; parallel evaluation execution is outside MVP scope. A manual stop marks the current job `CANCELLED`, preserves incomplete output, and pauses queue auto-run until explicitly re-enabled. A crash marks a stale-heartbeat job `FAILED` and leaves auto-run disabled after restart.
- A live walkthrough is an ephemeral browser session, one at a time in the UI, with browser-local range/cursor recovery. It does not prevent autonomous work.
- Chart requests are viewport/range based; overly wide views receive deterministic display-only aggregates. Selected evaluations export event-level data, aggregate statistics, and a lineage manifest. Report building is post-MVP.
- Include local diagnostics, bounded log rotation, manual backup/restore, and a seeded non-research demo dataset/preset.

## Jira reconciliation

| Stories | Required reconciliation |
| --- | --- |
| `SCRUM-54`–`60` | Canonical UTC bars; exact duplicate/conflict policy; immutable Parquet dataset revisions; manifest/dataset-format version; source provenance; gap validation; account/instrument-to-calendar mapping; no mutable upsert of a revision used by a run. |
| `SCRUM-61`–`67` | Keep replay strictly causal, but describe live walkthrough as ephemeral/browser-local and driven by the shared pipeline. Configuration changes require stopping the walkthrough; fresh state is recomputed from warm-up. |
| `SCRUM-68`, `77` | Enforce a deterministic component pipeline producing one immutable `MarketState` per completed bar. Add component-declared warm-up requirements and no infrastructure imports in core code. |
| `SCRUM-71` | Replace implicit/session-local handling with a versioned IANA calendar contract. Exact OANDA account/instrument calendar values remain a verification task. |
| `SCRUM-72`, `73`, `74`, `75` | Update TrendLeg contracts to the approved structural protected-swing lifecycle. EMA crosses create raw EMA-cross segments and may be detector evidence; they do not end a TrendLeg. Preserve the approved strict/causal/qualification rules and update dependencies to match. |
| `SCRUM-78`–`82`, `86` | Registered code-defined detector versions only; no browser formula loading. Shared runtime accepts the same immutable state in walkthrough and evaluation. Expand fixtures for lifecycle, parity, and clean lineage. |
| `SCRUM-83`, `85` | Apply the agreed active-qualified TrendLeg gating and independent competing-candidate behavior. Do not broaden unfinished detector tables; make remaining rule-table details an implementation/fixture prerequisite. |
| `SCRUM-87`–`94` | Schema-driven editor; Chrome/macOS target; viewport-bounded charting with display aggregation; background status/queue UI via polling; ephemeral walkthrough; diagnostics; preserved manual annotations; no browser deletion of research records. |
| `SCRUM-95`, `96` | Define durable `EvaluationJob`/queue state and transitions: queued, running, completed, cancelled, failed; PostgreSQL transactional claim/lease; serial evaluation queue; manual start/reorder/remove for unstarted entries; pause after stop/crash; low-priority chunked evaluation. |
| `SCRUM-97`–`104`, `119`, `120` | Calendar version and session boundary in outcome/run lineage; Parquet dataset revision pinning; compact evidence snapshots not full per-bar persistence; selected-run exports; shared-pipeline parity and no-look-ahead tests. |
| `SCRUM-105`–`108` | API-owned normalization/validation, configuration-schema version, generated editor schemas, immutable preset revisions with stale-write rejection, calendar version, Git/build identity, clean-build preflight, and config/build lineage in hashes/manifests. |
| `SCRUM-109`–`113` | Extend leakage, cross-mode parity, primitive fixtures, structured logging, and annotation audit coverage to the revised pipeline, versioning, and lifecycle semantics. |

## New or explicitly split backlog items

Create only if the above Stories cannot absorb the work without becoming incoherent:

1. **Establish local MVP runtime and developer workflow** — Nx, pinned toolchains, Docker Compose topology, local-only network binding, diagnostics, Chrome/macOS support, and GitHub Actions.
2. **Implement durable local job scheduler** — PostgreSQL queue/claim/heartbeat, evaluation serialisation, independent import slot, stop/crash semantics, and queue UI contract. This should not be hidden inside detector execution.
3. **Implement immutable Parquet dataset store and local operations** — data-root layout, manifest versioning, backup/restore verification, retention/log rotation, and seeded demo data. Split from `SCRUM-58` only if that Story is otherwise too broad.

## Dependency/order changes

1. Establish the local runtime/toolchain and source-code boundaries.
2. Define Bar/Instrument, dataset revision, validation, calendar, configuration, lineage, and persistence contracts.
3. Build the deterministic state pipeline and shared replay/evaluation driver against seeded fixtures.
4. Deliver one fixture-backed vertical slice: bars -> state -> registered detector -> event persistence -> Chrome walkthrough.
5. Add OANDA import and immutable dataset publication.
6. Add durable autonomous queue, outcomes, exports, and UI job controls.
7. Complete the remaining detector tables and production research fixtures; run benchmark and parity suites.

`SCRUM-72/73` and their dependencies must be updated to the approved TrendLeg model before downstream TrendLeg-dependent detector implementation begins. `SCRUM-104`, `109`, and `110` should verify the shared-pipeline result after the lifecycle update.

## Verification checklist before changing external sources

- Fetch every affected Story and show the exact proposed description/acceptance-criteria delta before editing it.
- Confirm the actual OANDA account environment and canonical identifiers for US30 and DAX, then verify session/holiday rules before publishing calendar values.
- Choose the reference Mac and pin the benchmark dataset/configuration/detector versions.
- Decide the exact browser-editable fields by registered schema; do not allow arbitrary formula creation.
- Update the Drive architecture documents before or alongside Jira, then re-check Jira dependency links against the corrected implementation order.
