# SCRUM-71 — OANDA UK/live session calendars

Decision and verification date: 2026-09-28. Scope: completing the provider-calendar alignment for **SCRUM-71**, not a full historical import or a complete exchange holiday database.

## Approved research scope

The researcher confirmed an OANDA **UK/live** account, full provider sessions, and an initial target of the last three years of **M1** data for both instruments. Authenticated instrument discovery confirmed the exact symbols `US30_USD` (US Wall St30) and `DE30_EUR` (Germany30). They map to canonical `US30` and `DAX` respectively. The public website now calls the German index Germany 40; that is not permission to rename the account's API symbol or guess a different instrument mapping.

Acquire the full available session. Narrow London/pre-US/US-open/late-session research selection remains an application concern; it must not discard earlier bars needed to initialize analytical state. Existing configurable named `SessionWindow` values remain supported. The initial provider profile uses one named `provider` window rather than inventing new research segmentation rules. The three-year acquisition target is separate from the existing one-year evaluation benchmark.

## Architecture and alternatives

Use finite, immutable provider schedules shipped as configuration, interpreted by the existing generic calendar engine. This preserves offline reproducibility and calendar-version lineage. Fetching current web hours at runtime would not reproduce historical schedules; substituting a generic US/German public-holiday list would incorrectly close instruments that trade on those holidays. Neither alternative is used.

`config/oanda_uk_calendars.py` provides immutable profiles and `build_oanda_uk_calendar`. Callers supply the exact verified provider/region/environment, account binding and provider symbol. The factory validates supported context; it does **not** authenticate or determine an arbitrary account's region. Import/account verification belongs to its own integration boundary. `CalendarRegistry` resolves the resulting exact provider/account/canonical-instrument identity without a fallback.

`TradingCalendar` derives `SessionState` from the original aware UTC timestamp, local IANA time, an analytical start date, the applicable dated override and half-open windows. `SessionComponent` consumes the pinned calendar/configuration identity and remains deterministic/resettable. Ordinary weekday closures, scheduled breaks and full dated holiday closures are distinct. Explicit exceptions may reopen an otherwise closed date.

The configuration module never reads `.env`, contacts OANDA, or stores a real account identifier. Provider-specific schedules stay out of detector logic and the generic domain engine. The exception index is immutable and supports constant-time date lookup, avoiding a scan through hundreds of dated DST overrides for every minute bar.

## Verified M1 boundary conventions

| Canonical instrument / API symbol | IANA zone | Analytical boundary and label | Normal local M1 window | Open analytical-start weekdays |
| --- | --- | --- | --- | --- |
| US30 / `US30_USD` | `America/Chicago` | 17:00; label is the **start** civil date | [17:00, next-day 16:00) | Sunday–Thursday |
| DAX / `DE30_EUR` | `Europe/Berlin` | 00:00; label is the civil date | Winter [01:15, 22:00); summer [02:15, 22:00) | Monday–Friday |

US30's Friday morning belongs to Thursday's analytical session; Sunday evening belongs to Sunday, not Monday. Its daily break is [16:00, 17:00). DE30 has a morning break from midnight to the seasonal opening and an evening break [22:00, next midnight). Its opening is 00:15 UTC in both seasons, while its closing follows Berlin's local 22:00. Summer opening overrides are finite dated weekdays derived using IANA DST at local noon; they do not reopen weekends. Closed holiday overrides take precedence.

The provider's published opening minutes are 17:01 Chicago for US30 and 01:16 Frankfurt for Germany (one hour later during DST). The authenticated completed M1 candles demonstrate interval starts in the **preceding minute**, 17:00 and 01:15/02:15 respectively. M1 candle time identifies the interval start; these observations do not establish the exact first tick time. End times are exclusive: a last 15:59 candle belongs to the US window ending at 16:00. Preserve those original UTC timestamps; never trim, shift or synthesize a candle to match prose trading hours.

Occasionally the first/last nominal minute has no candle. This does not change the recurring window: missing ticks inside otherwise expected-open minutes remain data-gap observations. OANDA's daily-candle aggregation `dailyAlignment` setting is not a replacement for these M1 instrument-session conventions. Outcome Stories consume this analytical-date/cutoff identity rather than browser midnight, and analytical state is not reset at that cutoff.

## Dated historical exceptions

US early-close entries below name the **civil end date**. Their `CalendarException.trading_date` is the preceding day. On early-close dates the scheduled break begins at the early cutoff and lasts until the next 17:00 boundary.

| US civil end dates | Chicago exclusive close |
| --- | --- |
| 2023-11-23 | 12:00 |
| 2024-01-15, 02-19, 05-27, 06-19, 07-04, 09-02, 11-28 | 12:00 |
| 2025-01-20, 02-17, 05-26, 06-19, 07-04, 09-01, 11-27 | 12:00 |
| 2026-01-19, 02-16, 05-25, 06-19, 07-03, 09-07 | 12:00 |
| 2023-11-24; 2024-07-03, 11-29, 12-24; 2025-07-03, 11-28, 12-24 | 12:15 |
| 2026-04-03 | 08:15 |

US full closures, expressed as **analytical start dates**: 2023-12-24, 2023-12-31, 2024-03-28, 2024-12-24, 2024-12-31, 2025-04-17, 2025-12-24, 2025-12-31. Closing the Christmas/New Year overnight session does not close the following civil day's 17:00 reopening. Good Friday was fully closed in the sampled 2024/2025 US history, but **2026 has a shortened session**; do not extrapolate one yearly recurrence across all years.

DE full closures, expressed as **civil/analytical dates**:

