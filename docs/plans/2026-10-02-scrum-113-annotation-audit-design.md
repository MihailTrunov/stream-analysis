# SCRUM-113 — manual-validation annotation audit model

An annotation is research judgment, never detector evidence. The MVP repository
stores an immutable target/lineage snapshot and an append-only revision history.
The current label and note are the last revision; earlier judgments remain
queryable. No delete operation is exposed.

Three target kinds are supported. `event` points to a persisted DetectorEvent
UUID and snapshots its instance, run, dataset, instrument, timeframe, pattern
version and detection configuration. `instance` points to a persisted
PatternInstance and snapshots the same lineage. `missed_pattern` points to no
event: it names an existing dataset revision, instrument/timeframe membership,
half-open UTC chart interval, and expected pattern ID/version. It cannot enter
detector-event counts or be silently carried to a new detector run.

The repository validates targets before insertion; foreign keys guard event,
instance and dataset identities. Revisions carry their own timestamp and
optional local reviewer identity. Writes use optimistic revision numbers to
avoid lost updates. Export is a separate research-metadata projection with
target lineage and full judgment history; detector records are untouched.

SCRUM-94 will own browser/API creation and display. Automatic missed-detection
matching, recall scoring, multi-user approval and deletion remain out of scope.
