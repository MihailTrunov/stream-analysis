# SCRUM-137 — current-provider and calendar acceptance

Verification date: 2026-11-06. This closes the provider/configuration acceptance boundary left by the [live workspace delivery](2026-11-06-scrum-137-live-analysis-workspace.md). The earlier check established that credentials were absent from the agent shell and that no application containers were running; the documented `app/.env` was subsequently located and used through Compose environment injection. Its contents were never displayed or copied into repository artifacts.

## Authenticated observations

Read-only account-scoped discovery returned `US30_USD` and `DE30_EUR`, both with display precision 1 and pip location 0, matching the pinned contracts. The supplied server context is UK/live; instrument discovery does not independently infer an account's jurisdiction.

[Sanitized candle observations](../research/2026-11-06-oanda-uk-calendar-observations.json) retain 18 bounded M1 windows and two latest-candle checks. They contain 33,901 completed candles, counts and consecutive timestamp envelopes, with no credentials, account identifiers, OHLC prices or financial/account responses. All observed completed-minute intervals classify open under the new profile. Missing nominal first/last/interior minutes remain gap observations; they do not become invented calendar exceptions.

The samples cover the former coverage edge, October regular sessions/weekends, European DST ending on 25 October, the US/European mismatch week, US DST ending on 1 November and current 5–6 November candles. Two initial requests extending beyond the current time returned HTTP 400; successful bounded replacements cap the end at the latest elapsed minute. No failed request is presented as schedule evidence.

The [official UK hours](https://www.oanda.com/uk-en/trading/hours-of-operation/) still describe US Wall St30 in Chicago time and Germany40 in Frankfurt time with its seasonal opening shift. The [holiday notices](https://www.oanda.com/uk-en/trading/holiday-trading-hours/) were checked through the latest displayed notice, 30 October 2026; those additional October notices name other instruments. No new US30/DE30 exception was inferred from unrelated holidays or absence of ticks.

## Versioned calendar delivery

The current profile is `oanda-uk-live-m1-2026-11-06-v1`, with inclusive analytical-start coverage from 2023-09-27 through **2026-11-06**. This is a finite verified schedule, not permission to extrapolate future sessions. Continue the approved calendar verification/versioning procedure before operating outside this range.

The previous `oanda-uk-live-m1-2026-09-28-v1` remains resolvable with exactly its original fields, coverage and exceptions. Independent Astra high review compared both old profiles field by field and checked every recorded completed interval against the current profile, finding no actionable defect.

Replay, retained feed verification and queued imports explicitly resolve their pinned calendar versions. Fixed offline demo revision IDs keep their original legacy calendar/provenance, avoiding republishing the same immutable IDs with changed metadata. A selected old-version provider source may establish matching account/instrument context for new navigation, but new range acquisition uses the requested calendar's own feed key; old prices/manifests are not relabelled or silently merged across versions. Cross-account, normalization and unknown-calendar differences continue to fail.

## Real live-analysis acceptance

The [sanitized acceptance result](../research/2026-11-06-oanda-live-acceptance.json) records actual OANDA responses through the production adapter, import pipeline and live analytical manager in a disposable process:

| Check | US30 | DAX |
| --- | --- | --- |
| Completed bootstrap candles imported | 180 | 180 |
| Additional completed live minutes consumed | 3 | 3 |
| Forming candle displayed separately | Passed | Passed |
| Repeated same-cutoff poll preserves detector counts | Passed | Passed |
| Captured input and ordinary replay market-state/event parity | Passed | Passed |
| Input verified after disconnect | Passed | Passed |

Both sessions reached connected status through 2026-11-06T10:15:00Z. The two durable inputs and all six captured journal minutes verified. Replay comparison used the same complete initialization origin and source prefix, comparing component state and detector transitions while excluding execution-specific identities.

Credentials were injected only into disposable Compose diagnostic processes. SQLite/Parquet stores were created in process-private temporary directories and removed afterward. No application dependencies were started, installed database was migrated, installed dataset changed or financial/order endpoint called. The existing stopped installation must be restarted on the new code to use this delivery.

## Regression and review

The new tests cover the October/November DST boundaries, immutable legacy resolution, unknown/out-of-coverage rejection and preparing fresh current-version history from an old selected bootstrap without changing the old revision. Prior out-of-coverage tests now use dates outside the extended finite range. Final verification: **991 Python tests passed, 12 skipped**; **56 browser tests passed**. Ruff, mypy (91 source files) and diff checks passed. SCRUM-137 is ready for review; no application installation was deployed or migrated.
