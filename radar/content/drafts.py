"""Content drafting (Section 18) plus the pre-publish checklist from
approval_workflow.md, enforced in code.

Hybrid mode: the system builds a structured scaffold (verified claims, the
selected angle, exposure-line template, disclosure, risk tier) and a writing
brief. The prose itself is written by the analyst or a Claude Code session
and saved back as a new version. Nothing here can publish — record_publication
only logs what a human already posted by hand (Section 30).
"""
from __future__ import annotations

import json
import re
import sqlite3

from radar import settings
from radar.pipeline import exposure_lookup

PLACEHOLDER_PATTERN = re.compile(r"\[(?:HOOK|VERIFY|ACTION|WRITE)[^\]]*\]|_\(fill[^)]*\)_")
NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
DRAFT_STATUSES = {"draft", "edited", "approved", "rejected"}


def _signal_entities(conn: sqlite3.Connection, signal_id: str, entity_type: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type = ?",
        (signal_id, entity_type),
    ).fetchall()
    return [r["normalized_value"] for r in rows]


def disclosure_required(conn: sqlite3.Connection, signal_id: str) -> bool:
    triggers = settings.scoring_weights()["disclosure_triggers"]
    trigger_schemes = {s.lower() for s in triggers["schemes"]}
    schemes = {s.lower().replace(" ", "_") for s in _signal_entities(conn, signal_id, "scheme")}
    category = conn.execute("SELECT topic_category FROM signals WHERE id = ?", (signal_id,)).fetchone()["topic_category"]
    return bool(schemes & trigger_schemes) or category in triggers["categories"]


def risk_tier(conn: sqlite3.Connection, signal_id: str) -> str:
    rules = settings.content_rules()["risk_tier_by_category"]
    category = conn.execute("SELECT topic_category FROM signals WHERE id = ?", (signal_id,)).fetchone()["topic_category"]
    if disclosure_required(conn, signal_id) or category in rules["red"]:
        return "red"
    if category in rules["amber"]:
        return "amber"
    return "green"


def _selected_analysis(conn: sqlite3.Connection, signal_id: str):
    return conn.execute(
        "SELECT * FROM analyses WHERE signal_id = ? AND selected = 1", (signal_id,)
    ).fetchone()


def _exposure_line_template(conn: sqlite3.Connection, signal_id: str) -> str:
    # Prefer HS codes actually stated in the source text (confidence >= 0.9)
    # over chapter-level leads inferred from a product-keyword/cluster match
    # (confidence 0.4, entities.py). Never join the two: a signal whose text
    # merges several clustered raw items can carry an inferred chapter from
    # an unrelated cluster, and joining it alongside a real code would read
    # as one fabricated-looking HS list.
    #
    # No bracket placeholder here even when no figure can be quoted: an
    # honest "not auto-retrieved" sentence (exposure_lookup.exposure_line)
    # is finished content in its own right, distinct from an analyst
    # placeholder lint_draft should still catch elsewhere in the draft.
    stated = conn.execute(
        "SELECT DISTINCT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type = 'hs_code' "
        "AND confidence >= 0.9",
        (signal_id,),
    ).fetchall()
    markets = [c for c in _signal_entities(conn, signal_id, "country") if c != "India"]
    if stated:
        hs_codes = [r["normalized_value"] for r in stated]
        return exposure_lookup.exposure_line(hs_codes, markets)
    inferred = conn.execute(
        "SELECT DISTINCT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type = 'hs_code'",
        (signal_id,),
    ).fetchall()
    if inferred:
        chapter = inferred[0]["normalized_value"]
        return (
            f"No exposure figure quoted: chapter {chapter} is only inferred from the product name, "
            f"not stated in the source, so it is not reliable enough to look up a trade value against."
        )
    return "No HS code extracted yet — cannot quote a trade exposure figure for this signal."


def _next_version(conn: sqlite3.Connection, signal_id: str, asset_slot: str) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS v FROM content_drafts WHERE signal_id = ? AND asset_slot = ?", (signal_id, asset_slot)
    ).fetchone()
    return (row["v"] or 0) + 1


