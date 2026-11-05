# Replay navigation and Stop preservation — review round 4

Date: 2026-11-05  
Reviewer: Astra, high reasoning  
Reviewed revision: `86037629c1f17bac09df7eb26b7bf5ae69a0a308` (`master`)  
Baseline: `3db677a99c19c846bbfc8982f13f04e65bff7d73`  
Change range: `3db677a..8603762` — `99e42be`, `8603762`.

## Assessment

**Two confirmed P2 defects require correction.** Both affect chart position during replay browsing. The review applied the repository code-review skill, read the changed controller/rendering/backend paths and traced restoration, navigation and causal-window ownership. It does not establish acceptance of live-provider operation or the entire chart-workspace story.

## Findings

### 1. [P2] Preserve the restored viewport instead of refocusing the saved target

Location: `web/src/ReplaySession.tsx:143–150`, particularly line 150. The resulting range replacement is in `web/src/CandlestickChart.tsx:205–219`.

Restoring an active replay browsing window restores its saved logical viewport through `initialRange`. Once the window is ready, the new focus effect unconditionally calls `setFocusTime` and increments `focusVersion`. The chart then applies a target-centered range over the restored pan/zoom.

Reproduction: browse to 09:10, zoom, wait for viewport persistence, then reload. A focused Chromium probe measured the persisted viewport before and after:

```json
{
  "before": {"from": 8.30769230769231, "to": 35, "origin": "2026-01-05T09:00:00Z"},
  "after": {"from": 0, "to": 35, "origin": "2026-01-05T09:00:00Z"}
}
```

The run and candles restore, but the user's zoom is overwritten. Distinguish restoring a window with a matching saved viewport from navigating to a new target; preserve the viewport during restoration. Add browser coverage for zoom/pan restoration while replay remains active, alongside the existing Stop-to-exploration restoration case.

### 2. [P2] Synchronize the browsing target after Move to an already-loaded candle

Location: `web/src/ReplaySession.tsx:200–203`; the stale target is consumed by `web/src/useReplayController.ts:78–82`.

`replay.window.target` changes only when Move to cannot find the selected candle in the loaded bars. Moving to another already-loaded candle updates `focusTime` alone. Changing timeframe reconstructs the browsing window around the earlier target, causing the chart to jump back to the previous navigation point.

Reproduction: browse to 09:10, Move to 09:30 within the loaded window, then switch the display timeframe to 5m. The Chromium probe first confirmed focus at 09:30, then observed the focus notice return to `Viewing 05 Jan 2026, 09:10 GMT`. The assertion expecting 09:30 failed.

Keep the persisted browsing target synchronized with successful Move to actions, including navigation satisfied from loaded candles. Add a browser regression that performs two Move to actions followed by a timeframe change; verify both the focus and restored target refer to the most recent selection.

## Verification and limits

Two temporary Chromium probes used the real seeded API and the shortened cursor-window response technique already used by `web/e2e/replay-history.spec.ts`. Both failed on the targeted behavioral assertions, confirming the defects above. Commands:

```sh
STREAM_ANALYSIS_SMOKE_API_PORT=18001 STREAM_ANALYSIS_SMOKE_WEB_PORT=15174 pnpm --dir web exec playwright test e2e/astra-review-probe.spec.ts --reporter=line
STREAM_ANALYSIS_SMOKE_API_PORT=18001 STREAM_ANALYSIS_SMOKE_WEB_PORT=15174 pnpm --dir web exec playwright test e2e/astra-review-probe.spec.ts -g 'latest Move' --reporter=line
```

Initial sandbox execution could not bind localhost; execution with the approved local-server permission succeeded. The temporary probe was removed. Production code and existing tests were unchanged.

The previous implementation validation — 923 Python tests passed, 10 skipped; 44 browser cases passed; 44 web unit tests passed; static checks and production build passed — remains prior evidence. The full suites were not rerun during this review. Existing browser coverage restores replay candles/target and verifies stopped exploration geometry, but does not cover active-replay zoom restoration or a second locally satisfied Move to before changing timeframe.

Stop during a pending replacement read and timeframe/history-boundary behavior were investigated without an additional confirmed finding. No live-provider, PostgreSQL multi-process or full API-restart cursor-reconstruction acceptance was performed. Deferred live-follow work remains outside this increment's completion claim.

No fixes, commits, push or Jira changes were made during this review. Correct both confirmed navigation defects before continuing to the next implementation boundary.

## Correction follow-up

Both findings were addressed after the review at the user's request. Restoring the same replay window/timeframe with a matching saved viewport now restores the focus label without issuing a new chart-focus command. Explicit navigation still increments the focus version and overrides the restored geometry. Move to an already-loaded candle now updates the controller's saved browsing target while retaining the same price window and coherent snapshot; timeframe changes and reload use that latest target.

Two permanent browser regressions were added in `web/e2e/replay-history.spec.ts`. Before the fix they reproduced the viewport reset (`from` changed from 8.30769230769231 to 0) and stale target (09:10 remained saved after Move to 09:30). After the fix, both pass, including explicit navigation after restoration, no extra price read for loaded navigation, timeframe changes, reload and analytical-state invariance.

Final correction verification: **46 browser scenarios passed** (8 replay-history cases and 38 remaining cases), **44 frontend unit tests passed**. Frontend lint, TypeScript checking, production build and `git diff --check` passed. Backend code is unchanged; the previous 923 Python passed / 10 skipped result was not rerun for these frontend corrections. SCRUM-133 remains In Progress for outstanding live-follow acceptance.
