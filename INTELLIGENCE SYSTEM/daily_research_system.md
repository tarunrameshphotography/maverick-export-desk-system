# Daily research system

Every day, the system answers one question: *what happened in global trade today that Indian exporters should know, and which of it can we explain better than anyone?*


### The day at the Desk (IST)

| Time | What happens | Who |
| --- | --- | --- |
| 05:30 | Automated collection sweep. Catches overnight US and EU publications (Federal Register, EU OJ), ePing alerts, late-evening DGFT/CBIC uploads, and news feeds. | Machine |
| 06:30 | Dedup, clustering, triage, first-pass scores. The **Desk Sheet** goes out: top 10 candidates, each with a one-line consequence, sources, score and saturation count. | Machine |
| 07:45 | Pick today's lead and one backup (15 minutes). Flag Watchlist items. | Founder or analyst |
| 08:00–09:30 | Verification pass (protocol below); run the Angle Engine; pull the exposure figure; machine draft and claims table produced. | Analyst + machine |
| 09:30–10:30 | Edit the draft into the founder's voice; build the visual from the template; risk-tier check. | Analyst + designer |
| 10:30 | Approval (Part 15) | Founder |
| 11:00–12:30 | Publish. Initial time window to test: late morning IST on weekdays. Founder replies to comments in the first hour. | Founder |
| 16:00 | Second sweep: afternoon DGFT/CBIC uploads, EU morning publications. Update the Watchlist. | Machine + analyst |
| 21:00 | Late check of DGFT/CBIC (evening notifications are common). Anything urgent becomes tomorrow's lead or a same-evening short post. | Machine alert |

**Speed tiers:**

- **T+0** — simple, primary-sourced facts (e.g. an extension notification).
- **T+1 (default)** — anything needing interpretation.
- **T+3 to T+7** — Second Order, Market Door, Rejection Watch.

Being a day later than the tax portals is fine. Being wrong is not.


### Weekly and monthly rhythm


#### Weekly

- Mon: plan the week, update the Countdown calendar
- Thu: Freight Pulse data (Drewry WCI)
- Fri: Brief assembly, Watchlist review, performance review (30 min)
- Sat: Brief published


#### Monthly

- Mid-month: India trade data → The Number
- RASFF / FDA pull → Rejection Watch
- Calls Ledger grading
- Audience-quality audit (Part 16)
- Scoring-weight review


#### People (minimum viable desk)

- Founder: 60–90 min a day (pick, voice, approve, comments)
- Research analyst: full-time; commerce, law or economics background; owns verification
- Designer: part-time, working from templates
- Automation: one developer for build, then light maintenance


### Verification protocol

1. **Policy, tax, customs and scheme claims** need the primary document open, not a summary of it. Record its number, date and URL.
2. **Numbers** are copied from the source with the exact quoted text stored alongside them. Any derived number (%, growth) is recomputed by the analyst.
3. **Dates** are checked for "effective from" versus "notified on", and converted to IST where relevant.
4. **Two-source rule** for anything not in a primary document: two independent credible outlets, never two outlets repeating one wire story.
5. Every sentence in the draft is tagged [FACT], [INTERPRETATION], [FORECAST] or [OPINION] in the claims table. The published post reflects those tags in its language (the uncertainty ladder).
6. **Pre-mortem line:** "What would make this post wrong in a week?" If the answer is plausible, soften the claim or wait.
7. **Legal-interpretation items** (duty liability, eligibility edge cases) get a quick check with a partner CA or customs broker before publishing.


### Worked example: one development through the system


> **CANDIDATE · FEMA EXPORT REGIME CHANGE · 1 OCT 2026**
>
> **Raw signal:** law-firm and compliance articles say the FEMA (Export and Import of Goods and Services) Regulations, 2026 (notified 13 Jan 2026) take effect on 1 October 2026. A June 2026 amendment reset the old regime's realisation period from 15 months to 9.
>
> **Verification to-do:**
>
> - Open the RBI notification and the Directions.
> - Confirm the effective date, the realisation periods (15 months; 18 for INR invoicing) and the transition treatment for shipments made before 1 Oct.
> - Check whether banks have issued EDF/EDPMS operating instructions.
>
> **Angle Engine:**
>
> - *Who gains:* exporters with long credit terms and INR-invoicing exporters.
> - *Headline gap:* "15 months" coverage ignores the June–September reversion to 9 months under the old rules. Shipments made in that window may still sit under the old clock. [VERIFY]
> - *Action:* check which regime governs each open shipping bill; review payment terms on October contracts; ask your AD bank for its EDF process.
>
> **Score:** 74 while resting on secondary sources; 87 once the RBI text is verified (see Part 12). **Decision:** lead post on 22 Sep and a Countdown item on 28 Sep.