def _insert_draft_row(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    analysis_id: int,
    channel: str,
    asset_slot: str,
    headline: str,
    body: str,
    fmt: str,
    visual_brief: str,
    exposure_line: str,
    source_reference: str,
    suggested_first_comment: str,
    disclosure_required: bool,
    disclosure_line: str | None,
    tier: str,
    status: str,
    now_iso: str,
    edited_by: str,
    edit_reason_codes: list[str] | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO content_drafts (signal_id, analysis_id, channel, asset_slot, version, headline, body, format,
                                     visual_brief, exposure_line, source_reference, suggested_first_comment,
                                     disclosure_required, disclosure_line, risk_tier, status, created_at, edited_by,
                                     edit_reason_codes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id, analysis_id, channel, asset_slot, _next_version(conn, signal_id, asset_slot),
            headline, body, fmt, visual_brief, exposure_line, source_reference, suggested_first_comment,
            int(disclosure_required), disclosure_line, tier, status, now_iso, edited_by,
            json.dumps(edit_reason_codes or []) if edit_reason_codes is not None else None,
        ),
    )
    return cur.lastrowid


def _linkedin_body(verified_claims, analysis, action_hint: str, disclosure: str | None) -> str:
    lines = ["[HOOK — founder voice; name the document, HS line or date. No exclamation marks.]", ""]
    for c in verified_claims:
        lines.append(f"[FACT] {c['claim_text']}")
    lines += ["", f"[INTERPRETATION] {analysis['angle_text']}", "", f"[ACTION — {action_hint}]"]
    if disclosure:
        lines += ["", disclosure]
    return "\n".join(lines)


def _instagram_body(signal, verified_claims, analysis, disclosure: str | None) -> str:
    ig = settings.content_rules()["instagram"]
    lo, hi = ig["reel_seconds"]
    s_lo, s_hi = ig["carousel_slides"]
    facts = "\n".join(f"  - {c['claim_text']}" for c in verified_claims) or "  - [VERIFY: no verified claims yet]"
    parts = [
        f"REEL CONCEPT ({lo}-{hi}s, founder on camera, captions burned in): [WRITE: one practical explanation of '{analysis['angle_label']}']",
        f"LANGUAGE: {ig['language']}",
        "HOOK (first 3s): [WRITE: a concrete question an MSME owner would ask — no income claims]",
        "SCRIPT (Tamil VO notes):",
        "  1. What changed — [WRITE]",
        "  2. Who it affects — [WRITE]",
        "  3. What to do — [WRITE]",
        "ON-SCREEN TEXT (English): [WRITE]",
        f"FACTS AVAILABLE (verified only):\n{facts}",
        "CAPTION: [WRITE: 2-3 lines, source named, LEAP mention only if genuinely relevant]",
        f"CAROUSEL CONCEPT ({s_lo}-{s_hi} slides, built to be saved): [WRITE: slide-by-slide outline]",
        "Do not copy the LinkedIn post. Never: " + "; ".join(ig["never"]) + ".",
    ]
    if disclosure:
        parts.append(disclosure)
    return "\n".join(parts)


# Phase 2 Content Engine (STRATEGY/content_architecture.md, DELIVERABLES):
# one signal produces a *bundle* of named, publish-ready assets instead of a
# single draft per channel. Each slot still follows the hybrid model — a
# structured scaffold with every claim traceable to a verified source, the
# prose written by the analyst/Claude Code session and saved back as a new
# version (save_draft_version). Nothing here writes final prose from thin
# air, and nothing here can publish.
LINKEDIN_VARIANT_EMPHASIS = {
    1: ("Consequence first", "Lead with the direct exporter consequence — who is affected and how, in the first line."),
    2: ("Fine print", "Lead with what the notification's fine print says that the headline coverage misses or gets wrong."),
    3: ("Action window", "Lead with the deadline or action window — what to do this week, and by when."),
}
INSTAGRAM_REEL_CONCEPT = {
    1: "a practical explainer: what changed, who it affects, what to do — in that order",
    2: "a headline-vs-reality contrast: state the headline claim, then what the notification actually says",
}
ASSET_SLOTS = [
    "linkedin_post_1", "linkedin_post_2", "linkedin_post_3",
    "instagram_reel_1", "instagram_reel_2", "instagram_caption",
    "instagram_carousel", "visual_direction",
]
ASSET_SLOT_CHANNEL = {
    "linkedin_post_1": "linkedin", "linkedin_post_2": "linkedin", "linkedin_post_3": "linkedin",
    "instagram_reel_1": "instagram", "instagram_reel_2": "instagram", "instagram_caption": "instagram",
    "instagram_carousel": "instagram", "visual_direction": "instagram",
}


