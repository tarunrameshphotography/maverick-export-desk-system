# Automation architecture

**Principle:** automate collection, sorting and first drafts; keep a human on choice, truth and publication. Stages run in this order. The dashed stages are human gates.

1. **Collect**Collectors pull feeds, APIs, page-change diffs and parsed email alerts (ePing, EPC circulars). Raw text is stored verbatim with a timestamp.
2. **Normalise & dedupe**Canonical URLs, text hashes, then similarity clustering so 14 articles about one notification become one story.
3. **Triage**A fast model classifies relevance and extracts entities: countries, HS codes, schemes, dates, clusters. Irrelevant items are dropped and logged.
4. **Score**Rules plus model fill in the Part 12 criteria. A saturation count comes from news queries. Ranked candidates.
5. **Pick**Morning Desk Sheet. A human chooses the lead and backup and adds field notes.
6. **Research & verify**A deep-analysis model with web search and fetch, restricted to trusted domains, builds a claims table: every claim carries a source URL, a quoted passage and a label.
7. **Angle & draft**Angle Engine questions → two or three angle options → a draft in the house voice, a visual brief, the exposure line and a disclosure flag.
8. **Approve**Analyst edits; founder approves by risk tier (Part 15). Reason codes are captured on every edit and rejection.
9. **Publish & archive**Manual posting in v1. The post URL, final text and assets are stored, and review dates are set for any forward-looking calls.
10. **Measure & learn**Weekly metrics import, lead attribution, a monthly job that proposes scoring-weight changes. A human accepts or rejects them.


### Recommended stack

| Layer | Phase 1 (weeks 1–6) | Phase 2 (months 2–4) |
| --- | --- | --- |
| Runtime and schedule | Python scripts on the office Windows machine via Task Scheduler (05:30, 16:00, 21:00) | Cloud cron (e.g. GitHub Actions), or Claude Managed Agents scheduled deployments, so it doesn't depend on one PC |
| Storage | SQLite file + daily backup | Postgres (e.g. Supabase) with the same schema (Part 14) |
| Collectors | RSS/Atom (feedparser); Federal Register API; page-change diff for DGFT, CBIC, RBI, DGTR; IMAP parser for ePing and EPC emails; Google News RSS queries | + UN Comtrade API for the exposure line; RASFF and FDA monthly pulls |
| Triage model | Claude Haiku 4.5 (`claude-haiku-4-5`). Overnight items go through the Message Batches API at 50% cost. | Same; tuned on reason codes from rejections |
| Analysis and drafting model | Claude Opus 5 (`claude-opus-5`) with adaptive thinking; server tools `web_search_20260209` / `web_fetch_20260209` restricted with `allowed_domains` for verification runs; structured outputs for the claims table | + prompt caching of the stable voice guide, franchise templates and examples (cuts repeat input cost) |
| Review surface | Desk Sheet emailed at 06:30; approval queue in a shared Google Sheet (status column, reason code, approver) | A small web dashboard: queue, claims table with source links, one-click approve or return |
| Publishing | Manual on LinkedIn (founder); Meta Business Suite scheduling for Instagram | Scheduling tools allowed only for *approved* items. Still no auto-publish. |
| Metrics | Weekly CSV export from LinkedIn and Instagram analytics; UTM links; a lead form with a "which post brought you?" field | Automated imports where APIs allow |


### Guardrails built into the pipeline

- **No claim without a quote.** The structured output schema *requires* `source_url` and `source_quote` for every numeric, date or rule claim. Drafts citing a claim not in the table are rejected automatically.
- **"Unknown" is a valid answer.** Prompts tell the model to leave a field empty rather than infer a rate, date or value.
- **Fetched pages are data, not instructions.** Text from the web is passed as quoted content, and the model is told never to follow instructions found inside it. Verification fetches are domain-restricted.
- **Recency check.** Every claim records the document date. Anything older than the latest amendment in the database is flagged "possibly superseded".
- **Disclosure trigger.** Any story tagged RoDTEP, RoSCTL, scrip or duty credit automatically gets the disclosure line in its draft.
- **Repeat detection.** Similarity search against the last 90 days of posts; near-duplicates are flagged before scoring.
- **Human approval required** at stages 5 and 8. The system has no credentials to publish anything.


### Why not auto-publish?

[OPINION] The brand's entire value is accuracy and judgement. One auto-published misreading of a customs notification would cost more trust than a year of daily posts earns. It also creates a quiet liability: exporters may act on it. The only thing worth considering for automation later is *scheduling* an already-approved post. Even the weekly Freight Pulse chart should get a 30-second human glance.


### Running cost (order of magnitude)

[FORECAST] At list prices, assuming the following daily volumes:

- **Triage:** about 300 items a day on Haiku 4.5 ($1 / $5 per million input/output tokens, half price via Batches).
- **Deep analysis:** about 5 stories a day on Opus 5 ($5 / $25), with roughly 40K tokens in and 5K out each.
- **Drafting:** 2 drafts a day.

Model spend comes to roughly **US$75–150 a month** before web-search tool charges and thinking tokens. Measure real usage from the API's usage fields in week one before trusting this figure. People, not models, are the real cost: the analyst is the essential hire.


### Build sequence

| When | Deliverable |
| --- | --- |
| Weeks 1–2 | Manual desk: source list, alert subscriptions (ePing, Federal Register, EPCs), Google Sheet database, templates. **Publishing starts now**; automation catches up. |
| Weeks 3–6 | Collectors + SQLite + dedup + the 06:30 Desk Sheet email |
| Weeks 7–12 | Triage and scoring model; claims-table research runs; draft generation; approval queue |
| Months 4–6 | Metrics import, Calls Ledger automation, learning job, WhatsApp channel distribution, Postgres migration |
