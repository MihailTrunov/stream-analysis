# SCRUM-57 — checkpointed historical acquisition

Decision date: 2026-09-29. Scope: acquire US30 and DAX one-minute bars from a
`HistoricalDataSource`, retain durable progress, and publish a validated,
immutable dataset revision. The owner confirmed that an unexpectedly stopped
worker must **not** auto-resume: the job becomes interrupted/failed, and the
researcher explicitly resumes it from its last durable checkpoint. An
exhausted provider or validation failure instead requires a new revision
attempt. This reconciles the Story's resume criterion with the MVP restart
policy in `CONTEXT.md`.

## Storage decision

Three options were considered. Keeping the entire import in PostgreSQL would
duplicate the Parquet store and inflate backups. Appending to a single
in-progress Parquet file makes crash-safe truncation/recovery difficult. Use
immutable, numbered staging batches under the configured data root. A batch is
written, flushed and linked under a content-addressed name before one PostgreSQL transaction
records its checksum, count, range and next source page token. A crash before
the transaction leaves an unreferenced file, never an advanced checkpoint; a
restart re-fetches that page. Referenced batches are verified before reading
or publication. Partial staged data is never a selectable dataset revision.

The provider-neutral application service drives the source one page at a time.
Exact cross-page duplicates collapse; conflicting duplicates fail. Empty
market spans and a final partial page work through the existing page contract.
The checkpoint records source metadata, durable count, last timestamp and
continuation token. Status is queryable independently of browser lifetime.

## Lifecycle and publication

A PostgreSQL job row owns immutable request identity and a generated revision
ID. A database constraint allows at most one queued or running import. Claiming a queued
or explicitly resumed interrupted job is transactional. A running worker
refreshes its heartbeat. Startup recovery marks stale running jobs interrupted;
it does not queue them. The API can create, inspect and explicitly resume jobs.
The import worker is the only process that performs provider requests and
Parquet publication; API/UI activity does not wait for acquisition.

When the source has no continuation token, validate the staged sequence and
calendar mapping, compute canonical/source checksums and gap observations,
then publish via `DatasetStore.publish` in a database transaction. The revision
becomes selectable only after publication commits. The import records requested
and actual half-open ranges, bar count, source, retrieval time, calendar and
normalization versions. A matching retry of a completed request returns the
existing job/revision by default; an explicit fresh attempt is required to
incorporate provider corrections. Credentials are worker-local environment
values and never enter job rows, staging files, logs or manifests. A one-way
account fingerprint pins the original account context across an interruption.

## Verification

Fake-source tests force interruption after a durable batch, restart the
service, explicitly resume and verify that the source starts at the checkpoint.
Further tests cover uncommitted files, duplicate/conflicting boundaries,
empty windows, final partial pages, completed-request idempotence, checksum
tampering, and publication only after completion. PostgreSQL integration runs
in CI; live OANDA acquisition remains a separate manual verification.
