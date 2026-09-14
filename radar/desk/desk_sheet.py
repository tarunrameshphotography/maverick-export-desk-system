"""Morning Desk Sheet (Section 16). Assembles the seven sections a founder
needs to answer "what should we publish today?" in a few minutes, from
whatever the pipeline has already scored — it reads, it never re-scores."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from radar import settings
from radar.pipeline.evidence import static_unreadable_reason
from radar.pipeline.exposure import IndianExposure
from radar.pipeline.exposure_lookup import exposure_line
from radar.pipeline.normalize import canonicalize_url
from radar.pipeline.verify import verification_status_summary

DEADLINE_WINDOW_DAYS = 30
CONTENT_MEMORY_WINDOW_DAYS = 90


def _signal_entities_set(conn: sqlite3.Connection, signal_id: str, entity_types=("scheme", "authority")) -> set[str]:
    placeholders = ",".join("?" for _ in entity_types)
    rows = conn.execute(
        f"SELECT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type IN ({placeholders})",
        (signal_id, *entity_types),
    ).fetchall()
    return {r["normalized_value"] for r in rows}


# Too generic to mean "we already covered this": every US CVD case shares them.
GENERIC_INSTRUMENTS = {"Countervailing Duty", "Anti-Dumping Duty", "Safeguard Duty"}


def _memory_keys(conn: sqlite3.Connection, signal_id: str) -> set[str]:
    values = _signal_entities_set(conn, signal_id, ("scheme", "regulation", "trade_agreement", "product"))
    return values - GENERIC_INSTRUMENTS


def get_lead_and_backups(conn: sqlite3.Connection, backup_count: int = 2) -> tuple[dict | None, list[dict]]:
    rows = conn.execute(
        "SELECT * FROM signals WHERE decision IN ('lead', 'secondary') ORDER BY score_final DESC"
    ).fetchall()
    ranked = [dict(r) for r in rows]
    if not ranked:
        return None, []
    return ranked[0], ranked[1:1 + backup_count]


def get_watchlist(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM signals WHERE decision = 'watchlist' ORDER BY score_final DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_verification_blockers(conn: sqlite3.Connection) -> list[dict]:
    """Candidates whose claims aren't verified yet."""
    candidates = conn.execute(
        "SELECT * FROM signals WHERE decision IN ('lead', 'secondary') ORDER BY score_final DESC"
    ).fetchall()
    blockers = []
    for row in candidates:
        summary = verification_status_summary(conn, row["id"])
        if not summary["fully_verified"]:
            blockers.append({**dict(row), "verification": summary})
    return blockers


def get_evidence_blockers(conn: sqlite3.Connection, limit: int = 8) -> list[dict]:
    """Section 16D: important signals that failed *only* the evidence gate (G1).
    Gate failure still means discard (Part 12), but a strong story with one
    secondary source is a research task — find the primary document — not
    something to lose. Shown only if the rest of its score would clear
    'secondary'."""
    floor = settings.scoring_weights()["decision_thresholds"]["secondary"]
    rows = conn.execute(
        "SELECT * FROM signals WHERE decision = 'discard' AND score_base >= ? ORDER BY score_base DESC",
        (floor,),
    ).fetchall()
    out = []
    for row in rows:
        failed = json.loads(row["score_breakdown_json"] or "{}").get("gates", {}).get("failed", [])
        if failed == ["G1_evidence"]:
            out.append(dict(row))
    return out[:limit]


def get_upcoming_deadlines(conn: sqlite3.Connection, reference_date: date | None = None, window_days: int = DEADLINE_WINDOW_DAYS) -> list[dict]:
    """Dates from live signals only — a deadline inside a discarded signal
    (e.g. a bank-specific RBI direction) is not an exporter deadline."""
    reference_date = reference_date or date.today()
    end = reference_date + timedelta(days=window_days)
    rows = conn.execute(
        """
        SELECT se.signal_id, se.entity_type, se.normalized_value, s.title
        FROM signal_entities se JOIN signals s ON s.id = se.signal_id
        WHERE se.entity_type IN ('effective_date', 'deadline')
          AND COALESCE(s.decision, '') != 'discard' AND s.status NOT IN ('REJECTED', 'ARCHIVED')
        """,
    ).fetchall()
    deadlines = []
    for row in rows:
        try:
            target = date.fromisoformat(row["normalized_value"])
        except ValueError:
            continue
        if reference_date <= target <= end:
            deadlines.append(
                {"signal_id": row["signal_id"], "title": row["title"], "type": row["entity_type"], "date": row["normalized_value"]}
            )
    return sorted(deadlines, key=lambda d: d["date"])


