# Milestones — evidence-based record

Compiled from `git log`, `tests/`, and direct inspection of the code as it
stands today (2026-09-14). See [roadmap.md](roadmap.md) for what these
milestones feed into.

## Completed before this session (14 commits, `cd86e7d`..`e22899e`)

| Milestone | Built | Tests | Notes / known limitations |
|---|---|---|---|
| Core pipeline (Phases 2-8 of the original spec) | `radar/pipeline/normalize.py`, `dedupe.py`, `entities.py`, `classify.py`, `scoring.py`, `saturation.py` | covered in `test_normalize.py`, `test_dedupe.py`, `test_entities.py`, `test_scoring.py`, `test_saturation.py` | Entity extraction is deliberately rule/regex-based, not model-based (fast, free, deterministic, testable) |
| Verification, angle engine, Desk Sheet | `radar/pipeline/verify.py`, `angles.py`, `radar/desk/desk_sheet.py` | `test_verify.py`, `test_angles.py`, `test_desk_sheet.py` | Angle *generation* explicitly routed to a human/Claude Code session — genuine analytical work, not a heuristic |
| Drafting, Calls Ledger, approvals, performance learning | `radar/content/drafts.py`, `desk/calls_ledger.py`, `desk/approvals.py`, `desk/performance.py` | `test_drafts.py`, `test_calls_ledger.py`, `test_approvals.py`, `test_performance.py` | Drafting produced one scaffold per channel with `[WRITE:]`/`[VERIFY:]` placeholders; `record_publication` only *logs* a URL a human already posted — no publish code path exists |
| Runner, CLI, scheduling | `radar/runner.py`, `radar/cli.py`, `config/schedule.yaml` | `test_runner.py`, `test_cli.py` | `radar schedule --install` registers Windows Scheduled Tasks for collection only |
| Live collectors, saturation/source-independence fixes | `radar/collectors/` (RSS, DGFT page-watch, sample fixture) | `test_pagewatch_and_freshness.py`, `test_collectors_registry.py` | Tuned against real probed sources during the first live run |
| Founder-readable Desk Sheet, primary-document fetch + quote verification | `radar/pipeline/evidence.py` | `test_evidence.py` | Machine-checks that a claimed quote actually appears in the stored primary document |
| Analyst pass recorded in DB; first README | — | — | First live end-to-end run |
| CBIC customs API collector; DGTR anti-dumping case listing activated | `radar/collectors/` | `test_cbic_collector.py` | Source-coverage priority-1 items |
| Exposure trade-value lookup; PDF/evidence-quality surfaced on Desk Sheet | `radar/pipeline/exposure_lookup.py` | `test_exposure_lookup.py` | UN Comtrade auto-retrieval with an honest manual-lookup fallback — "never a fabricated number" |
| DGTR Indian-implication gate fix; product-extraction fix for DGTR title phrasing | `radar/pipeline/exposure.py`, `entities.py` | `test_exposure.py` | Found during live validation against real DGTR notices |

At the start of this session: **209 tests passing**, no git remote configured,
`gh` already authenticated as `tarunrameshphotography`.

## Completed this session

### 1. Full repository audit (this milestone)
Read every pipeline/desk/content module, the DB schema and migration
convention, the CLI, the full strategy/brand/intelligence doc set
(`BRAND/`, `STRATEGY/`, `AUDIENCE/`, `MARKET/`, `INTELLIGENCE SYSTEM/`), and
ran the existing suite, before writing anything — to avoid re-deriving a
roadmap from assumption instead of the repo's actual state.

### 2. Phase 1 hardening — product → HS → market → exposure
**What changed:**
- `radar/pipeline/entities.py::extract_hs_and_clusters` now also emits a
  chapter-level `hs_code` entity (confidence 0.4) when a product keyword
  matches a cluster in `config/clusters.yaml` but no HS code/chapter is
  stated in the text — clearly labelled `"inferred from product keyword
  '<x>' (cluster map)"` in `raw_value`, never conflated with a stated code
  (confidence 0.9+).
- `radar/pipeline/exposure.py::analyze_indian_exposure` adds an explicit note
  whenever any `hs_codes` entry is chapter-level (2 digits), naming it as an
  inferred lead that still needs the actual tariff line verified.
