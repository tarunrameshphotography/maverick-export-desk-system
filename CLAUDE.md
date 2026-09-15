# Maverick Minds — Export Desk System: project context

Read this before touching code. It is the durable source of truth for what
this project is, what exists, and what must not be undone. Deeper detail
lives in `INTELLIGENCE SYSTEM/roadmap.md` and `INTELLIGENCE SYSTEM/milestones.md`
(update both when you finish meaningful work) and `RADAR_README.md` (day-to-day
command reference).

## What this project actually is

Not a triage dashboard. The end goal is a **daily autonomous content engine**
for Maverick Minds: research the day's global trade/export/regulatory
developments, pick the 2-3 stories that matter to Indian exporters/MSMEs,
verify them against primary sources, work out the product/HS/market/exposure
implications, find the distinctive Maverick Minds angle, produce publish-ready
LinkedIn and Instagram content, run it through quality control, queue it, and
(eventually, once validated) publish it automatically — then feed real
engagement data back into what gets picked and written next.

```
latest developments -> signal selection -> verification -> product/HS/market/
exposure -> Maverick angle -> content generation -> QC -> publishing queue ->
(eventually) auto-publish -> performance data -> learning loop
```

The brand thesis, unchanged since the original strategy docs: **"First with
the consequences, not first with the news."** See `BRAND/voice_bible.md` for
the full voice spec and `BRAND/positioning.md` / `MARKET/` for why this beats
a generic trade-news account.

## Current architecture (as it exists, not as originally sketched)

Everything lives under `radar/`, is SQLite-backed (`radar/db/`, migration
files applied in numeric order, tracked in `schema_migrations`), and is
driven by YAML config in `radar/config/` — **no logic is hard-coded** where a
config file can hold it instead (scoring weights, content rules, source
registry, clusters, schedule).

```
radar/collectors/   RSS, Federal Register API, DGFT/CBIC page-watch + API, WTO ePing
                     (IMAP email-digest polling, since it has no feed/API), sample fixture,
                     manual lead intake (registry.add_manual_lead / `radar lead-add`)
radar/pipeline/      normalize -> dedupe/cluster -> entities -> classify -> exposure ->
                     saturation -> scoring -> verify -> evidence -> angles -> orchestrator;
                     source_roles.py (source tiers, independent origins, provenance)
radar/desk/          Desk Sheet, approvals (state machine), Calls Ledger, performance/learning
radar/content/       drafts.py — content scaffolding + the pre-publish checklist;
                     generation.py — Phase 2 prose generation (grounded LLM
                     call filling the scaffold; founder-approved exception,
                     see below)
radar/publishing/    queue.py (lifecycle state machine), validation.py (text/
                     media/approval gates), dispatch.py (scheduler tick +
                     Publisher seam for Phase 4). Phase 3, done — see below.
radar/db/            schema.sql (baseline) + migrations/NNN_*.sql (always applied, in order)
radar/cli.py         every day-to-day command (see RADAR_README.md)
```

Run `python -m pytest tests/ -q` before and after any change — **354 tests**,
all passing as of 2026-09-15 (WTO ePing inbox connector). If a change drops that number without an
explicit, understood reason, stop and fix it before continuing.

## Phase 3 — Publishing queue: DONE (2026-09-15)

The queue/lifecycle layer described in roadmap.md's Phase 3 is finished and
tested: `radar/publishing/` (+ migration `006`, `config/publishing.yaml`,
CLI `queue-*` commands, hooks in `drafts.py`) now has `tests/test_publishing_queue.py`
(36 tests) proving the state machine — approval/idempotency/repeat gating,
media validation, scheduling (cadence, founder-only tiers, past-time
refusal), supersession on edit/rejection, and every dispatch outcome
(published / no-post-id "unknown" / permanent error / retryable backoff to
failure / publisher exception / stale lease) with the `auto_publish` gate
left genuinely closed throughout (only `dispatch.assert_live_publishing_allowed`
is monkeypatched in tests that need to reach the publisher seam; the flag
itself is never touched). Also walked end-to-end on a scratch DB via the CLI
(`init` → sample run → `claim-mark` → `angle-add/select` → `content-bundle`
→ `draft-edit`/`draft-lint` → `draft-status approved` → `queue-add` →
`queue-schedule` → `queue-dispatch` dry run while due, confirmed no state
change → `queue-dispatch --live` refused → `queue-mark-published` →
terminal). No code changes were needed in `radar/publishing/` itself — the
2026-09-14 checkpoint's design and implementation held up under the full
planned test list in `INTELLIGENCE SYSTEM/milestones.md`. See that file for
what was and wasn't covered (the exhaustive bullet list there is aspirational;
the delivered suite covers every state-machine transition and guardrail, not
literally every enumerated scenario) and roadmap.md for what Phase 4 now
plugs into.

## WTO ePing inbox connector: DONE (2026-09-15)