def get_content_memory_warnings(conn: sqlite3.Connection, candidate_signal_ids: list[str], window_days: int = CONTENT_MEMORY_WINDOW_DAYS) -> dict[str, list[str]]:
    """For each candidate signal, lists prior published posts that share a
    scheme/authority entity within the lookback window — Section 16F."""
    warnings: dict[str, list[str]] = {}
    for signal_id in candidate_signal_ids:
        candidate_entities = _memory_keys(conn, signal_id)
        if not candidate_entities:
            continue
        rows = conn.execute(
            """
            SELECT pc.published_at, pc.url, cd.signal_id
            FROM published_content pc JOIN content_drafts cd ON cd.id = pc.draft_id
            WHERE pc.status = 'published' AND cd.signal_id != ?
            """,
            (signal_id,),
        ).fetchall()
        matches = []
        for row in rows:
            if not row["published_at"]:
                continue
            try:
                published = datetime.fromisoformat(row["published_at"]).date()
            except ValueError:
                continue
            if (date.today() - published).days > window_days:
                continue
            other_entities = _memory_keys(conn, row["signal_id"])
            if candidate_entities & other_entities:
                matches.append(f"Published {row['published_at']}: {row['url'] or row['signal_id']}")
        if matches:
            warnings[signal_id] = matches
    return warnings


def suggested_action(signal_row: dict, verification: dict | None) -> str:
    if verification is not None and verification["total"] == 0:
        return (f"No checkable claims in the headline alone — open the primary document, then record claims "
                f"with `python -m radar claim-add {signal_row['id']} ...`.")
    if verification is not None and not verification["fully_verified"]:
        return (f"Verify {verification['pending']} claim(s) against the primary document: "
                f"`python -m radar verify {signal_row['id']}`.")
    exposure = IndianExposure.from_json(signal_row["exposure_json"]) if signal_row.get("exposure_json") else None
    if exposure and exposure.actionable_this_week == "true":
        return "Publish this week — a deadline or effective date falls within 30 days."
    if signal_row.get("decision") == "lead":
        return "Ready for angle selection and drafting."
    return "Hold on Watchlist until a new development or verification closes the gap."


_EXPOSURE_LABELS = [
    ("landed_cost_change", "changes price or landed cost"),
    ("cash_cycle_change", "changes the cash cycle"),
    ("market_access_change", "changes market access"),
    ("compliance_cost_change", "adds compliance cost"),
    ("competitive_positioning_change", "shifts competitive position"),
    ("opportunity", "opens an opportunity"),
    ("threat", "creates a threat"),
    ("actionable_this_week", "has a date inside 30 days"),
]


