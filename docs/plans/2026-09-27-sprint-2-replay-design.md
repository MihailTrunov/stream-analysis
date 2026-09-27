# Sprint 2 ReplayRun and SimulationClock design — 2026-09-27

Scope: SCRUM-61 and SCRUM-62. SCRUM-60, SCRUM-105 and SCRUM-106 supply
dataset identity and the resolved detection snapshot. SCRUM-63 later owns the
user-facing ReplayCursor; SCRUM-80 later invokes the shared analytical pipeline.

## ReplayRun boundary (SCRUM-61)

Three approaches were considered: keep lifecycle only in browser memory,
mutate `run_snapshots`, or persist a separate lifecycle record referencing the
immutable snapshot. Use the third. The existing `run_snapshots` row continues
to pin dataset revision, fully resolved DetectionAnalysisConfig and hash,
calendar version, build identity and optional preset. A replay-lifecycle row
adds selected half-open range, CREATED/RUNNING/PAUSED/COMPLETED/FAILED/ABORTED
status, current cursor, timestamps and optional failure reason. Creation is
transactional and verifies that the selected revision has matching dataset
lineage. Reload checks the immutable snapshot and hash. Status transitions
are explicit and invalid transitions fail without mutation. Reset means a
new run ID and fresh analytical state, never rewriting an old snapshot.

The immutable run context carries lineage and resolved configuration but no
future Bars. It can be passed to analytical components without leaking a
dataset array. Actual pipeline execution and structured per-bar logs are
accepted with the later shared runtime; this Story provides their pinned run
identity and transition surface.

## SimulationClock boundary (SCRUM-62)

The clock accepts a validated `BarSequence`, starts before index zero, and
advances exactly one completed Bar per step. Each step returns an immutable
observable view with a fixed visible index, so a previously issued view does
not gain access when the clock advances. The view exposes only current/past
bars and UTC market time; attempts to ask for future indexes or timestamps
raise. No public seek or playback-rate control is supplied. Empty, duplicate,
unordered and final-bar cases are tested. Warm-up bars use the same stepping
boundary; SCRUM-63/80 decide how to drive the clock and pipeline.
