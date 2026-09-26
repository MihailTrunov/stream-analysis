# Five-story implementation review — 2026-09-26

## Review scope

This records the findings from the initial review on 2026-09-26, before the requested second pass. The implementation scope is the five Jira Stories `SCRUM-122`, `SCRUM-54`, `SCRUM-78`, `SCRUM-105`, and `SCRUM-112`, plus their three follow-up CI-fix commits. The exact Git range is `1229aab..bc624c6` (commits `5140444`, `ed5d43b`, `35c435d`, `00fd4b6`, `1de4c8d`, `5f11915`, `b7367f1`, and `bc624c6`). Findings concern changes in that range, assessed against the Story contracts, `CONTEXT.md`, `docs/mvp-reconciliation-plan.md`, and the linked Charts specifications. This was a review, not an implementation change or a claim that the Stories are complete.

## Findings from the initial pass

1. **High — `SCRUM-122`: the required offline installation smoke path is absent.** The browser asset is a one-line static page (`web/index.html`), the workers only sleep (`src/market_analysis/application/worker.py`), and API diagnostics report configured/expected service flags rather than verifying actual availability or schema readiness (`src/market_analysis/api/app.py`). The Story calls for a seeded, demo-backed local startup and walkthrough/evaluation smoke path; the scaffold does not yet demonstrate it.
2. **High — `SCRUM-122`: reproducible toolchain and CI claims exceed what is implemented.** `pnpm-lock.yaml` has no resolved package dependency graph; `package.json` invokes Nx via `pnpm dlx`; `requirements.lock` contains direct pins rather than a complete locked dependency graph; and the pin test checks package names but not exact versions. `.github/workflows/ci.yml` runs backend pytest, Ruff, and mypy, but no frontend build, PostgreSQL integration, or browser smoke check.
3. **High — `SCRUM-105`: configuration identity can omit effective defaults and run context.** `PatternSelection.from_definition` expands pattern defaults, but directly constructing `PatternSelection` can retain empty parameters. These paths can hash differently despite representing the same effective configuration. The initial implementation also does not yet define/persist a complete run snapshot including calendar version and other required context (`src/market_analysis/config/models.py`).
4. **High — `SCRUM-78`: pattern transition and version contracts are inconsistent.** Validation allows two outgoing transitions from one state with the same trigger and different target states, leaving runtime behavior ambiguous. The registry also rejects a same-version default-only change, while the Story permits default changes without necessarily bumping the semantic version when the configuration/hash changes (`src/market_analysis/patterns/definition.py`; `tests/unit/test_pattern_definition.py`).
5. **Medium — `SCRUM-54`: canonical `Bar` normalization and completeness validation are too loose.** Numerically equal `Decimal` values such as `1.0` and `1.00` compare equal but serialize differently, affecting canonical identity; `is_complete="no"` is accepted rather than rejected as a non-Boolean value (`src/market_analysis/domain/market_data.py`).
6. **Medium — `SCRUM-112`: logging is not connected to the runtime.** The structured-logging helper is implemented, but neither the API nor workers use it, so it does not yet yield the required operational trace. Its arbitrary-object fallback uses `str(value)`, which is not guaranteed to be deterministic or safe to log (`src/market_analysis/application/logging.py`).

## Verification and limits of the initial pass

The initial pass reported 29 passing pytest tests, passing Ruff, passing mypy over 19 source files, and a passing `docker compose config --quiet`. It did not start the Compose stack, exercise a browser walkthrough, or observe a GitHub Actions run. The five Jira Stories were still in `To Do` at the time of that review. The linked Technical Architecture document contained some older bars-table and in-process-batch wording, but its later reconciliation section explicitly superseded that wording; this is a document clarity risk, not evidence that the implementation should follow the older design.

