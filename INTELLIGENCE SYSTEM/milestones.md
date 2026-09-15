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

## Phase 4 — Social publishing integrations: code built and tested (2026-09-15)

Built per `docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md`,
with LinkedIn/Instagram API contracts verified against each platform's live
developer documentation (not model memory) during implementation, not just
during design — see that spec's "Verified API facts" section and this
entry for the exact endpoints, request/response shapes, and doc URLs
checked on 2026-09-15.

**Built and wired:**
- `radar/publishing/platform_http.py`: pure-function HTTP-outcome
  classification shared by both platforms — 401/403 always `permanent_error`
  (never retried), 429 `retryable_error` (with `Retry-After` if present),
  5xx `retryable_error`, other 4xx `permanent_error`, a transport exception
  always `unknown`.
- `radar/publishing/credentials.py`: env-var-only credential loading
  (`LINKEDIN_ACCESS_TOKEN`/`LINKEDIN_PERSON_URN`/`LINKEDIN_ORG_URN`,
  `INSTAGRAM_ACCESS_TOKEN`/`INSTAGRAM_BUSINESS_ACCOUNT_ID`) — the only seam
  either publisher touches `os.environ` through, shaped so a future
  token-refresh mechanism can replace these two functions' bodies without
  changing either publisher's constructor.
- `radar/publishing/linkedin.py`: `LinkedInPublisher` — text, image
  (Images API upload), organic MultiImage (2-20 images, explicitly distinct
  from LinkedIn's sponsored-only Carousel — confirmed live in the Posts API
  docs: "Organic carousel is currently not supported"), document (Documents
  API, required title), and video (Videos/Assets API's always-chunked
  upload, ETag part IDs, `finalizeUpload`) post formats. Confirmed live:
  the created post's ID comes back in the `x-restli-id` response header,
  not the body; image upload's PUT requires an `Authorization` header while
  video upload's PUT does not. `Linkedin-Version` header value pinned via
  `radar/config/publishing.yaml`'s new `platform_apis.linkedin.api_version`
  (`"202608"`), not hard-coded.
