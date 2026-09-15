# Maverick Minds — Export Signal Radar (v1)

Code for the Desk described in `INTELLIGENCE SYSTEM/`. Machines collect, dedupe, score and draft; humans pick, verify and approve. **Nothing publishes automatically** (`auto_publish` cannot be enabled in v1).

## Setup (Windows, Python 3.10+)
```
pip install -r requirements.txt
python -m radar init
python -m pytest tests/ -q
```
No API keys are needed in v1 (hybrid mode: triage and scoring are rule-based; verification, angles and prose are done in a Claude Code session using the commands below).

## Daily use
| Step | Command |
|---|---|
| Morning run (collect → score → Desk Sheet) | `python -m radar run morning` → `data/desk_sheets/<date>.md` |
| Content generation run (fills + lints bundles for signals already at READY_FOR_REVIEW) | `python -m radar run content` |
| Why did it rank this? | `python -m radar why SIG-2026-0024` |
| Who reported it, by role (primary / independent corroboration / leads / context / saturation) | `python -m radar provenance <signal>` |
| Manual intake: a social post, field note or article no feed caught | `python -m radar lead-add expert_social_posts --title "..." --url ... [--body ...] [--published 2026-09-14] [--publisher linkedin.com]` (sources: `official_social_posts`, `expert_social_posts`, `unverified_reposts`, `manual_news_lead`, `field_notes` — anonymise field notes) |
| Fetch and store the primary document | `python -m radar fetch <signal>` |
| Verification worksheet | `python -m radar verify <signal>` |
| Verify a claim (quote must appear in the stored document) | `python -m radar claim-mark <id> verified --type fact --quote "..."` |
| Add a claim found in research | `python -m radar claim-add <signal> --text ... --type fact --url ... --source-type primary --quote "..."` |
| Angle brief / add angle / **pick angle (human)** | `angle-brief`, `angle-add`, `angle-select <id>` |
| Draft + writing brief | `python -m radar draft <signal> --channel linkedin` (or `instagram`) |
| Full 8-asset content bundle (3 LinkedIn + 2 Reels + caption + carousel + visual brief) | `python -m radar content-bundle <signal>` |
| Bundle, then fill it with grounded LLM prose (needs `ANTHROPIC_API_KEY`) | `python -m radar content-generate <signal>` |
| Edit, lint, **approve (human)** | `draft-edit`, `draft-lint`, `draft-status <id> approved --by founder [--external-check "CA name"]` |
| Log what a human posted | `python -m radar published <draft> --url ... --text @final.txt` |
| Publishing queue: add an approved draft, attach media, schedule, dispatch (dry run by default) | `queue-add <draft> --by founder`, `queue-media`, `queue-schedule <item> <when> --by founder`, `queue-dispatch` (`--live` is refused while `auto_publish` is false) |
| Publishing queue: list / inspect / cancel / requeue / reconcile / record a manual post | `queue`, `queue-show <item>`, `queue-cancel`, `queue-requeue`, `queue-reconcile`, `queue-mark-published` |
| Calls Ledger | `call-add`, `call-grade`, `calls --due` |
| Weekly metrics / monthly learning | `metrics-import week.csv`, `learn`, `learn-decide <id> --accept/--reject` |
| Offline demo | `python -m radar run morning --sample --date 2026-09-14` |
| Schedule (05:30, 06:30, 16:00, 19:00, 21:00) | `python -m radar schedule` (dry run) → `--install` to register Windows tasks |

## Source universe: what the Radar watches, and what each source is for
Broad discovery → cross-source validation → primary-source verification → publishable signal. Every source has a tier in `sources.yaml` (`source_tiers`) rating discovery, evidence, context and saturation separately (`radar/pipeline/source_roles.py`).

| Job | Sources (automated unless marked) |
|---|---|
| **Discovery** | Indian regulators (DGFT ×3, CBIC API, DGTR, RBI, PIB); foreign officials (US Federal Register ×2, USTR, UK Trade Remedies Authority, WTO news); news (Reuters via Google News, Business Standard, Mint, BusinessLine, ET Economy, Fibre2Fashion); topic queries (trade remedies on India, EU trade/CBAM/EUDR, GCC, China GACC, RASFF alerts, logistics/freight, DGFT/customs, RoDTEP/RoSCTL, Global Trade Alert); social/expert posts and field notes (**manual**, `lead-add`) |
| **Evidence (verification)** | Primary tiers only (`primary_official`, `trade_data`). A fact claim verifies only against a primary source; secondary sources can back an attributed interpretation/opinion; lead-only sources (social, reposts, field notes) verify nothing. Enforced in `verify.py`, not just documented. |
| **Context** | News analysis, GTRI (as reported), ICRIER/RIS/ORF/CSEP, law-firm alerts, EPC/commodity-board statements; TradeStat, UN Comtrade (exposure line), ITC Trade Map (**manual**) |
| **Saturation** | Secondary media and research items (copies count here — wide coverage *is* saturation) |
| **Corroboration (gate G1/G4)** | Independent secondary *origins*, not URLs: same outlet, same wire credit (PTI/Reuters/ANI/IANS/Bloomberg) or near-identical headline = one origin. Lead-only tiers never count. |
| **Still manual / not collected** | WTO ePing (email digest), ICEGATE (connection refused), FIEO site, US FDA import alerts, EU RASFF portal, EU Official Journal, DG TRADE site, GACC site, Canada/Australia/Turkey/Brazil/GCC regulators, carrier advisories & port authorities, freight indexes (licensed), TradeStat, ITC Trade Map, Financial Express feed (malformed), all social platforms (LinkedIn has no read API for others' posts; X read API is paid) |

The same event seen by several sources is one signal: items cluster by strong keys (document number, instrument + date, product + country) or by title/entity similarity that holds against at least half the cluster, and an item arriving on a later run attaches to a matching signal from the last 10 days — a primary document found after the news becomes that signal's primary.

## Configuration (no logic hard-coded)
`radar/config/`: `sources.yaml` (registry, probe notes, freshness), `scoring_weights.yaml` (Part 12 gates, weights, penalties, thresholds, disclosure triggers, model routing, flags), `categories.yaml`, `clusters.yaml`, `content_rules.yaml` (disclosure line, banned phrases, risk tiers, franchise formats), `schedule.yaml`. Secrets go in `.env` (see `.env.example`).

## Layout
`radar/collectors` (RSS, Federal Register API, DGFT/CBIC page-watch + API, sample fixture) · `radar/pipeline` (normalize, dedupe/cluster, entities, classify, exposure, saturation, scoring, verify, evidence, angles, orchestrator) · `radar/desk` (Desk Sheet, approvals, Calls Ledger, performance/learning) · `radar/content` (`drafts.py` scaffolding, `generation.py` grounded prose) · `radar/publishing` (queue lifecycle, validation, dispatch/Publisher seam) · `radar/db` (schema + migrations) · `tests/` (344 tests).