Relevant Story links: [SCRUM-122](https://mihailtrunov.atlassian.net/browse/SCRUM-122), [SCRUM-54](https://mihailtrunov.atlassian.net/browse/SCRUM-54), [SCRUM-78](https://mihailtrunov.atlassian.net/browse/SCRUM-78), [SCRUM-105](https://mihailtrunov.atlassian.net/browse/SCRUM-105), and [SCRUM-112](https://mihailtrunov.atlassian.net/browse/SCRUM-112). Linked [Technical Architecture & Engineering Principles](https://docs.google.com/document/d/1RIGUQQHJCGvepXnlZc4Pm7fuoDiAR-REWVAgZZQwFGA/edit).

## Independent second pass — 2026-09-26

Review scope remained the same eight commits, five Stories, and local/linked specifications above. This pass used the repository's `.agents/skills/code-review/SKILL.md` and a review-only Codex invocation with `gpt-6-sol` at `xhigh`; the reviewer examined the full changed files and surrounding contracts. The Jira descriptions and linked Charts Technical Architecture and Core Domain Model documents were re-read on 2026-09-26. The model did not edit repository files. Findings below are presented after independent validation, not copied uncritically from its output.

### New or more specific findings

1. **High — Nx Python targets are likely unable to start on a clean installation.** `pyproject.toml` sets `tool.uv.package = true` and declares Hatchling, but the distribution is `stream-analysis` while the sole package is `src/market_analysis`; no Hatch wheel package selection is configured. `uv run` builds/installs the project before executing an Nx command. Hatchling's documented default package-name heuristics do not match this tree, so the supported `platform:test`/`lint`/`typecheck`/`ci` path will fail at package build. Configure `[tool.hatch.build.targets.wheel] packages = ["src/market_analysis"]`, then prove `uv run` works from a fresh checkout. This is supported by [uv project packaging](https://docs.astral.sh/uv/concepts/projects/config/) and [Hatchling wheel-selection rules](https://hatch.pypa.io/latest/plugins/builder/wheel/); a clean build was not executable in this environment.
2. **Medium — distinct typed configuration values can share one canonical JSON.** `_canonical_value` in `src/market_analysis/config/models.py` converts `Decimal("1.20")` to the JSON string `"1.20"`, indistinguishable from an actual string-valued parameter. Since the models accept both types without binding every selection to its typed schema, downstream hashes can collide for behaviorally different values. Preserve type information or enforce schema-specific types before hashing.
3. **Medium — the default runtime data root is not ignored by Git.** `.env.example` and Compose use `./data`, but `.gitignore` does not cover `data/` (`git check-ignore` returns no match). Starting the stack can make PostgreSQL files, logs, and research artifacts available to an accidental `git add -A`. Ignore the default root while preserving intentionally committed fixtures.
4. **Medium — the bundled demo fixture is incompatible with the canonical Bar loader.** Both rows in `tests/fixtures/demo_bars.json` omit `source_id` and `is_complete`; `Bar.from_canonical_dict` raises `DomainValidationError: source_id must be a string` on the first row. Fix the fixture or provide an explicit normalization loader, and exercise it in the offline smoke test.
5. **Medium — log permissions weaken after rotation.** `configure_logging` applies `chmod 0600` only to the initial `application.jsonl`. Under a normal `022` umask, `RotatingFileHandler` reopens the active log as `0644` after rollover; this was reproduced using the actual logger. Enforce private mode for every replacement file, not only the first one.
6. **Medium — `Bar.is_complete` is not validated on direct construction.** `Bar(..., is_complete="false")` succeeds, is truthy to consumers, and emits a non-Boolean canonical payload that `Bar.from_canonical_dict` rejects. Require an actual Boolean in `Bar.__post_init__`.
7. **Medium — the default Python dependency graph is not frozen end-to-end.** `requirements.lock` pins only direct dependencies; Docker/CI install it with `uv pip`, while Nx uses `uv run` from `pyproject.toml`. These paths can resolve different transitive versions for one checkout. Use one full lock/installation policy and verify declaration/lock consistency. The frontend Nx `pnpm dlx` path remains outside the empty `pnpm-lock.yaml` as noted in the initial pass.
8. **Medium — transition ambiguity is executable, not just a specification concern.** A minimal `PatternDefinition` with two transitions from `idle` on the same `go` trigger to two different target states constructs successfully; so does a definition with competing triggers and no precedence. The contract should reject ambiguity or explicitly encode deterministic multi-transition behavior.
9. **Low/medium — Instrument equality conflicts with its canonical serialization.** Two `Instrument` values with the same provider mappings in opposite tuple order compare unequal and have different hashes, but serialize to the same canonical dictionary because serialization sorts mappings. Normalize mapping order at construction if mapping order is not semantic.

### Disposition of initial findings

The second pass confirms the missing `SCRUM-122` smoke path/diagnostics and incomplete CI/toolchain scope; the demo fixture and Hatchling issues sharpen this into concrete installation failures. It also confirms the `SCRUM-54` Bar canonical/Boolean defects, unresolved direct `PatternSelection` construction under `SCRUM-105`, `SCRUM-78` transition ambiguity, and unwired `SCRUM-112` logging. The initial claim that registry rejection of a same-version **default-only** definition change is independently a Story violation should be treated as a design question, not a confirmed bug: values may instead vary through resolved configuration overrides. The stronger requirement is to ensure every run persists its fully resolved typed parameters and identifies the code-defined semantic contract.

### Verification limits of the second pass

Pure-Python probes reproduced Bar equality-versus-serialization, invalid completion flags, the invalid demo fixture, transition ambiguity, Instrument equality-versus-serialization, and post-rotation log mode `0644`; `git check-ignore` confirmed the default data root is uncovered. The model review completed successfully with `gpt-6-sol` / `xhigh`. Full pytest/Ruff/mypy and a clean `uv run` or Compose startup were **not** rerun: this sandbox lacked the pinned Python 3.13.5 installation and access to the necessary uv cache/dependencies. Consequently, the Hatchling failure is strongly supported by the declared configuration and official build rules but remains unverified by a clean build here. No GitHub Actions execution was observed.

### Follow-up on 2026-09-26

The exact local Python pin was changed from 3.13.5 to the available 3.13.7. `uv run pytest` then directly confirmed the Hatchling package-selection failure described above. Adding `packages = ["src/market_analysis"]` under `[tool.hatch.build.targets.wheel]` let the project build, after which `uv run pytest` passed all 29 tests. uv generated `uv.lock` during this run. This resolves the local Hatchling build blocker; the other findings above are not resolved by this follow-up.

### Defect-fix sequence on 2026-09-26

The isolated defects from the second pass and the concrete domain/transition defects from the initial pass were fixed in separate commits:

| Finding | Commit |
| --- | --- |
| Local Python 3.13.7 pin | `51d4348` |
| Hatchling package selection | `dafcd7e` |
| Typed configuration-value collision | `a6aeab0` |
| Default runtime data root not Git-ignored | `fd04bbb` |
| Unloadable demo Bar fixture | `21534ac` |
| Log permissions after rotation | `a7c0d2e` |
| Non-Boolean Bar completion accepted | `73b6d01` |
| Incomplete Python dependency graph/install path | `8bcea71` |
| Ambiguous PatternDefinition transitions | `1a798fe` |
| Instrument provider-mapping order mismatch | `3fe9a41` |
| Equal Bar Decimal values serialized differently | `39b74ea` |
| Unlocked Nx task-runner installation | `e311e77` |

`pnpm exec nx run platform:ci` passed after these fixes: 41 tests, Ruff, and mypy over 19 source files. A frozen pnpm install and a hash-verified `uv pip` dry run also passed. These commits do **not** complete the broader Story-level gaps below: `SCRUM-122` still lacks a working offline walkthrough/evaluation smoke path and real DB/schema/worker diagnostics; CI still lacks frontend, PostgreSQL integration, and browser smoke checks; `SCRUM-105` still needs enforced fully resolved run snapshots and persistence; and `SCRUM-112` still needs logging integrated into actual replay/evaluation work. The same-version default-only registry concern remains a design question, not a confirmed defect.
