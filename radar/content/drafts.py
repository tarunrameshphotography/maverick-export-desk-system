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
    hs = ", ".join(_signal_entities(conn, signal_id, "hs_code")) or "[VERIFY: HS code]"
    markets = [c for c in _signal_entities(conn, signal_id, "country") if c != "India"]
    market = ", ".join(markets) or "[VERIFY: market]"
    return (
        f"India exported [VERIFY: value] of HS {hs} to {market} in [VERIFY: period] "
        f"(source: [VERIFY: TradeStat / UN Comtrade])."
    )


def _next_version(conn: sqlite3.Connection, signal_id: str, channel: str) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS v FROM content_drafts WHERE signal_id = ? AND channel = ?", (signal_id, channel)
    ).fetchone()
    return (row["v"] or 0) + 1


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
    cur = conn.execute(
        """
        INSERT INTO content_drafts (signal_id, analysis_id, channel, version, headline, body, format,
                                     visual_brief, exposure_line, source_reference, suggested_first_comment,
                                     disclosure_required, disclosure_line, risk_tier, status, created_at, edited_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, 'system_scaffold')
        """,
        (
            signal_id, analysis["id"], channel, _next_version(conn, signal_id, channel),
            analysis["angle_label"], body, fmt,
            f"{fmt} — [WRITE: what the visual shows; no more precision than the data has]",
            _exposure_line_template(conn, signal_id), primary_ref,
            f"Source: {primary_ref}", int(needs_disclosure), disclosure, risk_tier(conn, signal_id), now_iso,
        ),
    )
    conn.commit()
    return cur.lastrowid


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
    cur = conn.execute(
        """
        INSERT INTO content_drafts (signal_id, analysis_id, channel, version, headline, body, format,
                                     visual_brief, exposure_line, source_reference, suggested_first_comment,
                                     disclosure_required, disclosure_line, risk_tier, status, created_at,
                                     edited_by, edit_reason_codes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'edited', ?, ?, ?)
        """,
        (
            new["signal_id"], new["analysis_id"], new["channel"], _next_version(conn, new["signal_id"], new["channel"]),
            new["headline"], new["body"], new["format"], new["visual_brief"], new["exposure_line"],
            new["source_reference"], new["suggested_first_comment"], new["disclosure_required"],
            new["disclosure_line"], new["risk_tier"], now_iso, edited_by, json.dumps(edit_reason_codes or []),
        ),
    )
    conn.commit()
    return cur.lastrowid


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
    no code path that posts to LinkedIn or Instagram."""
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None or draft["status"] != "approved":
        raise ValueError("Only approved drafts can be recorded as published")
    if draft["disclosure_required"] and settings.content_rules()["disclosure_line"] not in final_text:
        raise ValueError("Published text is missing the required disclosure line")
    cur = conn.execute(
        """
        INSERT INTO published_content (draft_id, channel, published_at, url, final_text, status, commercial_line)
        VALUES (?, ?, ?, ?, ?, 'published', ?)
        """,
        (draft_id, draft["channel"], published_at, url, final_text, commercial_line),
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