def _linkedin_variant_body(variant: int, verified_claims, analysis, disclosure: str | None) -> str:
    label, guidance = LINKEDIN_VARIANT_EMPHASIS[variant]
    lines = [f"[VARIANT {variant} — {label}. {guidance}]",
             "[HOOK — founder voice; name the document, HS line or date. No exclamation marks.]", ""]
    for c in verified_claims:
        lines.append(f"[FACT] {c['claim_text']}")
    lines += ["", f"[INTERPRETATION] {analysis['angle_text']}", "",
              "[ACTION — what should an exporter do this week?]"]
    if disclosure:
        lines += ["", disclosure]
    return "\n".join(lines)


def _instagram_reel_body(reel_number: int, verified_claims, analysis, disclosure: str | None) -> str:
    ig = settings.content_rules()["instagram"]
    lo, hi = ig["reel_seconds"]
    concept = INSTAGRAM_REEL_CONCEPT[reel_number]
    facts = "\n".join(f"  - {c['claim_text']}" for c in verified_claims) or "  - [VERIFY: no verified claims yet]"
    parts = [
        f"REEL {reel_number} CONCEPT ({lo}-{hi}s, founder on camera, captions burned in): "
        f"[WRITE: {concept}, on '{analysis['angle_label']}']",
        f"LANGUAGE: {ig['language']}",
        "HOOK (first 3s): [WRITE: a concrete question an MSME owner would ask — no income claims]",
        "SCRIPT (Tamil VO notes):",
        "  1. What changed — [WRITE]",
        "  2. Who it affects — [WRITE]",
        "  3. What to do — [WRITE]",
        "ON-SCREEN TEXT (English): [WRITE]",
        f"FACTS AVAILABLE (verified only):\n{facts}",
        "Never: " + "; ".join(ig["never"]) + ".",
    ]
    if disclosure:
        parts.append(disclosure)
    return "\n".join(parts)


def _instagram_caption_body(analysis, primary_ref: str, disclosure: str | None) -> str:
    parts = [
        f"CAPTION for '{analysis['angle_label']}': [WRITE: 2-3 lines, source named, LEAP mention only if genuinely relevant]",
        f"Source to name: {primary_ref}",
    ]
    if disclosure:
        parts.append(disclosure)
    return "\n".join(parts)


def _instagram_carousel_body(verified_claims, analysis, disclosure: str | None) -> str:
    ig = settings.content_rules()["instagram"]
    s_lo, s_hi = ig["carousel_slides"]
    facts = "\n".join(f"  - {c['claim_text']}" for c in verified_claims) or "  - [VERIFY: no verified claims yet]"
    parts = [
        f"CAROUSEL CONCEPT ({s_lo}-{s_hi} slides, built to be saved) for '{analysis['angle_label']}': "
        "[WRITE: slide-by-slide outline]",
        f"FACTS AVAILABLE (verified only):\n{facts}",
        "Do not copy the LinkedIn post — restructure for a saved, swipeable read.",
    ]
    if disclosure:
        parts.append(disclosure)
    return "\n".join(parts)


def _visual_direction_body(analysis, fmt: str) -> str:
    return "\n".join([
        f"VISUAL DIRECTION for '{analysis['angle_label']}' (franchise: {analysis['franchise'] or 'unassigned'}, "
        f"format: {fmt}):",
        "[WRITE: what every visual across this bundle shows — no more precision than the underlying data has.]",
        "- LinkedIn posts: [WRITE: image/document treatment, if any]",
        "- Reels: [WRITE: on-screen graphics, captions style, b-roll if any]",
        "- Carousel: [WRITE: slide layout, chart type if a number is being shown]",
        "Consistency check: the same number or claim shown visually must match the verified claim it illustrates.",
    ])