- `radar/content/drafts.py::_exposure_line_template` now prefers a *stated*
  HS code outright over any inferred one, and never joins the two — this was
  added mid-session after live smoke-testing surfaced a real case (see
  "Limitation found and mitigated" below).
- `radar/pipeline/exposure_lookup.py::COUNTRY_M49` grew from 8 to 39
  destinations using the UN Statistics Division's public M49 codes; the
  lookup now tries every extracted destination market in order instead of
  only the first.

**Tests added:** `test_entities.py` (2), `test_exposure.py` (1),
`test_exposure_lookup.py` (2 new + 1 fixed), `test_drafts.py` (2). Full suite:
**223 passing** (was 209 → 214 after Phase 1 → 223 after Phase 2).

**Limitation found and mitigated, not root-caused:** live smoke-testing
against the sample pipeline (`radar run morning --sample`) showed a RoDTEP
signal picking up an unrelated "Coimbatore / engineering goods" cluster tag.
Root cause: `orchestrator.py` builds the entity-extraction text by
concatenating *every* raw item clustered under a signal — if the similarity
clusterer over-merges two unrelated stories, keyword matching sees both
texts as one. This is a pre-existing dedupe/clustering characteristic, not
something introduced this session, but the new HS-chapter inference made its
effect visible in generated copy for the first time. Fixed at the
exposure-line boundary (a stated code always wins; an inferred one is always
flagged, never silently joined) rather than in the clusterer, which is out
of this cycle's scope — flagged in roadmap.md as a follow-up if it recurs on
real (non-sample) data.

### 3. Phase 2 — the Content Engine
**What changed:**
- New DB column `content_drafts.asset_slot` (migration
  `005_content_asset_slots.sql`), back-filled to the existing `channel`
  value so the pre-existing single-draft workflow is unaffected.
- New `radar/content/drafts.py::create_content_bundle(conn, signal_id, now)`
  generates all eight named assets in one call: `linkedin_post_1/2/3`,
  `instagram_reel_1/2`, `instagram_caption`, `instagram_carousel`,
  `visual_direction`. Each is a distinct DB row, versioned independently by
  `asset_slot`, each carrying `source_reference` back to the signal's
  primary document, each subject to the same disclosure/risk-tier/lint rules
  as existing drafts.
- The three LinkedIn variants are structurally different scaffolds
  (consequence-first / fine-print / action-window emphasis) so a human/Claude
  Code session has three genuinely different starting points, not one post
  copy-pasted three times.
- New CLI command: `python -m radar content-bundle <signal_id>`.
- Prose generation remains hybrid by design (see roadmap.md) — this phase
  extended the *completeness* of the scaffold (all 8 named assets, not one
  per channel), not the authorship model.

**Tests added:** 8 new tests in `test_drafts.py` covering: all 8 slots
created and each sourced; distinct LinkedIn variant content; the "no
selected angle" guard; disclosure/risk-tier applied per asset; independent
per-slot versioning; the rendered bundle brief; and that the legacy
single-draft path still works after the schema change.

**Verified live**, not just under pytest: ran `radar init` →
`radar run morning --sample` → `angle-add` → `angle-select` → `claim-add` →
manual claim verification → `radar content-bundle` → `radar draft-lint` end
to end against a scratch DB, confirming the CLI wiring and DB writes work
outside the test harness too.

## Completed this session (2026-09-14, continuation)

### Repo re-verification
Cloned the repo fresh (this machine had no local copy) and independently
re-ran the full suite before touching anything — confirmed the prior
session's "223 passing" claim exactly, and confirmed `radar/content/drafts.py`
genuinely still emitted `[WRITE:]`/`[VERIFY:]` scaffolds rather than finished
prose. Surfaced that `CLAUDE.md` explicitly flags finishing that scaffold as
a real product decision requiring the founder's sign-off — asked, and
received it, before writing any generation code.

### Phase 2, Part B — real content generation
**What changed:**
- New `radar/content/generation.py`: `generate_bundle_prose()` grounds every
  generated asset in `claims` rows with `status='verified'` plus the
  selected angle text only, via an injectable `GenerateFn` (real
  implementation calls the Anthropic API; tests inject fakes so the suite
  never touches the network). Every generated asset is saved through the
  existing `save_draft_version` and linted through the existing
  `lint_draft` — no new bypass of either.
