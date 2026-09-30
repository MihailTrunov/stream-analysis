# SCRUM-84 — approved Range Compression v1 rule table

Decision date: 2026-09-30. The researcher approved this implementation
interpretation of [SCRUM-84](https://docs.google.com/document/d/1JQqBtsAZl-TANorqXwnEwddSYm2bpOyWYOI8L5wu6ns/edit)
and its provisional thresholds. It supersedes the Story's ambiguous
`CONFIRMED/ACTIVE` state wording without changing the research hypothesis.

| Current occurrence state | Next state | Completed-bar rule |
| --- | --- | --- |
| No occurrence | CANDIDATE | Canonical RangeState is available and compression ≥80 **and** CHOP >61.8; count this bar as 1. |
| CANDIDATE | ACTIVE | The entry conjunction holds for the configured number of consecutive completed bars (default 5, inclusive of start). This single transition is the confirmation event; no separate persisted CONFIRMED state. |
| CANDIDATE | INVALIDATED | Before confirmation, either entry threshold fails or the required RangeState observation is unavailable. Record the exact failing/unavailable cause. |
| ACTIVE | COMPLETED | Compression <60 **or** CHOP <50, recording COMPRESSION_RELEASE, CHOPPINESS_RELEASE, or BOTH. Equality to either release threshold does not release. |

Only `confirmation_bars >= 2` is supported in v1, preserving a distinct
candidate observation before confirmation. Configured thresholds and bar count
participate in DetectionConfigHash. The `require_all_confirmation_bars`,
`require_bounded_range`, and `predict_breakout_direction` v1 settings are
fixed respectively to true, false, and false; semantic alternatives require
a new definition version.

Confirmation uses `event_time = candidate_start_time` and
`detection_time = current fifth qualifying bar`; the earlier event time is
permitted only because that same occurrence's candidate event is already
observable. A failed candidate remains inspectable; a later qualifying bar
can start a new distinct occurrence. ACTIVE ignores the higher entry
thresholds. No breakout, direction, future expansion, or outcome gates
confirmation or completion.

Required boundary tests: strict/inclusive entry and release equality,
fourth/fifth-bar timing, configured bar count/hash variation, unavailable
candidate invalidation, both release causes and simultaneous release,
active hysteresis, terminal re-entry, typed evidence, no-look-ahead,
and reset/replay parity.