- `radar/publishing/instagram.py`: `InstagramPublisher` — image, carousel
  (up to 10 items), and Reel (container + bounded status polling, never
  auto-retried past the poll bound — returns `unknown` instead of guessing,
  matching Meta's own documented recommendation: "query a container's
  status once per minute, for no more than 5 minutes") post formats via the
  Graph API two-step container/publish flow. API version, poll attempt
  count, and poll interval all read from `publishing.yaml`'s new
  `platform_apis.instagram` keys. Error classification inspects the Graph
  API's JSON error body (`error.type`/`error.code`) before falling back to
  `platform_http`, since Graph API frequently returns HTTP 400 for both
  malformed requests and expired tokens alike.
- `radar/cli.py::cmd_queue_dispatch` now builds a `publishers` dict from
  whichever of the two credential functions return non-`None` — a platform
  with no env vars configured is simply absent, reusing `dispatch_due`'s
  existing "skipped: no publisher for X" behavior from Phase 3.

**Tests added:** 46 new tests across `tests/test_platform_http.py` (11),
`tests/test_credentials.py` (6), `tests/test_linkedin_publisher.py` (15),
`tests/test_instagram_publisher.py` (11), `tests/test_dispatch_real_publishers.py` (2),
plus 1 in `tests/test_cli.py`. Every publisher test injects a fake HTTP
session (`tests/_fake_http.py`) — zero real network calls in the suite.
Full suite: **316 passing** (was 270).

**Verified live, not just under pytest:** `radar init` → `radar queue-dispatch`
on an empty scratch DB with no platform env vars set (both publishers
absent, no error) and again with a fake `LINKEDIN_ACCESS_TOKEN`/
`LINKEDIN_PERSON_URN` set (publisher constructs cleanly; still "Nothing is
due" since nothing was queued and `--live` was not passed). No real
LinkedIn/Instagram API call was made anywhere in this phase, per the
founder's explicit instruction.

**What was deliberately not done:** `auto_publish` remains hard-refused by
`settings.feature_flags()`, completely untouched; no OAuth token
acquisition/refresh was implemented (`credentials.py` is the seam, not a
refresh mechanism); no real HTTP call to either platform was made in this
session. Flipping the gate, or adding refresh, are each their own future
decision — see roadmap.md's Phase 4 section.

## Phase 5 — Daily content orchestrator: DONE (2026-09-15)

`radar run content` and `radar schedule --install` (via a new
`MaverickRadar_ContentGen` entry in `config/schedule.yaml`) automatically
generate and lint a full content bundle for every signal a human has
already moved to `READY_FOR_REVIEW` by selecting an angle (`angle-select`).
Orchestrator scheduling for content generation only — not queue placement:
queueing an approved draft still requires a human, since
`publish_queue.enqueue` requires `status='approved'` and nothing in this
codebase auto-approves a draft. See roadmap.md's "Phase 5 done" entry for
the full detail, including the final integration-review fix pass
(2026-09-15) that closed a migration FK bug and a permanent-skip retry
defect found during that review.

## Source-universe expansion: DONE (2026-09-15)

Goal: stop being a "government/regulatory news collector" and become a broad
discovery system that still only lets primary-verified facts into content.

**Before:** 16 active sources (of 26 registered) — Indian regulators (DGFT
×3, CBIC, DGTR, RBI, PIB), US Federal Register ×2, WTO news, Business
Standard, 5 Google News queries. **After:** 33 active of 53 registered. Reliability was one number plus `kind: primary|secondary`; the two-
source rule counted distinct publisher domains; clustering was greedy
single-link and only within one run's new items.

**Built:**
- `sources.yaml` `source_tiers` (primary_official, trade_data,
  secondary_media, research_analysis, trade_body, social_expert,
  aggregator_repost, field_intelligence) rating discovery / evidence /
  context / saturation separately; `provenance` config (syndication
  threshold, wire services). `radar/pipeline/source_roles.py` resolves them.
- 17 newly active sources (16 new + UK TRA switched from manual to its
  Atom feed), each probed live 15 Sep 2026: Reuters (Google News
  site: query — no public RSS), Mint, BusinessLine, ET Economy, Fibre2Fashion,
  USTR RSS, UK Trade Remedies Authority Atom, EU trade/India, GCC, China GACC,
  RASFF-on-India, logistics/freight, Global Trade Alert, GTRI, ICRIER/RIS/
  ORF/CSEP, law-firm alerts, EPC/commodity boards. 11 new registered-but-
  manual entries (EU OJ, other trade-remedy regulators, TradeStat, Comtrade,
  ITC Trade Map/GTA, carrier/port advisories, FE feed, 4 social/manual-lead
  sources); FIEO and field_notes re-tiered (no longer "primary").
- Evidence gate (`verify.py::_check_evidence_authority`): fact/verify claims
  verify only against primary sources; secondary can back attributed
  interpretation/opinion; lead-only verifies nothing; a collected secondary
  URL can't be relabelled primary.
- Corroboration counts independent origins (`corroborating_origin_count`),
  not URLs; lead-only tiers never count toward G1/G4.
- Clustering: majority-link for title similarity (strong keys still chain);
  new items attach to matching signals from the last 10 days across runs; a
  later primary becomes the signal's primary; automated-status signals are
  re-queued for scoring, human-advanced ones are left alone. Claim
  extraction is now idempotent per source URL so a late primary's claims
  are extracted.
- Manual intake `radar lead-add` and `radar provenance <signal>`.
- Tests: `tests/test_source_roles.py` (19) + 2 chaining regressions in
  `test_dedupe.py`. Suite 323 → 344.

**Live smoke (scratch DB, 15 Sep 2026):** 33 sources checked, 476 items, 0
source failures. Before the clustering fix the largest signal merged 25
unrelated items (EU FTA + Mercosur + inflation + trade deficit — all
title-similarity links, no strong keys); after, the largest is one real event
(US final AD/CVD on solar cells from India/Indonesia/Laos: 14 reports → 7
independent origins). A LinkedIn lead added on a later run attached to that
signal as a discovery lead, with no primary and no corroboration credit.

**Residual gaps:** some mixing remains in small (2-7 item) clusters where
generic tokens ("trade", "exports") carry similarity; no "conflicting
reports" detection; a signal re-opened by late coverage re-extracts entities
but not the Desk Sheet history; saturation still counts lead-only items
(harmless today — they are manual and rare); no primary feed yet for EU OJ,
RASFF, GACC, CBSA/ADC.

## WTO ePing inbox connector: DONE (2026-09-15)

Goal: automate the source-universe expansion's flagged top-priority gap —
ePing, "the best early-warning source per spec," was `active: false` because
it delivers SPS/TBT notifications only as an email digest, not a feed or API.

**Investigated live (15 Sep 2026):** registering at eping.wto.org confirmed
ePing has no RSS/API alternative — signup asks for product/HS/market
criteria and an alert frequency (daily/weekly) and only ever emails matching
notifications. Registered a dedicated inbox, `maverickminds.cs@gmail.com`,
for daily SPS+TBT alerts across all products/markets. **Found and worked
around a live bug on eping.wto.org during registration:** the Country/
territory dropdown never populated (its backing Vue `countries` array stayed
`undefined` even though the API call behind it returned 200 with real data —
a client-side rendering bug, not a network failure); fetched and parsed that
endpoint's XML response directly and injected it into the page's own Vue
instance to unblock registration, since WTO's own JS never did. No real
digest has arrived yet (registration only just completed, and the very
first email from WTO's account system was an *activation* link with a
24-hour expiry — not a digest — which the founder still needed to act on
separately).

**Built:**
- `radar/collectors/eping.py` (`method: email_imap`): IMAP-polls the inbox
  for `UNSEEN` mail from `epingalert.org` (fetching marks it `\Seen`, which
  doubles as "already processed" with no extra DB state); splits each
  digest's flattened text at `G/TBT/N/...`/`G/SPS/N/...` document-symbol
  matches into one block per notification; pulls the permalink, an
  ePing-domain link inline via `[URL]`-bracket text, from each block; best-
  effort date extraction (ISO or "D Month YYYY") from the surrounding text.
  Tolerates both an HTML digest (custom no-bs4 parser, matching
  `pagewatch.py`'s convention, explicitly skips `<style>`/`<script>`/`<head>`
  content — caught live: the WTO activation email's inlined CSS reset would
  otherwise have flooded every parsed block) and a plain-text one, since
  which format ePing actually sends was unknown at build time.
- `radar/settings.py`: `EPING_IMAP_HOST/PORT/USER/PASSWORD/SENDER_DOMAIN`,
  following the existing `COMTRADE_API_KEY`/`ANTHROPIC_API_KEY` pattern
  (secrets via `.env`, never hard-coded); `.env.example` documents the Gmail
  App Password requirement (plain-password IMAP login is refused once
  2-Step Verification is on, which is required to issue an App Password).
- `registry.py`'s `_COLLECTORS_BY_METHOD["email_imap"]`; `sources.yaml`'s
  `wto_eping` flipped to `method: email_imap`, `active: true` (tier
  unchanged — `kind: primary` already defaults to `primary_official`, which
  already fits: high discovery, primary evidence, low context/saturation).
  Migration `008_eping_email_imap_method.sql` (SQLite can't `ALTER` a CHECK
  constraint, so rebuilds `sources` like `004_cbic_api_method.sql` did).
- `tests/test_eping_collector.py` (10 tests, IMAP fully mocked): HTML and
  plain-text digests parse correctly; missing credentials raise
  `CollectorError` before any connection attempt; connection/login/search/
  fetch failures each raise `CollectorError`; a digest from the right sender
  with no recognisable notification pattern raises `CollectorError` with a
  body snippet (not a silent empty result); a notification block with no
  extractable link is skipped rather than fabricating one; `logout()` is
  called even when parsing fails. **Explicitly mock/fixture-only** — no real
  ePing digest existed to test against; `sources.yaml`'s notes and the
  module docstring both flag this and point at what to re-check once one
  arrives.
- `tests/test_collectors_registry.py` and `tests/test_runner.py`'s
  all-sources-failing tests updated to also stub `email_imap`, since it's
  now a fifth automated method alongside `rss`/`api`/`pagewatch`/`cbic_api` —
  without the stub, those tests were making a real IMAP call against
  whatever credentials happened to be in the runner's `.env`, non-hermetic
  in exactly the way `cbic_api`'s equivalent tests already guarded against.
- Suite 344 → 354.

**Live-tested (15 Sep 2026, real credentials, real inbox):** IMAP login
succeeds; `search`/`select` against the real 702-message inbox work; found
and correctly parsed the real WTO activation email as a live smoke test of
`_extract_bodies`/`_flatten_html` (this is what surfaced the `<style>`-leak
bug above — first pass returned 48,984 characters, almost entirely CSS;
after the fix, 327 clean characters). No real *digest* email has arrived
yet — that's still pending on the founder completing account activation and
the first daily alert being sent — so end-to-end collection into
`raw_items` via `radar run` has not been live-tested, only fixture-tested.

**Residual gap:** re-verify `eping.py`'s block/regex parser against a real
digest once the first one arrives (`EPING_SENDER_DOMAIN` search, block
splitting, link/date extraction) and adjust if the real layout differs from
the documented schema this was built against.

## Not started (see roadmap.md for detail)
- Phase 6 (partial): live metrics ingestion from platform APIs — CSV import
  and the weight-learning loop already exist and work.
