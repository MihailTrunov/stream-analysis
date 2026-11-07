# Normal live startup correction

The installed stack had no provider credentials loaded because the startup wrapper did not select the existing ignored `app/.env`. Disabling demo seeding also left retained synthetic revisions selectable, and browser restoration could reopen a synthetic history tab. These were separate startup problems.

The wrapper now uses `app/.env` automatically when there is no root `.env` and no explicit `--env-file`. Root and explicit selections retain precedence. It exports demo seeding as disabled by default; `pnpm start:demo` or an explicit shell environment setting enables it. Credential contents are neither logged nor copied.

The API diagnostics declare whether demo mode is enabled. Normal startup filters synthetic revisions and synthetic active replay sessions from the chart workspace. A fresh normal workspace opens live mode for the last selected instrument. Genuine saved historical/live tabs and focused selection are preserved, including explicitly disconnected tabs. Live connections continue to initialize only when their tab is focused.

Demo mode retains the original browser storage key. Normal mode uses `stream-analysis.workspace.live.v1`, migrating genuine historical/live tabs from the original key only when the normal snapshot is absent. A demo-only snapshot becomes a fresh live tab; its original snapshot and all installed data remain intact. Saved live tabs whose bootstrap source is synthetic discard that bootstrap reference before connecting.

Verification: 10 focused Python startup/lifecycle tests, 54 web unit tests, web typecheck/lint and focused Ruff checks passed. Three isolated Chrome startup tests verify demo exclusion and storage preservation, focus-only preparation of restored live tabs, and restoration of genuine history. They deliberately mock the provider endpoints; they do not establish a real provider connection. Full isolated Chrome regression: **59 passed** (3.7 minutes). Mypy passed across 127 source files; shell syntax and diff checks passed.

The installed stack has not been restarted or modified during verification. Re-run `pnpm start` to rebuild/recreate its containers with the selected local environment. This fixes configuration loading; a successful real provider subscription is a separate runtime check.