- `drafts.py::_exposure_line_template` now calls
  `exposure_lookup.exposure_line` (previously wired only into the Desk
  Sheet) instead of a permanent `[VERIFY: value]` placeholder — a genuine
  fix, not just plumbing for generation: the old placeholder could never be
  satisfied by any amount of "finishing," honest or not.
- New CLI: `python -m radar content-generate <signal_id>`.
- `requirements.txt` gained an optional `anthropic` dependency (not required
  for collection/verification/scoring/scaffolding or for the test suite —
  only for actually calling `content-generate`); `.env.example` documents
  `ANTHROPIC_API_KEY` and `RADAR_CONTENT_MODEL`.

**Tests added:** 11 new tests in `tests/test_generation.py`, including one
that deliberately plants a fabricated number (a fake "model" that ignores
the grounding instruction) to prove `lint_draft` actually catches it, not
just that the wiring runs — and one proving that fabricated content still
cannot reach `approved` status. Full suite: **234 passing** (was 223).

**Verified live, not just under pytest:** ran `radar init` →
seeded a signal/claim/angle by hand → `radar content-bundle` →
`radar content-generate` end to end against a scratch DB. Without
`ANTHROPIC_API_KEY`/the `anthropic` package installed, `content-generate`
fails with a clear, actionable error rather than a silent bad result —
confirming the CLI wiring is correct even though the real API call itself
wasn't exercised in this pass (no API key configured in this environment;
the module's own tests exercise every branch of the grounding/lint logic
with fake models instead).

**What was deliberately not done:** no change to `auto_publish`,
`record_publication`, or the risk-tier/founder-approval gates in
`set_draft_status` — a generated asset needs exactly the same human sign-off
as a hand-written one before it can be marked approved, and there is still
no code path that posts to LinkedIn or Instagram.

## Phase 3 — Publishing queue: DONE (2026-09-15)

Resumed from the 2026-09-14 checkpoint on a new machine. A fresh audit of
`radar/publishing/` against the planned test list below found the
2026-09-14 implementation already correct: **all 36 new tests in
`tests/test_publishing_queue.py` passed against the checkpointed code with
zero changes needed in `radar/publishing/queue.py`, `validation.py` or
`dispatch.py`.** The remaining work this session was writing the tests
themselves, a live CLI smoke test, and this documentation update — not
bug-fixing. Full suite: **270 passing** (was 234).

**Built and wired (now with `tests/test_publishing_queue.py`, 36 tests):**
- Migration `006_publishing_queue.sql`: `publish_queue`, `queue_media`,
  `publish_attempts`, `queue_events`; `published_content` gains
  `platform_post_id` + `queue_item_id`. Partial unique index on
  `idempotency_key` over live + published items (idempotent enqueue).
- `radar/config/publishing.yaml`: slot→format map, per-format text/media
  rules (LinkedIn + Instagram limits verified against official docs
  2026-09-14 — sources listed in the file header), editorial cadence from
  `STRATEGY/*_strategy.md`, approval freshness per tier (red 48h, amber 7d),
  founder-only scheduling for amber/red, retry/backoff, lease minutes.
- `radar/publishing/validation.py`: file inspection (size, sha256, real
  image type + pixel dimensions; video duration / PDF pages must be
  *declared*), text/media validation, and the draft/approval/signal gates.
- `radar/publishing/queue.py`: state machine (awaiting_media, queued,
  scheduled, publishing, published, retry_pending, needs_reconciliation,
  held, failed, cancelled, superseded) with compare-and-set transitions and
  a `queue_events` audit row per change; enqueue, attach_media, schedule
  (cadence + freshness + founder rule), unschedule, cancel, requeue,
  mark_published_manually, reconcile, supersession hooks, views.
- `radar/publishing/dispatch.py`: `dispatch_due` (dry run by default; live
  refused unless `auto_publish`), pre-flight gate, CAS claim + lease +
  attempt row committed before the platform call, retry/backoff,
  unknown outcomes → `needs_reconciliation` (never auto-retried),
  stale-lease recovery, `Publisher` protocol for Phase 4. No real platform
  publisher exists.
- `drafts.py` hooks: `save_draft_version` supersedes live queue items for
  that slot (incl. Reels/carousels using that caption); `set_draft_status`
  away from approved supersedes items using that draft;
  `record_publication` now reconciles the matching queue item and refuses
  a duplicate record for an already-published draft.
