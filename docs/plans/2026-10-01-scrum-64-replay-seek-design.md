# SCRUM-64 — deterministic replay seek

Date: 2026-10-01. Scope: in-memory, completed-bar replay navigation for the
interactive walkthrough. SCRUM-61's persisted run lifecycle remains immutable.

## Decision

Use full causal recomputation from the clock's bar-zero origin. An alternative
would persist or retain analytical snapshots, but that requires versioned
component/detector serialization and proof that restoring each snapshot is
equivalent to replay. Forward-only stepping is cheaper but does not handle a
backward seek. MVP therefore resets the owned analytical pipeline and clock,
then processes bars zero through the target in canonical order. This includes
all warm-up bars before the visible interval. The target's returned view is
the same past-only `ObservableBars` view used by ordinary stepping.

The public cursor accepts either a zero-based index or an exact, timezone-aware
bar timestamp. Timestamp lookup is a control-plane operation on clock metadata;
the analytical pipeline never receives future bars. Index `-1` denotes the
pre-first-bar state. There is no nearest-bar clamping: invalid types,
out-of-range indexes, and absent/naive timestamps raise without changing
cursor or analytical state. Seeking to the current position is a no-op.
Seeking from the initial position processes only the required prefix; seeking
from any other position resets and replays that prefix. If reset or processing
fails, the existing failure latch prevents further advancement until a
successful explicit reset.

This API must not be called on `ReplayPipeline`'s persisted cursor. That
pipeline has a monotonic database cursor and immutable run snapshot; a UI
walkthrough seek uses its own in-memory cursor over the same pinned data,
config, calendar and detector bindings. Creating a new persisted run ID is
required if a persisted replay is restarted.

## Verification

Compare state frames and detector events after seek at several targets against
fresh uninterrupted replay to the same targets, then continue to the end.
Cover backward/forward seeks, warm-up, end-of-sequence, pre-first reset,
timezone-equivalent timestamp selection, invalid-target atomicity, no future
bar access, and a pipeline failure during reconstruction.