def create_content_bundle(conn: sqlite3.Connection, signal_id: str, now_iso: str) -> dict[str, int]:
    """Creates the full Phase 2 asset bundle for a signal in one call: three
    LinkedIn post variants, two Reel concepts, a standalone caption, a
    carousel concept, and a visual-direction brief — each a scaffold the
    analyst/Claude Code session fills in, each already carrying its source
    reference back to the signal it was produced from."""
    signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if signal is None:
        raise ValueError(f"No such signal: {signal_id}")
    analysis = _selected_analysis(conn, signal_id)
    if analysis is None:
        raise ValueError(
            f"{signal_id} has no selected angle. A human picks the lead and the angle before drafting (Section 17)."
        )

    verified = conn.execute(
        "SELECT * FROM claims WHERE signal_id = ? AND status = 'verified' ORDER BY id", (signal_id,)
    ).fetchall()
    needs_disclosure = disclosure_required(conn, signal_id)
    disclosure = settings.content_rules()["disclosure_line"] if needs_disclosure else None
    primary_ref = signal["primary_doc_ref"] or signal["primary_source_url"] or "[VERIFY: primary source]"
    fmt = settings.content_rules()["franchise_format"].get(analysis["franchise"], "Text")
    exposure_line = _exposure_line_template(conn, signal_id)
    tier = risk_tier(conn, signal_id)

    bodies: dict[str, str] = {
        "linkedin_post_1": _linkedin_variant_body(1, verified, analysis, disclosure),
        "linkedin_post_2": _linkedin_variant_body(2, verified, analysis, disclosure),
        "linkedin_post_3": _linkedin_variant_body(3, verified, analysis, disclosure),
        "instagram_reel_1": _instagram_reel_body(1, verified, analysis, disclosure),
        "instagram_reel_2": _instagram_reel_body(2, verified, analysis, disclosure),
        "instagram_caption": _instagram_caption_body(analysis, primary_ref, disclosure),
        "instagram_carousel": _instagram_carousel_body(verified, analysis, disclosure),
        "visual_direction": _visual_direction_body(analysis, fmt),
    }

    draft_ids: dict[str, int] = {}
    for slot in ASSET_SLOTS:
        draft_ids[slot] = _insert_draft_row(
            conn, signal_id=signal_id, analysis_id=analysis["id"], channel=ASSET_SLOT_CHANNEL[slot],
            asset_slot=slot, headline=f"{analysis['angle_label']} — {slot}", body=bodies[slot], fmt=fmt,
            visual_brief=f"{fmt} — [WRITE: what the visual shows; no more precision than the data has]",
            exposure_line=exposure_line, source_reference=primary_ref,
            suggested_first_comment=f"Source: {primary_ref}", disclosure_required=needs_disclosure,
            disclosure_line=disclosure, tier=tier, status="draft", now_iso=now_iso, edited_by="system_scaffold",
        )
    conn.commit()
    return draft_ids


def render_bundle_brief(conn: sqlite3.Connection, draft_ids: dict[str, int]) -> str:
    lines = [f"# Content bundle — {len(draft_ids)} assets", ""]
    for slot in ASSET_SLOTS:
        if slot in draft_ids:
            lines.append(f"- {slot}: draft #{draft_ids[slot]}")
    return "\n".join(lines)


def create_draft_scaffold(conn: sqlite3.Connection, signal_id: str, channel: str, now_iso: str) -> int:
    if channel not in ("linkedin", "instagram"):
        raise ValueError(f"Unknown channel: {channel}")
    signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if signal is None:
        raise ValueError(f"No such signal: {signal_id}")
    analysis = _selected_analysis(conn, signal_id)
    if analysis is None:
        raise ValueError(
            f"{signal_id} has no selected angle. A human picks the lead and the angle before drafting (Section 17)."
        )

    verified = conn.execute(
        "SELECT * FROM claims WHERE signal_id = ? AND status = 'verified' ORDER BY id", (signal_id,)
    ).fetchall()
    needs_disclosure = disclosure_required(conn, signal_id)
    disclosure = settings.content_rules()["disclosure_line"] if needs_disclosure else None
    primary_ref = signal["primary_doc_ref"] or signal["primary_source_url"] or "[VERIFY: primary source]"

    if channel == "linkedin":
        body = _linkedin_body(verified, analysis, "what should an exporter do this week?", disclosure)
    else:
        body = _instagram_body(signal, verified, analysis, disclosure)

    fmt = settings.content_rules()["franchise_format"].get(analysis["franchise"], "Text")
    draft_id = _insert_draft_row(
        conn, signal_id=signal_id, analysis_id=analysis["id"], channel=channel, asset_slot=channel,
        headline=analysis["angle_label"], body=body, fmt=fmt,
        visual_brief=f"{fmt} — [WRITE: what the visual shows; no more precision than the data has]",
        exposure_line=_exposure_line_template(conn, signal_id), source_reference=primary_ref,
        suggested_first_comment=f"Source: {primary_ref}", disclosure_required=needs_disclosure,
        disclosure_line=disclosure, tier=risk_tier(conn, signal_id), status="draft", now_iso=now_iso,
        edited_by="system_scaffold",
    )
    conn.commit()
    return draft_id


