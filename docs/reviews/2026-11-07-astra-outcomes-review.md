# Independent Astra high review: scheduler fixes and registered outcomes

Astra reviewed `8bc48ba..d7f8f71` on master: scheduler corrections `3a0400d`, SCRUM-97 forward returns `9e3347f`, and SCRUM-98 excursions `d7f8f71`. The review used the project's code-review skill, full affected files, call paths, regression tests and the approved ticket contracts.

**Verdict: no actionable P1/P2/P3 findings verified.** All three findings from the [previous scheduler review](2026-11-06-astra-scheduler-review.md) are resolved: standalone heartbeat covers source loading and processing; PostgreSQL orphan recovery locks and rechecks heartbeat freshness; shared chunk-size validation precedes recovery, claims and worker startup mutations.

Astra independently passed **17 cases**: eight scheduler-fix regressions (including the PostgreSQL stale-row race), eight M5 excursion probes covering actual source-close availability and bounded future windows, and one PostgreSQL ownership-loss probe. The last probe fenced the analytical owner after an uncommitted observation was inserted: the whole batch rolled back, zero observations persisted, and the run finished FAILED. The M5 probes also checked directionless raw and captured-ATR values against future extrema.

Reviewed paths included configuration/default resolution, v1 identity preservation, exact multi-configuration selection, immutable direction/ATR capture, signed formulas, source availability and window boundaries, registered queue execution, observation identity/lineage, cancellation/failure, completion and ownership fencing. Scratch probes were kept at `/private/tmp/astra-outcomes-review/test_independent.py`; no tracked files were changed by the reviewer.

The review used isolated disposable PostgreSQL schemas, without provider requests or installed application/database access. Astra did not duplicate broad regression suites or browser checks. No frontend implementation changed in this batch; browser validation was not repeated. Author verification is recorded in the [forward-return](2026-11-07-scrum-97-forward-return.md) and [excursion](2026-11-07-scrum-98-excursions.md) reports.

A final catalog-test isolation adjustment explicitly disables inherited database/demo-seeding settings before its read-only request. This test-only adjustment is outside the reviewed commit range; the reviewed production source is unchanged. Its focused check and final regression evidence are recorded below.

Final author regression on committed production source `d7f8f71`: **1,213 Python tests passed, 23 PostgreSQL-dependent skips** (375.94 seconds). A separate fresh disposable PostgreSQL run passed **24 tests**. The catalog-test isolation guard passed its focused test afterward. Ruff, mypy (120 source files), changed-outcome-file formatting and diff checks passed. The guard changes only test environment setup; production source is unchanged from the reviewed and fully tested commit.