`sources.yaml`'s `wto_eping` — flagged in the source-universe expansion as
"the best early-warning source per spec" but `active: false` because it has
no RSS/API, only an email digest — is now automated via
`radar/collectors/eping.py` (`method: email_imap`): IMAP-polls a dedicated
inbox (`maverickminds.cs@gmail.com`, registered live for daily SPS+TBT
alerts) and parses each digest's per-notification blocks. **Caveat that
matters if you touch this file:** the parser is built from ePing's
*documented* notification schema, not a real digest — none had arrived by
the time this was built (registration only just completed, and the first
email from WTO's account system was a 24-hour account-activation link, not
a digest). `sources.yaml`'s `wto_eping` notes and `eping.py`'s module
docstring both flag this; re-verify the parser against the first real
digest once one lands and adjust if the real layout differs. See
`INTELLIGENCE SYSTEM/milestones.md`'s "WTO ePing inbox connector" section
for the full build/test detail, including a live bug found and worked
around on eping.wto.org's own registration page during this work (its
Country dropdown never populated — a client-side Vue rendering bug, not a
site outage).

## The hybrid model — and the one founder-approved exception to it

`angles.py` is still **deliberately** mechanical: angle-picking is "genuine
analytical work... routed to a Claude Code session rather than a keyword
heuristic," and nothing in this codebase auto-selects an angle.

`content/drafts.py` builds the same kind of scaffold it always did —
`create_content_bundle()` assembles every verified fact, the selected angle,
the disclosure line and the risk tier into a structure with
`[HOOK]`/`[FACT]`/`[WRITE]`/`[VERIFY]` placeholders. That step is unchanged
and still exists on its own (`radar content-bundle`).

