# Content scoring model

The goal is not "find an export news article". It is **find the story Maverick Minds can explain better than anyone today.** So the model rewards angle and action as much as importance. It also penalises saturation.


### Step 1: Gates (pass all four or discard)

1. **G1 Evidence:** at least one primary source, or two independent credible secondaries.
2. **G2 Indian implication:** an identifiable effect on Indian exporters or importers.
3. **G3 Not a repeat:** we haven't covered it in the last 14 days, unless there is a genuine new development.
4. **G4 No unverifiable numbers:** the core claim doesn't depend on a figure we can't source.


### Step 2: Weighted criteria (each scored 0–5)

| Criterion | Weight | 5 means… | 1 means… |
| --- | --- | --- | --- |
| Exporter impact (money, time or risk at stake) | 20 | Changes price, cash cycle or market access materially | Cosmetic or symbolic |
| Actionability | 15 | A clear "do this before [date]" | Nothing to do |
| Maverick angle strength | 15 | A second-order insight the headline misses | Only restating the news |
| Indian exposure (breadth × specificity) | 12 | Many exporters, or clearly named HS lines and clusters | Vague, "Indian businesses" |
| Under-coverage (inverse saturation) | 12 | 0–2 Indian items with this angle in 72 h | 10+ items with the same angle |
| Time sensitivity | 10 | A window closes within 30 days | No time element |
| Explainability | 6 | Clear in one post and one visual | Needs a report |
| Audience–business fit | 5 | Core segments (Part 6) | Outside our audiences |
| Evidence quality | 5 | Primary document in hand | Only secondary |

**Base score** = Σ (weight × score ÷ 5), out of 100.
**Final score** = base × confidence multiplier (1.0 primary-verified; 0.85 credible secondary pending verification) − penalties.

**Penalties:**

- −5 for geopolitical or political sensitivity.
- −5 to −15 when the story needs speculation to be interesting.
- −10 for a near-repeat.

**Conflict of interest** (scrip-related) is not penalised. It triggers a mandatory disclosure line instead.

| Final score | Decision |
| --- | --- |
| ≥ 75 | **Lead Signal**: today's post; consider a carousel |
| 60–74 | Secondary post or Saturday Brief item |
| 45–59 | Watchlist, with a named trigger for revisiting |
| < 45 | Discard (logged, so we learn what we skip) |

**Saturation check (automatable):** count Indian news items and LinkedIn posts from the last 72 hours matching the story's entities and angle, from a Google News RSS query plus a manual LinkedIn search.

- 0–2 items scores 5.
- 3–10 items scores 3.
- More than 10 items scores 1, unless our angle differs, in which case score the angle, not the topic.


### Worked scoring: five real candidates (as of 14 Sep 2026)

| Candidate | Impact | Action | Angle | Exposure | Under-cov. | Time | Explain | Fit | Evid. | Base | Final | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RoDTEP/RoSCTL extension ends 30 Sep: price October quotes as if the benefit were zero | 5 | 4 | 4 | 5 | 3 | 5 | 4 | 5 | 5 | 88.0 | 88 | Lead · disclosure required |
| FEMA 2026 regulations effective 1 Oct | 4 | 5 | 4 | 5 | 4 | 5 | 4 | 4 | 4 | 87.4 | 74 → 87 | Lead once RBI text is verified |
| US final CVD, oleoresin paprika from India (FR, 21 Aug 2026) | 3 | 3 | 5 | 2 | 5 | 3 | 3 | 3 | 5 | 70.4 | 70 | Secondary. Broaden to "RoDTEP in US CVD cases" to lift exposure → ~75. |
| Hormuz: Gulf freight costs (restated) | 4 | 3 | 3 | 4 | 2 | 4 | 3 | 3 | 3 | 66.0 | 51 | Watchlist as-is |
| Same story, with the Incoterm angle ("stop quoting CIF to Gulf buyers") | 4 | 5 | 5 | 4 | 2 | 4 | 3 | 3 | 3 | 78.0 | 61 | Secondary post. The angle alone moved it 10 points. |
| August trade data release (headline numbers) | 2 | 1 | 2 | 4 | 1 | 4 | 4 | 3 | 5 | 49.8 | 50 | Watchlist. Post only if a line-level anomaly appears. |

Hormuz rows: base × 0.85 (secondary sources) − 5 (sensitivity). FEMA: × 0.85 until verified. The weights are a starting hypothesis; Part 16 describes how they get recalibrated from outcomes.
