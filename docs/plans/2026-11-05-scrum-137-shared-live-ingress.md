# SCRUM-137 — shared validated live ingress

## Delivery boundary

This increment implements shared acquisition and its local subscription API. It builds on SCRUM-136's completed-minute journal. Live analytical run/input lineage, detector-session attachment, browser mode/restoration controls and live-follow interaction are the next integration stage. SCRUM-137 remains In Progress; the Live workspace button is not enabled by this increment.

The existing OANDA UK calendars are finite and verified through analytical date 2026-09-28. Current-date subscription attempts outside that coverage fail before opening a provider connection. A verified versioned calendar extension is required for operation on 2026-11-05; recurring trading hours and holiday rules are not silently extrapolated. Tests use covered historical instants with fake transports and clocks.

## Provider completion and forming data

[OANDA's candle definition](https://developer.oanda.com/rest-live-v20/instrument-df/) exposes an explicit completion flag. Its [pricing endpoints](https://developer.oanda.com/rest-live-v20/pricing-ep/) distinguish prices and completed candles. This implementation polls authoritative midpoint M1 candles rather than reconstructing authoritative OHLC from sampled price-stream messages. The initial transport is server-side candle polling; no tick archive or quote-stream OHLC is introduced.

`LiveCandleSource` is provider-neutral and returns a terminal completed-M1 page plus optional forming display data. The OANDA adapter reuses existing account-scoped historical normalization, midpoint/smoothing rules, retries and redacted transport errors. It requests latest forming candles separately. A timestamp passing its close does not override a false provider completion flag. Future, duplicate, mismatched or malformed forming envelopes are rejected.

Forming data has `is_complete=false`, remains separate from durable prices and never advances the confirmed watermark. Analytical consumers must read the subscription's confirmed prefix rather than treating the forming candle as finalized input. No detector formulas or alternate analytical runtime are introduced.

## Shared acquisition and API

`LiveIngress` owns one polling thread/client per exact feed identity, shared across at most twenty subscriptions and two feed identities in this local deployment. Acquisition advances independently of state/price reads; a hidden client does not need to poll or render to keep an already-connected feed running. Per-feed polling locks serialize acquisition. Separate short snapshot locks let status requests complete while provider HTTP is blocked.

The API is local and server-configured:

- `POST /live/subscriptions`: UUID subscription identity, instrument and optional aligned catch-up start. Account fingerprint, price basis, calendar and credentials are resolved server-side. Configuration must be UK/live and account-scoped instrument verification occurs before prices are requested.
- `GET /live/subscriptions/{id}`: connection/status, confirmed watermark, last successful poll and separate forming candle.
- `GET /live/subscriptions/{id}/bars`: at most 500 durable completed M1 bars inside that subscription's confirmed prefix.
- `DELETE /live/subscriptions/{id}`: disconnect this consumer; the last disconnect stops acquisition and closes its provider client. API shutdown stops all channels.

Exact resubscription is idempotent. Replacing an existing subscription's feed/start requires explicit disconnect. Catch-up starts are limited to the preceding day, with at most one source hour per poll and a one-minute overlap to detect changed previously completed data. Joining before a shared feed's original start requires separate history preparation. These bounds prevent an accidental broad download on a live connection.

Every completed minute is committed through `retain_completed_minute` before the feed publishes its new watermark. Acquisition may retain later received minutes across a gap, but subscription reads cannot expose them as a contiguous confirmed prefix. Missing expected-open minutes produce a gap at the first missing minute; later polls repair it. Verified closed minutes can advance without fabricated bars. Conflicting redelivery freezes the feed until explicit disconnect/recovery; it does not silently retry a correction into the same identity. Failed transport retries from the retained watermark, with raw exception/URL/token/account values omitted from API state.

Statuses are connecting, catching_up, connected, gap, failed, conflict and stale. A previously connected feed becomes stale after fifteen seconds without a successful poll. Polls run every two seconds after the previous bounded request finishes. Provider latency can delay delivery; no response-time guarantee is claimed.

Subscription ownership is deliberately in memory. No channels are reconstructed from the durable journal at API startup. The browser integration must persist tab intent and reconnect only the initially focused live tab; other restored tabs connect on first focus. Completed journal data remains durable across these connection lifecycles. A subscription is an acquisition consumer, not yet an analytical session or a reproducible run.

## Architecture decision and next integration

Server-side polling reuses the provider's canonical completion/normalization boundary and supports both catch-up and current data. A price stream can offer faster forming-price updates but cannot replace the authoritative candle path. A distributed worker/broker would add ownership and lease machinery beyond the supported single-process local API; revisit this choice before multiple API workers/processes are supported.

Live session attachment must retain initialized source history plus the exact captured feed/minute lineage for every analytical stream. An initial historical dataset revision alone cannot attribute later live detector inputs. Resolve that manifest/attachment boundary before using live ingress to create persisted detector evidence. Then reuse existing candle aggregation and close-only dispatch, apply per-tab detector settings, and connect focused-tab view polling while hidden live sessions keep processing. Complete live-follow acceptance under SCRUM-133 alongside that UI stage. Current-calendar verification and actual account/provider acceptance remain distinct from deterministic CI tests.

## Verification

Focused tests cover shared subscribers, completed-versus-forming separation, background acquisition without client reads, exact source ownership, current calendar rejection, explicit disconnect/reconnect, gap repair, overlap conflict detection, credential redaction, transport retry, stale state, bounded catch-up, real thread shutdown, nonblocking status reads and the subscription API. Adapter tests use `httpx.MockTransport`; no real provider or credentials are used.

Final verification: **970 Python tests passed, 11 skipped**; **45 focused ingress/provider tests passed**, with the strengthened bounded catch-up scenario also rerun successfully. Ruff, full backend mypy (88 source files) and `git diff --check` passed. Browser code is unchanged and browser suites were not rerun for this backend increment. These tests do not establish live detector parity, tab/DOM behavior, multiple API-process ownership or actual provider/calendar acceptance; those remain explicit follow-up work.

No schema migration beyond the existing SCRUM-136 prerequisite `20261105_14` is needed. Existing installation state is not migrated or connected by this task.

## Follow-up delivery — 2026-11-06

[Live analytical sessions and chart integration](2026-11-06-scrum-137-live-analysis-workspace.md) now implements the input lineage, detector/tab attachment and live-follow work described above. This document preserves the earlier increment and its test evidence. The subsequent [provider/calendar acceptance](2026-11-06-scrum-137-provider-acceptance.md) verifies real US30/DAX processing and a new finite calendar through 2026-11-06.
