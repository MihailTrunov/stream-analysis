# SCRUM-93 — event explanation inspector

The replay timeline and chart annotations already select an emitted
`DetectorEvent`. Replace the compact JSON line with an inspector that reads
that same canonical event, not an independently queried or recalculated
interpretation.

The replay API will add the registered `PatternDefinition.name` to each event
projection. It already carries run ID, dataset revision, instrument/timeframe,
pattern ID/version, instance ID/sequence, detection-config hash, event/detection
times and structured rationale. No new persistence or analytical state is
needed.

Render three sections: identity and reproducibility lineage; the underlying
event time versus first observable detection time; and the versioned rationale
schema. For `detector-evidence-v1`, show each condition's ID/status, observed
typed value, recorded operator/threshold, recorded units, source references,
and every typed feature. Preserve decimal strings exactly. Use the evidence's
own units when present; suffixes such as `_points`, `_bars`, `_seconds` and
`_pct` may label feature values but never alter them. Show null or missing
optional fields explicitly. For an unrecognized rationale shape, show its
schema and raw serialized evidence rather than inventing an explanation.

Selection remains bound to the run-local emission ordinal; seek/reset clears
it. Tests use fixed confirmation and invalidation examples with distinct
event/detection times, exact thresholds, missing optional values, and lineage.
Browser smoke verifies the seeded demo event end to end. Keep README launch
instructions current.

SCRUM-94 remains blocked by SCRUM-113's audit model, which is still To Do;
this design does not add manual validation records or labels.
