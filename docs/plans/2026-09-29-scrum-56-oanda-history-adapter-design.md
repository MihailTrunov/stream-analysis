# SCRUM-56 — OANDA historical candle adapter

The adapter implements the existing `HistoricalDataSource` contract. It does not
publish datasets or manage import jobs (SCRUM-57). It uses the exact canonical
instrument's `oanda`/environment symbol mapping, a caller-supplied account and
bearer token, and the account-scoped v20 historical candle endpoint. Credentials
never enter bars, metadata, logs, continuation tokens, or stored datasets.

## Choice and alternatives

Fetch by bounded UTC time windows, each shorter than the provider's 5,000-candle
limit. A count-based cursor is smaller, but sparse closures and inclusive `from`
semantics make continuation harder to reproduce. Fetching the whole requested
range violates the provider limit. The adapter translates canonical timeframes
to OANDA granularities, always requests `price=M`, `smooth=false`, RFC3339 UTC,
and returns canonical bars with `source_id=oanda-midpoint` and source metadata.
Midpoint is the agreed first research price component; it applies to every OHLC
field, not a blend of bid and ask candle fields.

The adapter advances through empty windows internally because a partial
`HistoricalDataPage` must contain bars. Its opaque continuation token binds the
request identity and the next window. Page boundaries may overlap by one candle:
an exact duplicate is dropped, whereas different values for the same timestamp
fail the page. It filters the result to the requested half-open range and drops
incomplete candles; it does not fill market gaps or infer holidays. A terminal
empty page means no completed candles were found in the remaining interval.

## Failures and verification

Transport timeouts, transient 5xx and HTTP 429 receive a bounded retry with
backoff, honoring a bounded `Retry-After` when present. Other HTTP failures,
exhausted retries, malformed JSON, wrong response instrument/granularity, bad
timestamps, invalid OHLC/volume, out-of-order/conflicting records, or a bad
continuation token fail explicitly as `ProviderError`; missing symbol mapping
fails as `MappingUnavailableError`. No raw OANDA model escapes the provider
package. Tests use an injected transport and local fixtures for sparse pages,
ordering, overlap, empty/completed filtering, normalization, and retry/failure
paths. No routine test needs a live OANDA account.

OANDA references: [account candles](https://developer.oanda.com/rest-live-v20/pricing-ep/),
[candle definition](https://developer.oanda.com/rest-live-v20/instrument-df/), and
[API limits](https://developer.oanda.com/rest-live-v20/development-guide/).