**What changed (founder sign-off given explicitly, 2026-09-14):**
`radar/content/generation.py` now fills that scaffold with real, finished
prose via a direct Anthropic API call (`radar content-generate`), not an
interactive Claude Code session. This was previously the one thing this file
said not to do without the founder's explicit go-ahead — it has now been
given, so treat the sentence "do not have code generate final prose
unattended" as **superseded for this one call path**, not as still standing.
Every guardrail that made unattended prose risky before is still fully
enforced on the *output* of that call, unchanged:
- Generation is grounded only in `claims` rows already `status='verified'`
  (verify.py's machine-checked-quote gate) plus the selected angle text — the
  prompt is given nothing else to draw "facts" from, and is explicitly told
  never to state a number/date/HS code/value not present in that material.
- `lint_draft` still runs on every generated asset and still blocks
  `approved` status on a placeholder or an untraceable number —
  `tests/test_generation.py::test_lint_catches_a_fabricated_number_the_model_invented`
  plants a model that ignores the grounding instruction, to prove this catch
  is real, not just wired.
- `set_draft_status`'s risk-tier/founder-approval gates, `auto_publish`,
  and `record_publication`'s "logs a publish, cannot create one" behavior are
  completely untouched. A generated asset still needs the same human sign-off
  as a hand-written one before it can be marked approved, and there is still
  no code path that posts anywhere.

If a *further* loosening is ever wanted (e.g. skipping human review for
green-tier content, or letting generation retry itself past a lint failure),
treat that the same way this one was treated: a real product decision
requiring the founder's explicit sign-off, not an obvious next step.

## Guardrails that must never be silently removed

- **`auto_publish` cannot be `true`.** `settings.feature_flags()` raises
  `RuntimeError` if it is (see `tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled`).
  This is intentional and load-bearing, not a bug. Flipping it later is a
  real product decision, not a config tweak to make during unrelated work.
- **No claim without a quote.** `verify.py`/`evidence.py` require a source
  URL and a quoted passage that machine-checks against the stored primary
  document for every fact-type claim. `lint_draft` rejects any number in a
  draft that doesn't trace back to a verified claim.
- **Discovery is not evidence.** Every source has a tier in `sources.yaml`
  (`source_tiers`) rating discovery / evidence / context / saturation
  separately — news is a legitimate discovery, confirmation, context and
  saturation source, never "noise" and never primary evidence.
  `verify.py::_check_evidence_authority` lets a fact claim verify only
  against a primary source, lets secondary sources back only attributed
  interpretation/opinion, and lets lead-only tiers (social posts, reposts,
  field notes) verify nothing; a collected secondary URL can't be relabelled
  primary. The two-source rule counts independent origins, not URLs
  (`source_roles.corroborating_origin_count`: same outlet / same wire credit
  / near-identical headline = one origin; lead-only never counts). Social
  platforms are manual intake (`radar lead-add`), not scraped. See
  `tests/test_source_roles.py` and RADAR_README.md's "Source universe".
- **"Unknown"/"uncertain" is a valid, expected answer**, not a failure.
  `exposure.py`'s tribool fields return `"uncertain"` rather than guessing.
  Never change a function to "always return something" if the honest answer
  is "we don't know yet."
- **Never fabricate:** trade values, HS codes, exposure figures, regulatory
  claims, quotes, dates, policy details. If data can only be *inferred*
  (e.g. a chapter-level HS code from a product-name/cluster match), it must
  be labelled inferred/unverified everywhere it surfaces, at a visibly lower
  confidence than something stated in the source, and it must never be
  silently joined with something that *was* stated (see `exposure_lookup.py`'s
  and `drafts.py::_exposure_line_template`'s handling of this).
- **Disclosure line is automatic, not optional.** Any signal tagged with a
  disclosure-trigger scheme (RoDTEP/RoSCTL/scrip) or category gets
  `content_rules.yaml`'s `disclosure_line` forced into every public-facing
  asset in its bundle; `record_publication` refuses to log a publish without
  it.
- **Risk-tier approval gating.** Amber/red-tier drafts require founder
  approval; red tier additionally requires a recorded external (CA/customs
  broker) check. This is enforced in `drafts.py::set_draft_status`, not just
  documented.
- **Voice rules are enforced, not aspirational.** No exclamation marks/emoji
  in LinkedIn copy, banned-phrase list, word-count band — all checked by
  `lint_draft` against `content_rules.yaml`, which is itself sourced from
  `BRAND/voice_bible.md` and `STRATEGY/*.md`. If the brand voice changes,
  change the YAML and the source doc together, not the code's assumptions.

## Known limitations (don't be surprised by these; don't silently "fix" them without checking roadmap.md first)

- Entity extraction text is the concatenation of every raw item clustered
  under one signal (`orchestrator.py`). An over-eager similarity cluster can
  merge two unrelated stories and leak an unrelated cluster/HS tag onto a
  signal (observed live on sample data: a RoDTEP notice picking up an
  unrelated "Coimbatore / engineering goods" tag). Mitigated at the
  exposure-line boundary (never join a stated code with an inferred one).
  Clustering was tightened 2026-09-15 (title similarity must hold against
  half the cluster; strong keys still chain; new items attach to recent
  signals across runs), which removed the 25-item mixed cluster seen live,
  but generic tokens can still merge small clusters — see milestones.md.
- HS-code inference is chapter-level only (from `clusters.yaml`'s
  `hs_chapters`), confidence 0.4, and only fires when nothing more specific
  is stated. It is a *lead*, not a tariff classification — every place it
  surfaces says so.
- No live LinkedIn/Instagram metrics ingestion yet — `performance.py` learns
  from a manually imported weekly CSV. This is fine until Phase 4 (API
  publishing) exists to also pull metrics from.

## Development workflow expectations

- Write tests for meaningful new behavior; put them next to the existing
  test for that module (`tests/test_<module>.py`), matching existing style
  (plain functions, a shared `conn`/`_seed` fixture pattern — see
  `tests/conftest.py` and `tests/test_drafts.py`).
- Run the full suite (`python -m pytest tests/ -q`) before claiming anything
  works, not just the file you touched — several modules read the same YAML
  config and can interact in non-obvious ways.
- When you add a schema change, add a new numbered file under
  `radar/db/migrations/`; never edit `schema.sql` to match (the existing
  migrations 001-004 already established this convention — `schema.sql`
  stays as the original baseline and migrations are always replayed in full
  on every `init_db`, including on a brand-new database).
- Update `INTELLIGENCE SYSTEM/roadmap.md` and `milestones.md` when you
  complete or materially change a phase, so the next session doesn't have to
  re-derive project state from scratch.
- Live-smoke-test CLI changes against a scratch DB
  (`RADAR_DB_PATH=<scratch path> python -m radar ...`) in addition to pytest
  — this session caught a real data-quality issue (above) that no unit test
  would have surfaced, because it only appears when real pipeline-derived
  text flows through multiple modules together.

## Git / repository state

- `origin` is `github.com/tarunrameshphotography/maverick-export-desk-system`
  (as of 2026-09-15); `gh` is authenticated to that account. Still check
  `git remote -v` fresh before pushing, and never force-push without asking.
- Single branch (`master`).
- Commit messages in this repo describe *what changed and why it was found*
  (e.g. "Fix DGTR cases failing the Indian-implication gate (found during
  live validation)") — follow that convention rather than generic messages.

## Where the strategy/brand source-of-truth lives (not code, but binding)

- `BRAND/voice_bible.md`, `positioning.md`, `business_understanding.md`
- `STRATEGY/content_architecture.md`, `linkedin_strategy.md`, `instagram_strategy.md`
- `AUDIENCE/audience_segmentation.md`, `MARKET/market_analysis.md`, `competitor_analysis.md`
- `INTELLIGENCE SYSTEM/*.md` — the original system design docs (still correct
  on philosophy/guardrails; `roadmap.md`/`milestones.md` in that same folder
  are the living, evidence-based supplement for "what's actually built")
- `DELIVERABLES/30_day_editorial_plan.md` and the Export Desk Strategy doc

`radar/config/content_rules.yaml` and `scoring_weights.yaml` are meant to be
the machine-readable distillation of these — if you find them drifting apart,
the markdown docs are the source of truth; fix the YAML, don't reinterpret
the docs.