- 2023: 12-25, 12-26.
- 2024: 01-01, 03-29, 04-01, 05-01, 12-24, 12-25, 12-26, 12-31.
- 2025: 01-01, 04-18, 04-21, 05-01, 12-24, 12-25, 12-26, 12-31.
- 2026: 01-01, 04-03, 04-06, 05-01.

US national holidays are not general DE closures, nor is every German public holiday a provider closure. The 2026 DE notices expressed in Eastern time for April 2/30 convert to the regular 22:00 Berlin cutoff; Easter Monday's late Eastern reopening is Tuesday's normal Berlin opening, not a special Monday window.

## Coverage, failure behavior and maintenance

Profile version: `oanda-uk-live-m1-2026-09-28-v1`. Inclusive **analytical start-date** coverage: **2023-09-27 through 2026-09-28**. Starting on September 27 allows the overnight US session containing September 28 UTC to be represented. End-date inclusion covers the entire last analytical session, including its next-civil-day portion; it does not claim those future candles have already been acquired.

Unknown provider/region/environment, unknown exact symbols, missing account binding, unknown registry mappings and timestamps outside coverage fail explicitly. No generic calendar or silent historical/future extrapolation is permitted. This finite schedule is an initial verified MVP profile, not a claim that every missing minute across three years has been audited or that a perfect exchange holiday master exists.

Before extending coverage or supporting a changed provider schedule, manually verify account/instrument context, published hours/notices and representative completed M1 intervals, including DST mismatch weeks and relevant holiday boundaries. Add the dated exceptions, update coverage, issue a **new** calendar version, add fixtures and review the change. Preserve the old version/configuration for runs already pinned to it; extending a rolling import is not permission to mutate old run lineage.

## Verification evidence and sources

The [sanitized manual observations](../research/2026-09-28-oanda-uk-calendar-observations.json) retain **118** bounded requests: 32 regular/DST checks and 86 holiday checks across the two symbols, sampled from 2023–2026. All requested responses succeeded. Each record contains only completed-candle counts and UTC/local timestamp envelopes. No token, real account ID, OHLC response, account financial data, or raw provider payload is retained. Credentials were used in memory for read-only diagnostics.

An envelope is not proof that every enclosed minute traded. Small within-session gaps were compacted for readability; larger missing intervals remain observations. In particular, the long US30 gap overnight into 2025-11-28 is **not** encoded as a scheduled closure. A contemporaneous CME incident corroborates an outage-like event, but it does not justify inventing an OANDA calendar exception. Import gap classification remains a separate Story.

Primary references checked on the verification date:

- [OANDA UK hours of operation](https://www.oanda.com/uk-en/trading/hours-of-operation/): instrument zones, regular hours and Germany's seasonal opening shift.
- [OANDA UK index CFDs](https://help.oanda.com/uk/en/faqs/index-cfds.htm): UK product context and reference to the official hours.
- [OANDA UK holiday trading hours](https://www.oanda.com/uk-en/trading/holiday-trading-hours/): current 2025/2026 notices, including time-zone conversions; this page is **not** a 2023/2024 archive or a complete source for every historical exception listed above.
- [OANDA v20 account instrument candles](https://developer.oanda.com/rest-live-v20/pricing-ep/): the read-only account-scoped endpoint used for the bounded checks.
- [OANDA candle definitions](https://developer.oanda.com/rest-live-v20/instrument-df/): completed candles, interval-start timestamps and minute-aligned M1 granularity.
- [CME 2025-11-28 incident notice](https://www.cmegroup.com/market-data/cme-group-benchmark-administration/files/28-november-2025-cme-group-intraday-indices-cvol-and-petroleum.pdf): corroboration only for leaving the unusual outage-like absence out of the scheduled calendar.

Historical exceptions are finite manual findings from authenticated interval observations checked against available current notices, not invented past notices or a substituted exchange calendar. Tiny missing first/last minutes remain unexpected-gap candidates, not early-close rules.

## Acceptance and integration boundary

The SCRUM-71 checks cover exact M1 opening/closing instants and elapsed time, UTC/source preservation, winter/summer and US/European DST mismatch weeks, weekends and Sunday reopening, break/holiday distinction, dated early/full closures, immutable identity/version/mappings, inclusive coverage rejection, arbitrary named research windows and component reset determinism. Independent review and the completed validation results are recorded in the implementation commit and Jira closure comment.

Implementation used GPT-6-Sol at medium effort; independent read-only review used GPT-6-Sol at x-high effort and found no actionable findings. The focused suite passed **61 tests** and targeted Ruff passed. The reviewer independently checked all 118 observation records / 218 envelopes: **436** observed first/last-minute classifications, **436** UTC/local conversions, and **228,059** nominal envelope minutes matched the profile. This tests consistency with the retained envelopes; it does not turn compacted gaps into evidence of actual candles. A generic-window audit also passed **324** valid mappings and **1,296** exact-boundary/elapsed checks.

The uncached local Nx run passed Python tests (**521 passed, 2 database tests skipped**), Python type checks, and web lint/type checks/tests/build. That local tree includes unrelated uncommitted MarketState work: its `tests/unit/test_market_state.py:660` formatting error prevented the whole Nx command from being green. Those files were preserved and excluded from this Story's commit; Ruff passed across the remaining source/tests/migrations. The hosted workflow for the exact pushed commit supplies clean-tree PostgreSQL and browser validation; its result/link is recorded in Jira, not inferred from this local run.

Future import/runtime/segmentation/outcome Stories assemble these profiles through the public factory and registry, retain the pinned version in lineage, and consume the same conventions. Expected-slot iteration, full three-year acquisition, complete gap audit, outcome calculations and browser research-window controls are **not** silently delivered by this calendar-only completion. SCRUM-122 manual fresh-checkout acceptance is unchanged.
