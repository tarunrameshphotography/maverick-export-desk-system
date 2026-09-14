# Content memory: database design

The database is the Desk's memory. It stops repeats, powers the Ledger, shows which angles earn qualified attention, and becomes the raw material for LEAP. The schema covers every field the brief asked for and adds the ones that make learning possible.

```
-- Sources we monitor
sources(id, name, url, kind[primary|secondary], signal_type[early|confirm|enforced|context],
        cadence, method[rss|api|pagewatch|email|manual], country, reliability_1to5, active)

-- Everything collected, verbatim
raw_items(id, source_id, url, canonical_url, title, body_text, published_at, collected_at,
          content_hash, language)

-- One story = a cluster of raw items about one development
stories(id, title, first_seen_at, source_published_at, primary_source_url,
        primary_doc_ref,              -- e.g. "Notification No. 74/2025-26-DGFT"
        topic_category, lifecycle_stage[draft|notified|effective|enforced|reported],
        countries[], hs_codes[], industries[], clusters[], schemes[],
        effective_date, deadline_date,
        score_base, confidence_mult, penalties, score_final, score_breakdown_json,
        saturation_count_72h, decision[lead|secondary|brief|watchlist|discard],
        decision_reason, watch_trigger, revisit_on,
        previously_covered bool, related_story_ids[], similarity_max)
story_items(story_id, raw_item_id)

-- Atomic claims with evidence
claims(id, story_id, text, claim_type[fact|interpretation|forecast|opinion|speculation],
       source_url, source_quote, source_date, verified_by, verified_at,
       status[verified|pending|failed|superseded])

-- Drafts and versions
drafts(id, story_id, version, franchise, angle, hook, body, format, visual_brief,
       exposure_line, disclosure_required bool, risk_tier[green|amber|red],
       model_used, created_at, edited_by, edit_reason_codes[])

-- What actually went out
posts(id, draft_id, channel[linkedin_founder|linkedin_page|instagram|newsletter|whatsapp],
      language, published_at, url, final_text, format, cta_type,
      audience_segment, funnel_role, commercial_line[none|leap|advisory|incentive],
      status[scheduled|published|corrected|withdrawn])
post_metrics(post_id, captured_at, impressions, reactions, comments, reposts, saves, sends,
             profile_visits, link_clicks, follows, dwell_or_watch_time,
             qualified_comments, qualified_engagers_sampled, qualified_share)

-- Accountability
calls_ledger(id, post_id, call_text, confidence_word[likely|possible], made_on, review_on,
             outcome_text, grade[right|partly|wrong|unresolved], graded_on, graded_by)
corrections(id, post_id, error_text, correction_text, corrected_at, root_cause)

-- Commercial attribution
leads(id, created_at, name_hash, segment, channel, source_post_id, self_reported_source,
      intent[leap|advisory|incentive_sell|incentive_buy|campus|other], stage, value_band)

-- Learning and landscape
approvals(id, draft_id, approver, decision, reason_code, decided_at, time_to_decide_min)
competitor_posts(id, account, archetype, posted_at, topic, format, hook_type,
                 reactions, comments, reposts, sample_commenter_mix_json)
learnings(id, period, finding, evidence_json, action, weight_change_json, accepted bool)
```


### Why the extra fields matter

| Field | What it enables |
| --- | --- |
| `lifecycle_stage`, `source_published_at` vs `published_at` | Measures lead time: how early the Desk is relative to the document and to media coverage. This is the "saw it first" metric. |
| `claims.claim_type`, `source_quote` | Audit trail; the fact/interpretation discipline; defence if challenged |
| `revisit_on`, `watch_trigger`, `calls_ledger` | Follow-ups and the public Ledger. Turns one story into a series. |
| `hs_codes[]`, `clusters[]`, `schemes[]` | "What have we said about Ch. 61 this quarter?"; LEAP module assembly; cluster newsletters |
| `edit_reason_codes`, `approvals.reason_code` | Trains the drafting prompts on what the founder always changes |
| `qualified_share`, `leads.source_post_id` | Optimises for audience quality and business, not likes |
| `disclosure_required`, `risk_tier` | Compliance and trust controls you can prove |
| `decision = discard` (logged) | Checks whether skipped topics later became big. This calibrates the scoring model. |
