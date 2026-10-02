# SCRUM-94 — browser manual validation

The browser event timeline currently shows runtime-only DetectorEvents without
the durable event UUIDs required by SCRUM-113. The replay pipeline will accept
an optional event-evidence sink. Browser replay supplies a sink that commits
each emitted event and its PatternInstance within the same per-bar savepoint as
the replay cursor advance. A persistence failure latches replay rather than
showing an unrecorded event as reviewable. The chart/timeline API adds the
persisted event and instance UUIDs while retaining runtime emission order.

The review API creates event or instance annotations by persisted UUID, edits
them with expected revision, reads their full audit history, and returns a
separate export projection. It also creates missed-pattern annotations against
the pinned dataset revision, instrument, timeframe, and half-open chart interval.
Only supported labels are accepted. No endpoint deletes research records.

The walkthrough inspector shows any annotations for the selected visible
event, permits a local single-user tag/note edit, and marks reviewed events in
the timeline. A chart interval form creates and lists missed-pattern records;
it cannot reach beyond the current causal cursor. After reset/seek, event
annotations remain attached to the old run IDs and are not copied. Review
labels mean agreement with intended detection, never profitability. Browser
display reads stored annotation data; it does not modify DetectorEvents.

Validation will cover API create/edit/read/export, unsupported labels, exact
version/event identity, detector-event immutability, rerun isolation, missed
interval bounds, and browser inspector/timeline behavior.