def save_draft_version(
    conn: sqlite3.Connection,
    draft_id: int,
    now_iso: str,
    edited_by: str,
    edit_reason_codes: list[str] | None = None,
    **changes,
) -> int:
    """Copies the draft into a new version with the given field changes.
    Old versions are never overwritten — the revision trail is the learning data."""
    allowed = {"headline", "body", "visual_brief", "exposure_line", "source_reference", "suggested_first_comment"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot edit fields: {sorted(unknown)}")
    old = dict(conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone())
    new = {**old, **changes}
    draft_id = _insert_draft_row(
        conn, signal_id=new["signal_id"], analysis_id=new["analysis_id"], channel=new["channel"],
        asset_slot=new["asset_slot"], headline=new["headline"], body=new["body"], fmt=new["format"],
        visual_brief=new["visual_brief"], exposure_line=new["exposure_line"],
        source_reference=new["source_reference"], suggested_first_comment=new["suggested_first_comment"],
        disclosure_required=bool(new["disclosure_required"]), disclosure_line=new["disclosure_line"],
        tier=new["risk_tier"], status="edited", now_iso=now_iso, edited_by=edited_by,
        edit_reason_codes=edit_reason_codes or [],
    )
    from radar.publishing import queue as publish_queue

    publish_queue.supersede_for_slot(
        conn, new["signal_id"], new["asset_slot"], now_iso,
        f"slot '{new['asset_slot']}' has a newer version (draft #{draft_id})", edited_by,
    )
    conn.commit()
    return draft_id


def lint_draft(conn: sqlite3.Connection, draft_id: int) -> list[str]:
    """Automates the checkable items of the 10-point pre-publish checklist."""
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    rules = settings.content_rules()
    text = " ".join(filter(None, [draft["headline"], draft["body"], draft["exposure_line"], draft["visual_brief"]]))
    lowered = text.lower()
    issues: list[str] = []

    for placeholder in PLACEHOLDER_PATTERN.findall(text):
        issues.append(f"Unfilled placeholder: {placeholder}")

    for phrase in rules["banned_phrases"]:
        if phrase.lower() in lowered:
            issues.append(f"Banned phrase: '{phrase}'")

    if draft["channel"] == "linkedin":
        li = rules["linkedin"]
        if not li["allow_exclamation"] and "!" in text:
            issues.append("Exclamation mark in LinkedIn copy (voice_bible.md)")
        if not li["allow_emoji"] and EMOJI_PATTERN.search(text):
            issues.append("Emoji in LinkedIn copy (voice_bible.md)")
        words = len((draft["body"] or "").split())
        if not PLACEHOLDER_PATTERN.search(draft["body"] or "") and not (li["min_words"] <= words <= li["max_words"]):
            issues.append(f"Body is {words} words; Signal posts run {li['min_words']}-{li['max_words']}")

    if draft["disclosure_required"] and rules["disclosure_line"] not in (draft["body"] or ""):
        issues.append("Disclosure line required (RoDTEP/RoSCTL/scrip) but missing from body")

    evidence = " ".join(
        f"{r['claim_text']} {r['source_quote'] or ''}"
        for r in conn.execute(
            "SELECT claim_text, source_quote FROM claims WHERE signal_id = ? AND status = 'verified'",
            (draft["signal_id"],),
        ).fetchall()
    )
    evidence_numbers = {n.rstrip("%").replace(",", "") for n in NUMBER_PATTERN.findall(evidence)}
    body = (draft["body"] or "").replace(rules["disclosure_line"], "")
    checked_text = f"{draft['headline'] or ''} {body} {draft['exposure_line'] or ''}"
    for number in NUMBER_PATTERN.findall(checked_text):
        bare = number.rstrip("%").replace(",", "")
        if len(bare) <= 1:
            continue
        if bare not in evidence_numbers:
            issues.append(f"Number '{number}' is not traceable to a verified claim")

    return issues


def set_draft_status(
    conn: sqlite3.Connection,
    draft_id: int,
    status: str,
    reviewer: str,
    now_iso: str,
    reason_code: str | None = None,
    external_check_by: str | None = None,
) -> None:
    from radar.desk.approvals import REASON_CODES

    if status not in DRAFT_STATUSES:
        raise ValueError(f"Unknown draft status: {status}")
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None:
        raise ValueError(f"No such draft: {draft_id}")

    if status == "approved":
        issues = lint_draft(conn, draft_id)
        if issues:
            raise ValueError("Cannot approve; checklist failures:\n- " + "\n- ".join(issues))
        if draft["risk_tier"] in ("amber", "red") and reviewer != "founder":
            raise ValueError(f"{draft['risk_tier']} tier drafts need founder approval (approval_workflow.md)")
        if draft["risk_tier"] == "red" and not external_check_by:
            raise ValueError("Red tier needs a partner CA / customs-broker check recorded (external_check_by)")
    if status == "rejected" and reason_code not in REASON_CODES:
        raise ValueError(f"Rejecting a draft requires a valid reason_code, got {reason_code!r}")

    conn.execute("UPDATE content_drafts SET status = ? WHERE id = ?", (status, draft_id))
    conn.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, from_status, to_status, reviewer, decision, reason_code, decided_at)
        VALUES ('draft', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(draft_id), draft["status"], status, reviewer,
            f"{status}; external check: {external_check_by}" if external_check_by else status,
            reason_code, now_iso,
        ),
    )
    if status != "approved":
        from radar.publishing import queue as publish_queue

        publish_queue.supersede_for_draft(conn, draft_id, now_iso, f"draft #{draft_id} moved to {status}", reviewer)
    conn.commit()


