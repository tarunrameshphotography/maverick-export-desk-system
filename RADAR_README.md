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
| Why did it rank this? | `python -m radar why SIG-2026-0024` |
| Fetch and store the primary document | `python -m radar fetch <signal>` |
| Verification worksheet | `python -m radar verify <signal>` |
| Verify a claim (quote must appear in the stored document) | `python -m radar claim-mark <id> verified --type fact --quote "..."` |
| Add a claim found in research | `python -m radar claim-add <signal> --text ... --type fact --url ... --source-type primary --quote "..."` |
| Angle brief / add angle / **pick angle (human)** | `angle-brief`, `angle-add`, `angle-select <id>` |
| Draft + writing brief | `python -m radar draft <signal> --channel linkedin` (or `instagram`) |
| Edit, lint, **approve (human)** | `draft-edit`, `draft-lint`, `draft-status <id> approved --by founder [--external-check "CA name"]` |
| Log what a human posted | `python -m radar published <draft> --url ... --text @final.txt` |
| Calls Ledger | `call-add`, `call-grade`, `calls --due` |
| Weekly metrics / monthly learning | `metrics-import week.csv`, `learn`, `learn-decide <id> --accept/--reject` |
| Offline demo | `python -m radar run morning --sample --date 2026-09-14` |
| Schedule (05:30, 06:30, 16:00, 21:00) | `python -m radar schedule` (dry run) → `--install` to register Windows tasks |

## Configuration (no logic hard-coded)
`radar/config/`: `sources.yaml` (registry, probe notes, freshness), `scoring_weights.yaml` (Part 12 gates, weights, penalties, thresholds, disclosure triggers, model routing, flags), `categories.yaml`, `clusters.yaml`, `content_rules.yaml` (disclosure line, banned phrases, risk tiers, franchise formats), `schedule.yaml`. Secrets go in `.env` (see `.env.example`).

## Layout
`radar/collectors` (RSS, Federal Register API, DGFT table watcher, sample fixture) · `radar/pipeline` (normalize, dedupe/cluster, entities, classify, exposure, saturation, scoring, verify, evidence, angles, orchestrator) · `radar/desk` (Desk Sheet, approvals, Calls Ledger, performance/learning) · `radar/content/drafts.py` · `radar/db` (schema + migrations) · `tests/` (181 tests).
