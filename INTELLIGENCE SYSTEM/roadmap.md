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
| I | Content Engine — full asset bundle, genuinely finished prose | **Done** — scaffold (`drafts.py`) + grounded LLM generation (`generation.py`), founder-approved | `radar/content/drafts.py`, `radar/content/generation.py` |
| J | Quality control (lint, disclosure, risk tier, checklist) | **Done** | `radar/content/drafts.py::lint_draft`, `set_draft_status` |
| K | Publishing queue (status/approval workflow) | **Done (Phase 3, 2026-09-15)** — queue schema, lifecycle state machine, scheduling, dispatch seam and CLI, now with `tests/test_publishing_queue.py` (36 tests) and a live CLI walkthrough on a scratch DB. See milestones.md "Phase 3 — DONE" | `radar/publishing/`, migration `006`, `tests/test_publishing_queue.py` |
| L | Social publishing integrations (LinkedIn/Instagram APIs) | **Code built and tested (2026-09-15); `auto_publish` gate still closed by design** — see Phase 4 below | `radar/publishing/linkedin.py`, `radar/publishing/instagram.py` |
| M | Daily orchestrator / scheduler | **Partially done** — collection is scheduled (`radar schedule`); content generation and publishing are not | `radar/runner.py`, `config/schedule.yaml` |
| N | Performance capture & learning loop | **Done** for the metrics/Calls-Ledger side; no live API-based metrics ingestion yet (CSV import only) | `radar/desk/performance.py`, `calls_ledger.py` |

## Why "hybrid" was the default, and what changed this cycle

`angles.py` is still deliberately mechanical: picking the angle — the
genuine editorial judgement — stays "routed to the analyst or a Claude Code
session," not automated. That has not changed and is not planned to.

