# SCRUM-119 context capture review

This is an implementation/self-review, not an independent Astra review.

The review traced runtime output, the same-bar continuation chain, event persistence, captured-live launch ordering, higher-timeframe aggregation, SQL loading and API retrieval. Corrections during implementation normalized enums to ordinary strings, checked the actual persisted source occurrence/event, and moved runtime-dependent capture orchestration into the application layer. Existing architecture tests continue to enforce that persistence and API modules do not import the detector layer.

The snapshot stores selected observable state rather than serializing the whole runtime or recomputing indicators. It preserves source semantic references and phase context, while execution UUIDs in nested component lineage stay outside analytical X. Later bars cannot mutate detached values. Outcome selection is not an input to analytical capture. Current v1 schema and leakage rules are documented in the [design](../plans/2026-11-06-scrum-119-detection-context-design.md).

Migration guards were exercised on SQLite and disposable PostgreSQL. Capture and source events share a transaction. The SQL reader verifies payload identity and content hash before returning data; the API does not require an active chart session. Existing events are not retrospectively reconstructed from their latest occurrence context.

Follow-up: SCRUM-95/96 now provide the immutable evaluation/EventSet model and production executor using this same capture path. SCRUM-110 includes that driver and actual outcome-plan-only invariance. Production outcome calculations remain outside these detector/runtime parity stories. Custom multi-transition detectors must supply intermediate contexts to avoid explicit UNAVAILABLE context; this does not affect the three current MVP detectors.

Verification: **1,037 Python tests passed, 13 skipped; 56 browser tests passed.** The separate real PostgreSQL context integration test passed against a disposable PostgreSQL 16 container. A final 14-test context/curated-detector run passed after restricting serializable dataclasses to the approved market evidence types. Ruff, mypy (99 source files) and diff checks passed. Other skipped PostgreSQL checks are not claimed executed.

No installed application database or provider data was changed. Migration `20261106_16` is supplied for deployment, and the disposable PostgreSQL container was removed after verification.