def record_publication(
    conn: sqlite3.Connection,
    draft_id: int,
    url: str,
    final_text: str,
    published_at: str,
    commercial_line: str = "none",
) -> int:
    """Logs a post a human already published by hand. There is deliberately
    no code path that posts to LinkedIn or Instagram. If the draft is in the
    publishing queue, that queue item is marked published too, so the
    dispatcher can never post it a second time."""
    from radar.publishing import queue as publish_queue

    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None or draft["status"] != "approved":
        raise ValueError("Only approved drafts can be recorded as published")
    if draft["disclosure_required"] and settings.content_rules()["disclosure_line"] not in final_text:
        raise ValueError("Published text is missing the required disclosure line")
    active = publish_queue.active_items_for_draft(conn, draft_id)
    if len(active) > 1:
        raise ValueError(f"Draft #{draft_id} has {len(active)} live queue items "
                         f"({', '.join('#' + str(i['id']) for i in active)}); record it with queue-mark-published")
    if active and active[0]["state"] == "publishing":
        raise ValueError(f"Queue item #{active[0]['id']} has an automated attempt in flight; reconcile it instead")
    if not active and conn.execute(
        "SELECT 1 FROM published_content WHERE draft_id = ? AND status = 'published'", (draft_id,)
    ).fetchone():
        raise ValueError(f"Draft #{draft_id} is already recorded as published")
    cur = conn.execute(
        """
        INSERT INTO published_content (draft_id, channel, published_at, url, final_text, status, commercial_line)
        VALUES (?, ?, ?, ?, ?, 'published', ?)
        """,
        (draft_id, draft["channel"], published_at, url, final_text, commercial_line),
    )
    if active:
        from radar.publishing.validation import parse_stored, utc_iso

        stamp = utc_iso(parse_stored(published_at))
        publish_queue.record_item_published(
            conn, active[0], url=url, external_post_id=None, published_at=stamp, actor="manual_record",
            at=stamp, via="manual (record_publication)", published_content_id=cur.lastrowid,
        )
    conn.commit()
    return cur.lastrowid


def render_draft_brief(conn: sqlite3.Connection, draft_id: int) -> str:
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    rules = settings.content_rules()
    lines = [
        f"# Draft brief — draft #{draft_id} ({draft['channel']}, v{draft['version']}, risk tier {draft['risk_tier']})",
        "",
        f"**Working headline:** {draft['headline']}",
        f"**Format:** {draft['format']}",
        f"**Source reference (goes in first comment on LinkedIn):** {draft['source_reference']}",
        "",
        "## Scaffold",
        "```",
        draft["body"],
        "```",
        "",
        f"**Exposure line (must be sourced — never estimated):** {draft['exposure_line']}",
        "",
        "## Rules",
        "- Fact / interpretation / forecast / opinion must be distinguishable in the language (uncertainty ladder).",
        "- Every number must appear in a verified claim. Unverified numbers fail the lint.",
        "- Banned: " + ", ".join(rules["banned_phrases"]),
    ]
    if draft["channel"] == "linkedin":
        li = rules["linkedin"]
        lines.append(f"- {li['min_words']}-{li['max_words']} words, no exclamation marks, no emoji.")
    if draft["disclosure_required"]:
        lines.append(f"- Keep this line verbatim: {rules['disclosure_line']}")
    lines += [
        "- Pre-mortem: what would make this post wrong in a week? If plausible, soften or wait.",
        "- Log any forward-looking call in the Calls Ledger with a review date.",
    ]
    return "\n".join(lines)
