# Claude review: evaluation documentation structure

Reviewer: Claude Code (main session, glm-5.3), independent reviewer. Scope per
the [review brief](2026-11-08-review-brief-doc-structure.md). Proposal-only:
no canonical document was modified.

Reviewed state: current master (`be478a2`). Note: the architecture document
moved during the provider-chart deliveries; the SCRUM-95 paragraph is now at
`docs/core/technical-architecture.md:351-353` (the brief's `:329` anchor is
stale), and the README evaluation paragraphs are at `README.md:60-64`.

## Verdict

**Proposal produced; two isolation-exposed ambiguities flagged** for an owner
decision during restructuring. No normative content changes are proposed.

## 1. Proposed split of the SCRUM-95 paragraph (architecture doc)

The single paragraph at `:351-353` decomposes cleanly by delivery, with one
sentence group per story plus a cross-cutting tail. Proposed replacement —
same section, introduction line kept, then one short paragraph per story in
the document's existing amendment style (content preserved verbatim unless
flagged below):

- **SCRUM-95** — first sentence group (immutable multi-stream inputs, v2
  predicates with unchanged v1 hashes, the six states, per-stream/required-
  outcome completion gates, "models do not imply a working queue scheduler")
  + its contract link.
- **SCRUM-96** — dedicated-executor sentence (shared path, bounded chunks,
  durable progress, incomplete output preserved on cancellation/failure) +
  link.
- **SCRUM-123** — queue sentences (default service claims jobs, token
  ownership, heartbeats, pause-after-cancellation/recovery, reorder/cancel
  without changing frozen settings) + link. *Flagged:* the following sentence
  "The queue supports detector-only plans and the registered forward-return
  outcomes..." reads as current-state summary but predates SCRUM-98/99/100's
  registrations; when isolated it contradicts the later family paragraphs.
  Proposal: keep the sentence verbatim under a "Queue submission scope at
  SCRUM-97 delivery" framing or move its generic part ("detector-only plans
  and registered outcomes, evaluated downstream of persisted source events
  and immutable X in bounded owned batches; the lease remains active until
  all required observations are persisted, including honest unavailable/
  insufficient statuses") into the SCRUM-123 paragraph and drop the family
  enumeration — owner to choose; both preserve meaning.
- **SCRUM-97** — forward-return sentences + link (multiple-horizons and
  units sentences stay here; they were introduced by this delivery).
- **SCRUM-98 / SCRUM-99 / SCRUM-100 / SCRUM-101** — each family's sentence
  group + link, verbatim.
- **Deferred scope** — final tail (SCRUM-102 aggregate statistics, SCRUM-138
  Runs UI, standalone-explicit-run rule, distributed execution and automatic
  checkpoint resume deferred).

## 2. Proposed README restructuring (`README.md:60-64`)

Convert the four prose paragraphs into short bullet groups — Queue controls
(submit/start/pause/order/cancel, paused start, no checkpoint resume),
Supported outcome families (detector-only + registered forward-return/
excursion/threshold/structural; `/outcome-definitions` catalog), Segmentation
(`/segment-definitions`, per-run group sizes, SCRUM-102 remaining), and
Standalone execution (explicit run path, queue ownership rule). *Flagged:*
the queue-controls sentence appears in both paragraph 60 and 64's
neighborhood; the restructure should keep one canonical control list and
cross-reference it. All operational statements preserved verbatim.

## 3. Link integrity

All eight plan-doc links in the affected sections resolve at current master.
The split must keep each link attached to its story group; the mapping above
does so. After restructuring, re-run a link check over the three documents.

## Application guidance

Apply as a pure-reorganization commit (no reviewer-visible semantic edits; the
two flagged ambiguities resolved by the owner first), with the sentence-level
diff reviewed against this mapping. `docs/core/README.md` needs no change.
