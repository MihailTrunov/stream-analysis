# Stream Analysis

The supported local MVP startup is Docker Compose on a Mac with Docker running:

```sh
docker compose up --build
```

Open <http://127.0.0.1:5173> for the browser installation check and <http://127.0.0.1:8000/diagnostics> for the diagnostics API. PostgreSQL stays on the private Compose network. The `migrate` service runs the version-controlled Alembic migration before the API and workers start; application startup itself does not create or modify tables. Local data and logs live under `./data` by default (override with `STREAM_ANALYSIS_DATA_ROOT`). OANDA credentials are not required for startup; provider import is not implemented yet.

The seeded two-bar walkthrough is intentionally **not research-grade**. It checks that the browser can fetch canonical local bars, reveal them one at a time, and display service diagnostics. It does not calculate market state, run detectors, create events, or perform autonomous evaluation. Those features depend on later implementation Stories.

For development, use Node 22, pnpm 10.17.1, uv 0.10.0, and the pinned Python 3.13.7. Install with `pnpm install --frozen-lockfile` and `uv sync --locked --all-groups`; run `pnpm run ci` for lint, type checks, tests, and the web build. The PostgreSQL integration test runs when `STREAM_ANALYSIS_TEST_DATABASE_URL` points to a disposable PostgreSQL 16 database. Run `pnpm run smoke` for the Chrome browser check; it starts the local API and Vite server automatically. GitHub Actions performs these checks on Linux and installs Chrome for its smoke run.