def signal_card(conn: sqlite3.Connection, signal: dict) -> dict:
    """Everything Section 16A asks for about one candidate, drawn only from
    what the pipeline already stored — no new inference happens here."""
    exposure = IndianExposure.from_json(signal["exposure_json"]) if signal.get("exposure_json") else IndianExposure()
    why = [label for key, label in _EXPOSURE_LABELS if getattr(exposure, key) == "true"]
    unknown = [label for key, label in _EXPOSURE_LABELS if getattr(exposure, key) == "uncertain"]

    sources = conn.execute(
        """
        SELECT s.name, s.kind, ri.canonical_url, ri.url, ri.publisher
        FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ? ORDER BY s.kind, s.reliability_1to5 DESC
        """,
        (signal["id"],),
    ).fetchall()
    source_counts: dict[str, int] = {}
    for r in sources:
        label = f"{r['publisher'] or r['name']} ({r['kind']})"
        source_counts[label] = source_counts.get(label, 0) + 1
    primary_url = signal["primary_source_url"]
    primary_row = next((r for r in sources if (r["canonical_url"] or r["url"]) == primary_url), None)
    link_label = (primary_row["publisher"] if primary_row and primary_row["publisher"] else "source")
    selected = conn.execute(
        "SELECT franchise, angle_label FROM analyses WHERE signal_id = ? AND selected = 1", (signal["id"],)
    ).fetchone()
    rules = settings.content_rules()
    franchise = selected["franchise"] if selected else rules["franchise_by_category"].get(
        signal["topic_category"], rules["franchise_by_category"]["default"]
    )
    schemes = [r["normalized_value"] for r in conn.execute(
        "SELECT DISTINCT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type IN ('scheme','regulation','authority')",
        (signal["id"],),
    ).fetchall()]
    breakdown = json.loads(signal["score_breakdown_json"]) if signal.get("score_breakdown_json") else {}

    has_primary_source = any(r["kind"] == "primary" for r in sources)
    secondary_publishers = {r["publisher"] for r in sources if r["kind"] == "secondary" and r["publisher"]}
    claim_count = conn.execute(
        "SELECT COUNT(*) AS n FROM claims WHERE signal_id = ?", (signal["id"],)
    ).fetchone()["n"]

    return {
        "id": signal["id"],
        "title": signal["title"],
        "why_it_matters": (", ".join(why) if why else "no concrete effect established yet"),
        "unknowns": unknown,
        "countries": ["India"] * (exposure.direct_effect == "true") + exposure.destination_markets,
        "products": exposure.affected_products + [f"HS {h}" for h in exposure.hs_codes]
                    + [f"{c} cluster" for c in exposure.clusters],
        "category": signal["topic_category"],
        "instruments": schemes,
        "exposure": f"direct effect: {exposure.direct_effect}; confidence {exposure.confidence}",
        "exposure_notes": exposure.notes,
        "exposure_value": exposure_line(exposure.hs_codes, exposure.destination_markets),
        "evidence_quality_label": _evidence_quality_label(has_primary_source, len(secondary_publishers), claim_count),
        "manual_read_required": _manual_read_flag(conn, signal["id"], primary_url),
        "sources": [f"{label} x{n}" if n > 1 else label for label, n in source_counts.items()],
        "primary_source_url": primary_url,
        "primary_link_md": f"[{link_label}]({primary_url})" if primary_url else "not recorded",
        "doc_ref": signal.get("primary_doc_ref"),
        "score": signal["score_final"],
        "score_detail": (
            f"base {breakdown.get('base_score')} x {breakdown.get('confidence_mult')} "
            f"- penalties {breakdown.get('penalties_total')} {breakdown.get('penalties_applied') or ''}".strip()
        ),
        "decision": signal["decision"],
        "coverage": f"{signal.get('saturation_label') or 'unknown'} ({signal.get('saturation_count_72h') or 0} matching items / 72h)",
        "angle": selected["angle_label"] if selected else "pending — run `python -m radar angle-brief " + signal["id"] + "`",
        "franchise": franchise + ("" if selected else " (default for category — confirm in angle step)"),
        "format": rules["franchise_format"].get(franchise, "Text"),
        "disclosure": _needs_disclosure(conn, signal["id"]),
    }


def _manual_read_flag(conn: sqlite3.Connection, signal_id: str, primary_url: str | None) -> str | None:
    """Section 16's PDF/document-handling ask: make it obvious in the Desk
    Sheet when a signal's primary document needs a human to open and read it,
    rather than only discovering that after someone runs `fetch`."""
    if not primary_url:
        return None
    stored = conn.execute(
        "SELECT status, note FROM evidence_docs WHERE signal_id = ? AND canonical_url = ?",
        (signal_id, canonicalize_url(primary_url)),
    ).fetchone()
    if stored:
        return stored["note"] if stored["status"] == "not_machine_readable" else None
    return static_unreadable_reason(primary_url)


