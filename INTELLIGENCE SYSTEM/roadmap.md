# Roadmap — from Export Signal Radar to the Daily Autonomous Content Engine

This is the end-to-end roadmap for the system as it actually exists today,
reconstructed from the repository (code, tests, config, git history) rather
than from any prior plan. It supersedes any earlier phase numbering in
`automation_architecture.md` where the two disagree — that document is still
correct on architecture and guardrails, just not on what is built yet.

The end goal (unchanged from the founder's brief): a daily pipeline that goes

```
latest trade/export/regulatory developments
  -> signal selection (dedupe, score, verify)
  -> product / HS / market / Indian-exposure mapping
  -> Maverick Minds angle
  -> publish-ready LinkedIn + Instagram content, each traceable to its sources
  -> quality control
  -> a publishing queue
  -> (eventually) automated publishing to LinkedIn and Instagram
  -> performance data
  -> a learning loop that improves future selection and content
```

with a human approval gate that is *configurable*, not hard-wired — so it can
be tightened or loosened without a redesign, and defaults to requiring a
human today.

## Stage map

| # | Stage | State | Where it lives |
|---|---|---|---|
| A | Collect | **Done** | `radar/collectors/` (RSS, Federal Register API, DGFT/CBIC page-watch + API, sample fixture) |
| B | Normalize & dedupe | **Done** | `radar/pipeline/normalize.py`, `dedupe.py` |
| C | Triage / entity extraction | **Done** (rule-based, deliberately not model-based — see below) | `radar/pipeline/entities.py`, `classify.py` |
| D | Score & saturation | **Done** | `radar/pipeline/scoring.py`, `saturation.py` |
| E | Morning Desk Sheet (human picks lead) | **Done** | `radar/desk/desk_sheet.py` |
| F | Verify (claims table, evidence) | **Done**, hybrid | `radar/pipeline/verify.py`, `evidence.py` |
| G | Product / HS / market / Indian exposure | **Done, hardened this cycle** | `radar/pipeline/exposure.py`, `exposure_lookup.py`, `entities.py` |
| H | Maverick Angle (human/Claude Code picks) | **Done**, hybrid | `radar/pipeline/angles.py` |
| I | Content Engine — full asset bundle | **Done this cycle** (scaffold generation); prose still hybrid by design | `radar/content/drafts.py` |
| J | Quality control (lint, disclosure, risk tier, checklist) | **Done** | `radar/content/drafts.py::lint_draft`, `set_draft_status` |
| K | Publishing queue (status/approval workflow) | **Partially done** — approval state machine exists; scheduling/media/error-state fields do not | `radar/desk/approvals.py`, `content_drafts` table |
| L | Social publishing integrations (LinkedIn/Instagram APIs) | **Not built** — by design, gated behind `auto_publish` feature flag which cannot currently be enabled | none yet |
| M | Daily orchestrator / scheduler | **Partially done** — collection is scheduled (`radar schedule`); content generation and publishing are not | `radar/runner.py`, `config/schedule.yaml` |
| N | Performance capture & learning loop | **Done** for the metrics/Calls-Ledger side; no live API-based metrics ingestion yet (CSV import only) | `radar/desk/performance.py`, `calls_ledger.py` |

## Why "hybrid" is a permanent architectural choice, not a stopgap

`angles.py` and (now) `drafts.py`'s asset bundle are deliberately mechanical:
they assemble every fact, source and rule an author needs into a scaffold,
but the actual sentence-writing — picking the angle, phrasing the hook — is
explicitly routed to "the analyst or a Claude Code session" (see the module
docstrings). This is not unfinished automation; it is the documented answer
to "why not auto-publish?" in `automation_architecture.md`: genuine editorial
judgement stays with a human-plus-Claude-Code session, and code enforces that
nothing published is unsourced. Phase 2 (below) extended the *shape and
completeness* of what the scaffold offers — a full 8-asset bundle instead of
one draft per channel — without moving prose-writing into unattended code.
That is a considered choice, not a gap to "finish" later; see
[milestones.md](milestones.md) for what changed and why.

## Phases from here

### Phase 1 — Hardening product → HS → market → exposure (this cycle)
**Goal:** make "HS code where reasonably determinable" true more often,
without ever inventing one.
- Chapter-level HS codes are now inferred from a product-name match against
  `config/clusters.yaml` when no code is stated in the text, clearly flagged
  as inferred/unverified everywhere it surfaces (entity confidence 0.4 vs.
  0.9+ for a stated code; a distinct note in `IndianExposure`; the exposure
  line template refuses to ever join a stated code with an inferred one).
- UN Comtrade destination-market coverage (`exposure_lookup.COUNTRY_M49`)
  extended from 8 to 39 markets using the UN's public M49 reference codes,
  and the lookup now tries every extracted destination, not just the first.
- **Known limitation carried forward, not fixed this cycle:** signal text
  passed to entity extraction is the concatenation of every raw item
  clustered under a signal (`orchestrator.py`). When two unrelated stories
  get merged by the similarity clusterer, keyword-based cluster/HS matching
  can tag a signal with an unrelated cluster (observed live: a RoDTEP notice
  picking up a "Coimbatore / engineering goods" tag). The exposure-line fix
  above stops that from ever reading as a fabricated HS list, but the root
  cause is in dedupe/clustering, not exposure — worth a follow-up look if it
  recurs on real (non-sample) data.

### Phase 2 — The Content Engine (this cycle)
**Goal:** the asset list the founder specified — three LinkedIn variants,
two Reel concepts, a caption, a carousel, a visual-direction brief — each
sourced back to the signal it came from.
- New `create_content_bundle()` in `radar/content/drafts.py` creates all
  eight assets in one call from a signal's selected angle + verified claims.
  Each row carries `asset_slot` (new column, migration `005`), so the three
  LinkedIn variants version independently instead of colliding.
- The three LinkedIn variants are structurally distinct (consequence-first /
  fine-print / action-window emphasis), not copies with a different label.
- Every asset's `source_reference` is the signal's primary document — the
  literal mechanism satisfying "every generated asset must reference the
  source signals it was produced from."
- Existing single-draft workflow (`radar draft --channel`) is untouched and
  still works (`asset_slot` back-fills to the channel name for those rows).
- New CLI: `python -m radar content-bundle <signal_id>`.

### Phase 3 — Publishing queue (next)
Extend `content_drafts`/a new queue table with: `scheduled_at`, explicit
`media_requirement`, `published_platform_id`, `error_state`. The approval
state machine in `desk/approvals.py` already has the right shape (reason
codes, reviewer, risk-tier gating) — this phase is schema + a queue-listing
view, not a rebuild.

### Phase 4 — Social publishing integrations
LinkedIn (Community Management API / Marketing API, depending on what the
authenticated account supports) and Instagram (Graph API via a connected
Facebook Page, professional account required). Both gated behind the
`auto_publish` feature flag, which `settings.feature_flags()` **hard-refuses**
to let be `true` today (`tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled`).
Flipping that gate later is a one-line config change plus removing that
refusal — the architecture does not need to change shape to allow it.

### Phase 5 — Daily orchestrator
Extend `radar/runner.py` / `config/schedule.yaml` (which already knows how to
register Windows Scheduled Tasks) to run content generation and queue
placement on a schedule, not just collection. Publishing itself stays manual
until Phase 4 is live and the flag is deliberately flipped.

### Phase 6 — Feedback learning loop
`desk/performance.py` and `calls_ledger.py` already implement weight-proposal
learning from CSV-imported metrics. The remaining piece is live metrics
ingestion from the LinkedIn/Instagram APIs (once Phase 4 exists) instead of
a manual weekly CSV export.

## Dependencies

Phase 3 depends on nothing new (schema-only). Phase 4 depends on Phase 3
(a queue to publish *from*) and on real developer accounts/API access
(see milestones.md's "credentials needed" list). Phase 5 depends on Phase 2
(content to schedule) and benefits from Phase 3 existing first. Phase 6's
live-metrics half depends on Phase 4.

## What "done" means for each remaining milestone

- **Phase 3 done:** a signal's approved content bundle can be listed as a
  queue with scheduled time, required media, and status, independent of the
  Desk Sheet.
- **Phase 4 done:** an approved, queued item can be posted to LinkedIn and/or
  Instagram via API by a code path that is inert unless `auto_publish=true`
  and a human has approved that specific item; failures are recorded, not
  swallowed.
- **Phase 5 done:** `radar schedule --install` registers tasks that also
  generate and queue content, with the same dry-run-by-default safety as
  today's collection scheduling.
- **Phase 6 done:** a published post's real engagement numbers flow back into
  `performance.py` without a manual CSV step.
