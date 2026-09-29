# Stream Analysis

The supported local MVP startup is Docker Compose on a Mac with Docker running:

```sh
docker compose up --build
```

Open <http://127.0.0.1:5173> for the browser installation check and <http://127.0.0.1:8000/diagnostics> for the diagnostics API. PostgreSQL stays on the private Compose network, with live database files in a Docker-managed `postgres_data` volume. The `migrate` service runs the version-controlled Alembic migration before the API and workers start; application startup itself does not create or modify tables. Datasets, artifacts, exports and logs live under `./data` by default (override with `STREAM_ANALYSIS_DATA_ROOT`). OANDA credentials are not required for startup; provider import is not implemented yet.

The seeded two-bar walkthrough is intentionally **not research-grade**. It checks that the browser can fetch canonical local bars, reveal them one at a time, and display service diagnostics. It does not calculate market state, run detectors, create events, or perform autonomous evaluation. Those features depend on later implementation Stories.

For development, use Node 22, pnpm 10.17.1, uv 0.10.0, and the pinned Python 3.13.7. Install with `pnpm install --frozen-lockfile` and `uv sync --locked --all-groups`; run `pnpm run ci` for lint, type checks, tests, and the web build. The PostgreSQL integration test runs when `STREAM_ANALYSIS_TEST_DATABASE_URL` points to a disposable PostgreSQL 16 database. Run `pnpm run smoke` for the Chrome browser check; it starts the local API and Vite server automatically. GitHub Actions performs these checks on Linux and installs Chrome for its smoke run.

## Local research-data backup and restore

Backups are manual. Stop the application stack, then start **only** PostgreSQL and create a new archive outside the data root:

```sh
docker compose stop api evaluation-worker import-worker web
docker compose up -d --wait postgres
pnpm nx run platform:backup
```

The archive defaults to a UTC-stamped file under ignored `backups/`; pass a specific path after `--` if desired. It contains a PostgreSQL custom-format dump, immutable datasets, artifacts and exports, with a versioned checksum manifest. It does not contain the live PostgreSQL volume, `.env`, or credentials. Copying `./data` alone is **not** a complete backup. Keep the archive outside the data root and store it securely. `docker compose down` preserves the named volume; `docker compose down --volumes` deletes it.

To restore, stop the old stack and select a **new, empty** `STREAM_ANALYSIS_DATA_ROOT` (or move the old installation aside). Do not use a path containing existing research data. Then run:

```sh
COMPOSE_PROJECT_NAME=streamanalysis-restored STREAM_ANALYSIS_DATA_ROOT=/path/to/empty-data-root pnpm nx run platform:restore -- /path/to/backup.zip
```

Use a unique `COMPOSE_PROJECT_NAME` for a fresh installation: Compose scopes its PostgreSQL volume to that name, not to the data-root path. Restore verifies all archive members before extraction, starts PostgreSQL only, refuses a nonempty target database, restores the dump, then checks every database-referenced dataset manifest and Parquet checksum. A failed restore leaves the target untouched or partially restored for inspection; it never overwrites an existing installation. After success, start the application with the **same** `COMPOSE_PROJECT_NAME` and `STREAM_ANALYSIS_DATA_ROOT`. Older dataset formats are not accepted until an explicit compatibility reader is implemented.

The previous bind-mounted `./data/postgres` directory is left untouched and is **not** used by the new Compose configuration. Do not delete it until you have separately confirmed its contents are no longer needed or migrated them. Switching to a named volume does not automatically import that database.