- CLI: `queue`, `queue-add`, `queue-media`, `queue-schedule`,
  `queue-unschedule`, `queue-cancel`, `queue-requeue`, `queue-show`,
  `queue-mark-published`, `queue-reconcile`, `queue-dispatch [--live]`.
  Smoke-checked only: `init` applies 006, `queue` / `queue-dispatch` run on
  an empty DB, `queue-dispatch --live` is refused (exit 2).

**What `tests/test_publishing_queue.py` actually covers (36 tests):**
approval gate (unapproved draft refused, stale draft version refused,
internal slot (`visual_direction`) refused, requeue refuses content that
went unapproved by a path the supersession hooks don't cover); idempotent
enqueue (same draft/account returns the same item, no duplicate row);
same-signal repeat guard and its recorded override; a Reel enqueue requires
an approved `instagram_caption` draft and publishes *that* text, not the
script; media lifecycle (`awaiting_media` → `queued` once valid media is
attached, wrong media kind refused, unrecognised file type refused, a file
edited on disk after attach is flagged); scheduling (past time refused,
amber-tier scheduling requires the founder, a weekend LinkedIn post is
blocked by cadence unless overridden, rescheduling moves the time,
unscheduling returns to `queued`, cancelling requires a reason); supersession
(a new draft version, a rejection, and a new caption version each supersede
the live queue item using that content); invalid transitions (a terminal
item refuses any further transition) and requeue (gives a fresh attempt
budget while attempt numbers keep counting up); dispatch (dry run changes
no state, an item not yet due is skipped, live dispatch is refused while
`auto_publish` is disabled) and — with `dispatch.assert_live_publishing_allowed`
monkeypatched to a no-op, never the flag itself — every live outcome:
success writes `published_content` with the platform post ID, a "published"
result with no post ID is downgraded to `needs_reconciliation`, a permanent
error fails immediately, a publisher exception is treated as unknown and
never re-called, a retryable error backs off and eventually fails at
`max_attempts`, a stale lease is recovered to `needs_reconciliation` (never
retried), and a compare-and-set claim refuses an item that moved underneath
it; manual publication (prevents re-dispatch, refuses a second manual
publish) and reconciliation (`published` writes the record, `not_published`
returns it to `retry_pending`); and the legacy `record_publication` path
reconciling the matching queue item and refusing a duplicate.

**Verified live, not just under pytest:** ran `radar init` → `radar run
morning --sample` → `claim-mark` (3 claims verified) → `angle-add` /
`angle-select` → `content-bundle` → `draft-edit` with real prose → `draft-lint`
(clean) → `draft-status approved` (red tier, founder + external check) →
`queue-add` (idempotent, `queued`) → `queue-schedule` → `queue-dispatch`
(dry run before due: nothing due; `--live`: refused, exit 2) → waited for
the scheduled time to actually pass → `queue-dispatch` dry run again:
correctly reported "would publish" for the now-due item with **no state
change** → `queue-dispatch --live`: still refused → `queue-mark-published`
→ item reached the terminal `published` state → `queue-dispatch`: item no
longer picked up. Confirms the CLI wiring, the schema, and the dry-run/live
gate all work outside the test harness, against a real (if sample-sourced)
signal, end to end.

**Design decisions already made (see the module docstrings for why):**
`held` (our gate stopped it) is separate from `failed` (platform rejected
it / retries exhausted); approval freshness and founder-only scheduling are
config policy the founder can change; LinkedIn's organic "carousel" is a
document or multi-image post because the Posts API supports organic
carousels only as sponsored content; Instagram Reels/carousels publish the
separately approved `instagram_caption` draft as their caption.

## Not started (see roadmap.md for detail)
- Phase 3: publishing queue schema (`scheduled_at`, media requirement,
  platform post ID, error state).
- Phase 4: LinkedIn/Instagram API publishing integrations. `auto_publish`
  remains hard-refused by `settings.feature_flags()`.
- Phase 5: orchestrator scheduling for content generation/queue placement
  (only collection is scheduled today).
- Phase 6 (partial): live metrics ingestion from platform APIs — CSV import
  and the weight-learning loop already exist and work.