def _evidence_quality_label(has_primary_source: bool, secondary_source_count: int, claim_count: int) -> str:
    source_label = (
        "primary source" if has_primary_source
        else (f"{secondary_source_count} independent secondary sources" if secondary_source_count >= 2
              else "single secondary source — weak")
    )
    if claim_count == 0:
        return f"{source_label}, but 0 claims extracted yet — headline only, verify before treating as strong"
    return f"{source_label}, {claim_count} claim(s) extracted"


def _needs_disclosure(conn: sqlite3.Connection, signal_id: str) -> bool:
    from radar.content.drafts import disclosure_required

    return disclosure_required(conn, signal_id)


def assemble_desk_sheet(conn: sqlite3.Connection, reference_date: date | None = None) -> dict:
    reference_date = reference_date or date.today()
    lead, backups = get_lead_and_backups(conn)
    watchlist = get_watchlist(conn)
    blockers = get_verification_blockers(conn)
    deadlines = get_upcoming_deadlines(conn, reference_date)

    candidate_ids = [s["id"] for s in ([lead] if lead else []) + backups]
    memory_warnings = get_content_memory_warnings(conn, candidate_ids)

    blocker_by_id = {b["id"]: b["verification"] for b in blockers}

    counts = {r["decision"]: r["n"] for r in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM signals GROUP BY decision").fetchall()}

    return {
        "date": reference_date.isoformat(),
        "counts": counts,
        "lead_threshold": settings.scoring_weights()["decision_thresholds"]["lead_signal"],
        "lead": lead,
        "lead_card": signal_card(conn, lead) if lead else None,
        "lead_action": suggested_action(lead, blocker_by_id.get(lead["id"])) if lead else None,
        "backups": backups,
        "backup_cards": [signal_card(conn, b) for b in backups],
        "backup_actions": [suggested_action(b, blocker_by_id.get(b["id"])) for b in backups],
        "watchlist": watchlist,
        "verification_blockers": blockers,
        "evidence_blockers": get_evidence_blockers(conn),
        "upcoming_deadlines": deadlines,
        "content_memory_warnings": memory_warnings,
    }


def _render_card(card: dict, action: str | None = None) -> list[str]:
    lines = [
        f"**{card['title']}**",
        f"- Signal: `{card['id']}` · score **{card['score']}** ({card['decision']}) — {card['score_detail']}",
        f"- Why it matters: {card['why_it_matters']}",
        f"- Countries: {', '.join(card['countries']) or 'none extracted'}",
        f"- Products / sector: {', '.join(card['products']) or 'none extracted'} · category `{card['category']}`",
        f"- Instruments: {', '.join(card['instruments']) or 'none extracted'}",
        f"- Indian exposure: {card['exposure']}",
        f"- Export exposure: {card['exposure_value']}",
        f"- Coverage: {card['coverage']}",
        f"- Evidence quality: {card['evidence_quality_label']}",
        f"- Sources: {'; '.join(card['sources'])}",
        f"- Primary link: {card['primary_link_md']}" + (f" · {card['doc_ref']}" if card.get("doc_ref") else ""),
        f"- Suggested angle: {card['angle']}",
        f"- Franchise / format: {card['franchise']} — {card['format']}",
    ]
    if card.get("manual_read_required"):
        lines.append(f"- MANUAL READ REQUIRED: {card['manual_read_required']}")
    if card["unknowns"]:
        lines.append(f"- Not yet established: {', '.join(card['unknowns'])}")
    if card["disclosure"]:
        lines.append("- **Disclosure required** (RoDTEP/RoSCTL/scrip) · red risk tier")
    if action:
        lines.append(f"- Next step: {action}")
    return lines


def render_desk_sheet_markdown(data: dict) -> str:
    c = data.get("counts", {})
    lines = [
        f"# MAVERICK MORNING DESK — {data['date']}",
        "",
        f"**Today:** {c.get('lead', 0)} lead · {c.get('secondary', 0)} secondary · {c.get('watchlist', 0)} watchlist · "
        f"{c.get('discard', 0)} discarded (logged).",
        "",
    ]

    lines.append("## A. Top signal")
    if data["lead"]:
        if data["lead"]["decision"] != "lead":
            lines.append(f"_Nothing reached the Lead threshold ({data['lead_threshold']}) before verification and "
                         f"angle work. This is the strongest candidate; scores rise once claims are verified "
                         f"and an angle is selected._")
            lines.append("")
        lines += _render_card(data["lead_card"], data["lead_action"])
    else:
        lines.append("_No signal cleared the gates today._")
    lines.append("")

    lines.append("## B. Backup signals")
    if data["backup_cards"]:
        for card, action in zip(data["backup_cards"], data.get("backup_actions", [None] * len(data["backup_cards"]))):
            lines += _render_card(card, action) + [""]
    else:
        lines.append("_No backups available._")
    lines.append("")

    lines.append("## C. Watchlist")
    if data["watchlist"]:
        for s in data["watchlist"]:
            lines.append(f"- {s['id']} — {s['title']} (score {s['score_final']})")
    else:
        lines.append("_Nothing on the watchlist._")
    lines.append("")

    lines.append("## D. Verification blockers")
    today_ids = {s["id"] for s in ([data["lead"]] if data["lead"] else []) + data["backups"]}
    shown = [b for b in data["verification_blockers"] if b["id"] in today_ids]
    rest = len(data["verification_blockers"]) - len(shown)
    lines.append("**Today's picks, not yet verified:**")
    if shown:
        for b in shown:
            v = b["verification"]
            status = ("no claims extracted — read the primary document" if v["total"] == 0
                      else f"{v['pending']} of {v['total']} claims pending, {v['failed']} failed")
            lines.append(f"- `{b['id']}` — {b['title'][:110]}: {status}")
    else:
        lines.append("- _None — today's picks are verified._")
    if rest > 0:
        lines.append(f"- _+{rest} other candidates also await verification: `python -m radar signals --decision secondary`_")
    lines.append("")
    lines.append("**Strong stories with no primary source yet** (failed G1 only; find the primary document "
                 "or a second independent outlet, then `rescore`):")
    if data.get("evidence_blockers"):
        for b in data["evidence_blockers"]:
            lines.append(f"- `{b['id']}` — {b['title'][:110]} (score before gates {b['score_base']}) — "
                         f"[single source]({b['primary_source_url']})")
    else:
        lines.append("- _None._")
    lines.append("")

    lines.append("## E. Upcoming deadlines (next 30 days)")
    if data["upcoming_deadlines"]:
        for d in data["upcoming_deadlines"]:
            lines.append(f"- {d['date']} ({d['type']}) — {d['title']} [{d['signal_id']}]")
    else:
        lines.append("_None extracted in this window._")
    lines.append("")

    lines.append("## F. Content memory warnings")
    if data["content_memory_warnings"]:
        for signal_id, matches in data["content_memory_warnings"].items():
            lines.append(f"- {signal_id}: " + "; ".join(matches))
    else:
        lines.append("_No overlap with anything published in the last 90 days._")
    lines.append("")

    lines.append("## G. Suggested action")
    lines.append(data["lead_action"] or "No lead today — review the Watchlist for tomorrow's candidates.")

    health = data.get("run_health")
    if health:
        lines += [
            "",
            "## Run health",
            f"- Sources checked: {health['sources_checked']} · new items: {health['items_collected']} · "
            f"already-seen items: {health['duplicates_found']} · failed sources: {len(health['errors'])}",
        ]
        for err in health["errors"]:
            lines.append(f"  - {err[:200]}")
        lines.append("- Model calls: 0 (hybrid mode — triage and scoring are rule-based)")

    lines += ["", "---", "_Nothing in this sheet has been published. Every item needs human selection, "
              "verification and approval (Section 30)._"]
    return "\n".join(lines)


def write_desk_sheet(
    conn: sqlite3.Connection,
    reference_date: date | None = None,
    suffix: str = "",
    run_health: dict | None = None,
) -> tuple[dict, str]:
    data = assemble_desk_sheet(conn, reference_date)
    data["run_health"] = run_health
    markdown = render_desk_sheet_markdown(data)
    path = settings.DESK_SHEETS_DIR / f"{data['date']}{suffix}.md"
    path.write_text(markdown, encoding="utf-8")
    return data, str(path)
