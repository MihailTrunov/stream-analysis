# Live analytical workspace — Astra Python and web review

Date: 2026-11-06. Reviewers: independent Astra agents at high reasoning, with separate Python and web scopes. Scope: shared live ingestion delivered in `3c9169a`/`9d0c840` and the live analytical session/workspace changes delivered with this report. The reviewers inspected code and ran isolated reproductions; they did not edit production code. Current-date OANDA/calendar acceptance was outside deterministic review evidence.

Implementation commits: `8db9e8c` (Python/storage) and `f5166a0` (web/workspace).

## Findings and resolution

| Priority | Area | Confirmed behavior | Resolution |
| --- | --- | --- | --- |
| P1 | Python bootstrap integrity | A second same-feed bootstrap could disagree with previously captured minutes because overlap validation started after the bootstrap end. | Compare all captured bootstrap overlap under the same SQL feed lock used by acquisition, holding it through initialization/manifest publication. Reconcile later-acquired overlap before analytical advancement and freeze on conflict. Reject mismatched normalization versions. |
| P2 | Python annotation attribution | Live detector/session annotation endpoints returned 404; missed-pattern attribution treated a live input as a historical dataset. | Resolve live review scopes and stream IDs durably from manifests and committed receipts. Preserve live-input identity; add an explicit live missed-target foreign key and receipt-bounded interval validation. Preserve SQLite audit triggers across table rebuild. |
| P1 | Web live projection | At the 500-bar cap, unchanged logical indices left the visible cutoff stale, hiding newly received patterns. | Derive visible timestamps from current bars on each snapshot, even when logical indices do not change. |
| P2 | Web earlier history | Panning past the loaded left edge requested no older retained candles. | Wire earlier-page requests, retain ongoing live processing, and use separate shared-history navigation outside initialized live context. Persist older-history source/cutoff and reload the selected timeframe. |
| P2 | Web manual calculation follow-up | The first Calculate click on a full live window could be discarded when removing the forming candle shifted the left edge by one bar. | Preserve the pending explicit action across that same-cutoff adjustment; changed cutoff/settings/connection still cancel it. |
| P2 | Web historical projection follow-up | Older uncalculated history inherited the current live stream's calculated state; M1 source responses without availability timestamps inherited live-now as their cutoff. | Use historical calculation coverage for external views and derive M1 completion from source timestamp plus one minute. Keep current live observations out of external history. |
| P2 | Web mode restoration follow-up | An older-history source survived History → Live and was restored when the new live chart froze. | Clear external history identity and pending navigation when leaving live mode. The final isolated mode-switch regression passed. |
| P2 | Web pending-navigation follow-up | Switching chart timeframe during older-history acquisition could relabel the returned bars with the new timeframe. | Disable timeframe changes during pending navigation; ordinary external-source timeframe changes reload independently. |

## Follow-up evidence

The backend follow-up found no remaining actionable defect. Two additional focused reviewer probes passed: conflicting overlap arriving after attachment freezes the committed prefix, and annotations resolve while the live session remains connected. Parent regressions additionally verify rejected attachment leaves no published input and event/missed annotations remain available after disconnect with live-input attribution and future-bound rejection.

The web reviewer reproduced and then verified the rolling-window and first-click manual calculation fixes. Permanent browser regressions retain these scenarios and earlier-history navigation; the final isolated older-history/cutoff/timeframe/mode-switch probe passed. Both Astra reviewers report no outstanding confirmed findings.

## Verification

Full Python: **986 passed, 12 skipped**. Complete browser suite: **56 passed**. Frontend units: **49 passed**. Isolated PostgreSQL migration/portable-backup restoration: **1 passed**. Ruff, mypy (91 source files), frontend build/lint and diff checks passed. Detailed verification is recorded in the accompanying [delivery document](../plans/2026-11-06-scrum-137-live-analysis-workspace.md). Verification includes the full Python/browser suites, frontend unit/build/lint checks, Ruff/mypy, migration/audit guards, and a real isolated PostgreSQL dump/archive/restore run. These checks do not establish actual-account current-calendar operation or support multiple acquisition owner processes.

## Deployment boundary

The finite verified OANDA UK calendar still ends on 2026-09-28. Current-date operation fails closed. No application installation was migrated and no real provider credentials were read, logged or committed. SCRUM-137 remains open for server configuration and reviewed calendar/provider acceptance, following the approved calendar verification procedure.
