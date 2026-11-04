# Multi-timeframe workspace review — round 2 corrections

This follow-up addresses all four confirmed findings in the [Astra review](2026-11-03-multitimeframe-workspace-review-round-2.md). Detector formulas and provider acquisition are unchanged.

## Corrections

- **F1 — analytical clock:** sessions track finalized time independently of the last source candle. Aggregators advance through verified scheduled closures, including at initialization and historical/replay completion. Missing open minutes still fail rather than being filled. History prices and detector execution use the same closure rules.
- **F2 — missed-pattern attribution:** the review form explicitly selects a detector/timeframe target and uses actual completed candle boundaries. The server validates the pinned analytical configuration and aggregation manifest against the canonical M1 source revision. Saved/exported annotations retain both source and analytical timeframes, manifest identity and checksum. Replay intervals starting inside a higher-timeframe candle can review that candle after it closes.
- **F3 — initialized coverage:** each detector reports valid analysis bounds and initialization status. A historical window may be partially initialized or unavailable; the browser no longer equates successful execution with complete coverage. The first valid state becomes available at the close of the candle completing the required initialization count. Ongoing occurrences retain their earlier context without exposing uninitialized terminal results as valid detections.
- **F4 — result retention:** historical runtimes use a separate bounded LRU cache (default eight results), independent of the twenty replay slots. Eviction does not remove durable detector evidence, source manifests or annotation attribution. Historical event and missed-pattern reviews resolve immutable lineage after eviction. Replay reset can replace its own slot at capacity and preserves the original session if replacement fails.

The configuration preview now delegates runtime validation to the application layer, preserving the repository rule that transport code does not depend directly on detector implementations.

## Persistence

Migration `20261104_12` adds `analysis_annotation_lineage`, keyed by annotation ID. Legacy annotations and their export shape remain compatible. Historical cache eviction requires no database deletion. Apply the migration before using analytical missed-pattern reviews against an existing database.

## Verification

Focused regressions cover scheduled-close completion, missing-open-minute rejection, zero/partial initialization, both analytical stream orders, overlapping replay-start buckets, persisted/exported five-minute reviews, cache eviction with durable audit access, independent replay capacity and reset at capacity. The browser regression also waits for applied settings to reach local storage before intentionally reloading.

Final verification: **841 Python tests passed, 10 skipped**; **25 browser cases passed**; **26 web unit tests passed**. Web lint, TypeScript/production build, targeted Python Ruff/mypy and `git diff --check` passed. The skipped integration tests require an external PostgreSQL test database.

## Remaining delivery boundaries

The review's unconfirmed commit-failure recovery and simultaneous-event ordering concerns remain separate follow-ups; they were not among its four confirmed findings. Shared range acquisition/indexing (SCRUM-128), navigation beyond a pinned revision (SCRUM-133), provider live ingress and replay recovery after API restart remain planned work. No live provider acceptance or PostgreSQL integration result is claimed here.