`drafts.py`'s scaffold (facts, angle, disclosure, risk tier assembled into a
structure with placeholders) is also unchanged and still exists as its own
step (`radar content-bundle`). What changed this cycle, with the founder's
explicit sign-off (see `CLAUDE.md`'s "hybrid model" section): a new module,
`radar/content/generation.py`, now fills that scaffold with real prose via a
grounded LLM call (`radar content-generate`) instead of requiring a human or
an interactive Claude Code session to write it by hand. The editorial
judgement (which angle, which lead) is still human-gated; only the
*sentence-writing*, once the angle is already chosen and the facts are
already verified, is now automated — and every mechanism that made that safe
to consider (grounding in verified claims only, `lint_draft`'s untraceable-
number and placeholder checks, risk-tier approval gating, no `auto_publish`
path) is unchanged and still enforced on the output. See
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

### Phase 2 — The Content Engine (done)
**Goal:** the asset list the founder specified — three LinkedIn variants,
two Reel concepts, a caption, a carousel, a visual-direction brief — each
sourced back to the signal it came from, and (this cycle) genuinely finished,
not a scaffold a human still has to write.

**Part A — the bundle scaffold:**
- `create_content_bundle()` in `radar/content/drafts.py` creates all eight
  assets in one call from a signal's selected angle + verified claims. Each
  row carries `asset_slot` (column, migration `005`), so the three LinkedIn
  variants version independently instead of colliding.
- The three LinkedIn variants are structurally distinct (consequence-first /
  fine-print / action-window emphasis), not copies with a different label.
- Every asset's `source_reference` is the signal's primary document.
- Existing single-draft workflow (`radar draft --channel`) is untouched.
- CLI: `python -m radar content-bundle <signal_id>`.

**Part B — real prose generation (founder sign-off given 2026-09-14):**
- New `radar/content/generation.py::generate_bundle_prose()` takes a
  scaffolded bundle and writes the actual finished text for every asset via
  a direct Anthropic API call, grounded *only* in verified claims and the
  selected angle — never given anything else to draw "facts" from, and
  explicitly instructed never to state a number/date/HS code/value not in
  that material.
- Each generated asset is saved as a new draft version (`save_draft_version`
  — full history preserved, `edited_by="claude_content_engine"`) and
  immediately run through the existing `lint_draft`, unchanged — a
  fabricated number or surviving placeholder still blocks approval exactly
  as it would for hand-written copy.
- `drafts.py::_exposure_line_template` now calls the real UN Comtrade lookup
  (`exposure_lookup.exposure_line`, previously wired only into the Desk
  Sheet) instead of emitting a `[VERIFY: value]` placeholder that could
  never be finished — it now returns either a real retrieved figure or an
  honest, bracket-free "not auto-retrieved, here's where to check" sentence.
  Never a fabricated number, never a permanently-stuck placeholder.
- `create_and_generate_bundle()` is the single-call entry point:
  verified signal + selected angle → scaffold → generated prose → linted →
  ready for the approval gate, in one call.
- CLI: `python -m radar content-generate <signal_id>` (needs
  `ANTHROPIC_API_KEY`; `content-bundle` alone still works without it).
- `auto_publish`, `record_publication`, and risk-tier/founder approval gating
  are completely untouched — a generated asset needs the same human sign-off
  as a hand-written one, and nothing here can publish.

### Phase 3 — Publishing queue (done, 2026-09-15)
Built as a new `publish_queue`/`queue_media`/`publish_attempts`/`queue_events`
schema (migration `006`) plus `radar/publishing/{queue,validation,dispatch}.py`
and CLI `queue-*` commands — a state machine (awaiting_media -> queued ->
scheduled -> publishing -> published, with retry/hold/reconciliation/
cancellation/supersession side paths), not a rebuild of the approval state
machine in `desk/approvals.py`, which it reuses as-is for the underlying
draft approval gate. See milestones.md for the full built/tested breakdown.

### Phase 4 — Social publishing integrations (code built and tested, 2026-09-15; gate still closed)
`radar/publishing/linkedin.py` and `radar/publishing/instagram.py` now
satisfy Phase 3's `Publisher` protocol: LinkedIn (Posts/Images/Videos/
Documents/MultiImage APIs, contract verified against
learn.microsoft.com/en-us/linkedin/marketing/community-management/ on
2026-09-15, moniker li-lms-2026-08) and Instagram (Graph API content
publishing, verified against developers.facebook.com/docs/instagram-platform/
content-publishing/ on 2026-09-15, v25.0). Both read credentials from
environment variables only (`radar/publishing/credentials.py`), classify
every HTTP outcome through a shared `platform_http.py` (401/403 always
`permanent_error`, never silently retried), and are fully covered by unit
tests injecting a fake HTTP session — zero real network calls anywhere in
this codebase or its test suite. `auto_publish` remains hard-refused by
`settings.feature_flags()`, unchanged (`tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled`);
`queue-dispatch --live` still refuses before either publisher is ever
called. See `docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md`
and `milestones.md` for the full built/tested breakdown, and re-verify both
platforms' API versions against their live docs before ever flipping the
gate — this was checked once, not continuously. Flipping the gate later is
a one-line config change plus removing the hard refusal — the architecture
does not need to change shape to allow it, and is a separate, explicit
founder decision (see CLAUDE.md's guardrails).

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

Phase 3 is done. Phase 4 depends on Phase 3 (a queue to publish *from*,
now built) and on real developer accounts/API access (see milestones.md's
"credentials needed" list). Phase 5 depends on Phase 2 (content to schedule)
and benefits from Phase 3 existing first (it now does). Phase 6's
live-metrics half depends on Phase 4.

## What "done" means for each remaining milestone

- **Phase 4 done:** an approved, queued item can be posted to LinkedIn and/or
  Instagram via API by a code path that is inert unless `auto_publish=true`
  and a human has approved that specific item; failures are recorded, not
  swallowed.
- **Phase 5 done (2026-09-15):** `radar run content` and `radar schedule
  --install` (via a new `MaverickRadar_ContentGen` entry in
  `config/schedule.yaml`) automatically generate and lint a full content
  bundle for every signal a human has already moved to `READY_FOR_REVIEW`
  by selecting an angle (`angle-select`). It never auto-approves a draft or
  places anything in the publishing queue — both remain explicit human
  actions via `draft-status approved` and `queue-add`/`queue-schedule`, as
  required by CLAUDE.md's hybrid-model guardrails. Tested in
  `tests/test_runner.py` (candidate generation, skipping signals without a
  selected angle or without verified claims, and idempotency — a signal
  that already has a bundle is not regenerated on a later run) and walked
  end-to-end on a scratch DB. Live Anthropic generation was not exercised in
  this environment (no `ANTHROPIC_API_KEY`/`anthropic` package installed):
  the automated tests cover the generation path via an injected fake
  `generate_fn`, and the CLI wiring/candidate-selection/guardrails were
  verified live on a scratch DB, but not a real model call.
  A final integration-review fix pass (2026-09-15) closed two defects found
  by whole-branch review: migration 007's `DROP TABLE system_runs` could
  raise `sqlite3.IntegrityError` on any real DB with `source_failures` rows
  referencing it (fixed by disabling foreign keys for the rebuild, as
  migration 004 already does); and a signal skipped for having zero
  verified claims was being permanently excluded from every future content
  run, because the scaffold rows were already committed before the
  no-verified-claims check ran (fixed by checking verified-claim count
  before scaffolding). That same pass noted a residual, harder limitation
  it did not attempt to fix: a crash partway through a single signal's
  generation (after some but not all of its asset slots are written) leaves
  that signal's partial `content_drafts` rows in place, and it is not
  retried automatically on a later run — recovering from a partial bundle
  would need a larger transactional-retry design.
- **Phase 6 done:** a published post's real engagement numbers flow back into
  `performance.py` without a manual CSV step.
