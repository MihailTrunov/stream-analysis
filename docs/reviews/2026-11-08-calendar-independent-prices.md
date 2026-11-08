# Calendar-independent chart price acquisition

> Superseded policy: the [provider-observed detector amendment](2026-11-08-calendar-independent-detectors.md)
> removes the calendar-confirmation gate for chart detectors and replay when prices
> are available. Statements below describing that gate document the earlier delivery.


The previous startup fix, commit 316798a, displayed the last verified history but still prevented current provider requests beyond finite calendar coverage. The user explicitly requires a price attempt for both live and historical chart windows even when calendar information is unavailable.

## Implemented boundary

`OandaHistoricalDataSource.price_window` uses account-scoped midpoint M1 candles with a count and an exclusive end, without deriving calendar sessions. The provider selects the last 1,440 completed minutes across closures. Account/instrument authentication, response identity, canonical OHLC, ordering, minute alignment, completion and conflicting duplicates remain validated. Credentials remain server-side. Provider errors are sanitized at the API boundary.

`GET /live/prices/{instrument}` accepts an optional historical `end` and selected chart timeframe. Calendar status is calculated after acquisition and retention. `analysis_ready: false` explicitly distinguishes these display inputs from validated research inputs, including when their dates fall inside calendar coverage. The existing analytical ingress, open-session gap checks, immutable revisions, replay and detector dispatch remain unchanged. No calendar version is extended or extrapolated.

Completed raw M1 prices are retained under `<data-root>/provider-prices/<account-feed-hash>/<UTC-date>.parquet`. The identity includes account/environment/source/symbol/normalization settings and excludes calendar version. Files contain canonical bar JSON and individual checksums. Writers serialize across processes, merge exact duplicates, reject conflicting completed data, fsync and atomically replace each daily file. Exact redelivery does not rewrite the file. There is no database migration and no publication of these files into the research revision catalogue. Portable backups include the new directory.

Price-only live subscriptions share a provider channel per feed, with limits of two feeds and twenty consumers. They reconnect lazily on first tab focus, acquire independently of DOM polling, and retain new completed minutes while a connected tab is hidden. Frequent polls refresh only the recent closes; initial/recovery requests refresh the bounded window. Acquisition stops when the last consumer disconnects. Repeated historical reads coalesce within a bounded cache; near-current historical ends are rechecked for delayed finalization, and retained prices survive historical refresh failures. Historical exploration does not register a live price subscription. Application shutdown closes channels and sources.

The UI requests startup prices independently of analytical preparation. Historical navigation starts a price request before preparing analytical history and can display its price-only result if preparation fails. Saved price-only historical windows restore independently of a published source, while explicit validated dataset views retain their existing behavior. Price buffers preserve older-price navigation and distinguish raw display data from validated inputs. Cancelled or superseded navigation cannot update another timeframe/tab scope; subscription effects own separate identities so delayed cleanup cannot disconnect a replacement consumer.

Higher-timeframe price-only previews are built from M1, include a partial bucket containing the requested period, are labelled provisional and explicitly incomplete; they cannot enter detector calculations. Calendar-aligned validated analytical aggregation is unchanged. A raw display window contains 1,440 source minutes, so higher-timeframe previews may contain fewer than the usual analytical chart buffer's 90 bars. A raw live preview refreshes completed closes; forming-candle display remains part of the validated live path.

## Verification

Authenticated read-only provider checks on 2026-11-08 used existing local credentials in memory and a temporary data root. No credentials, account identifiers, installed database or installed candle files were exposed or modified. Latest and historical requests (historical end 2026-11-08T00:00:00Z) each returned 1,440 completed candles for both instruments, with `calendar_status: unverified`, `analysis_ready: false` and no refresh error:

| Instrument | First returned M1 candle | Last returned M1 candle |
| --- | --- | --- |
| US30 | 2026-11-05T21:00:00Z | 2026-11-06T21:59:00Z |
| DAX | 2026-11-05T17:45:00Z | 2026-11-06T20:59:00Z |

These are the provider's last completed prices on the closed Sunday, not a claim of a current tradable quote or new verified calendar coverage. Ongoing close advancement was verified with deterministic provider tests; the authenticated check could not observe new Sunday market candles.

Validation results:

- Full Python regression run: **1,352 passed, 26 skipped**, 13m56s. The environment-dependent skips remain explicit; this run did not use the installed database. Two final price-window edge tests were added after that run's collection and passed separately.
- Final provider/cache/API/backup run: **51 passed**, including all 12 raw-price cases. Existing analytical live/ingress/retention checks also passed in the earlier targeted 102-test run. A final assertion verified that another completed minute becomes durable without a DOM read.
- Frontend unit tests: **76 passed**. Full browser run: **68 passed**; follow-up affected browser runs: **24 passed** and **7 passed**, including the added empty-response case. Across those runs, all 65 preexisting browser cases and all four new price-only cases passed. Empty older-price responses retain the loaded chart.
- Mypy over **128 source files**, Ruff, production build/typecheck, frontend lint and diff checks passed.

The final implementation also avoids provider requests for already retained local navigation, coalesces repeated historical requests, bounds higher-timeframe jumps to the requested period, and separates raw display evidence from validated detector markers. Existing calculated-marker and zoom preservation regressions pass. No independent agent review is claimed for this change.

Installed services were not rebuilt or restarted. Use `pnpm start` after pulling the delivered commits to rebuild the application normally.

Implementation commits: `119e55e` (provider acquisition/cache/API/backup) and `a47477e` (chart integration and browser regressions), on master. Tracked under SCRUM-137 with navigation follow-up in SCRUM-133.
