"""Morning Desk Sheet (Section 16). Assembles the seven sections a founder
needs to answer "what should we publish today?" in a few minutes, from
whatever the pipeline has already scored — it reads, it never re-scores."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from radar import settings
from radar.pipeline.exposure import IndianExposure
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
    candidates = conn.execute(
        "SELECT * FROM signals WHERE decision IN ('lead', 'secondary') ORDER BY score_final DESC"
    ).fetchall()
    blockers = []
    for row in candidates:
        summary = verification_status_summary(conn, row["id"])
        if not summary["fully_verified"]:
            blockers.append({**dict(row), "verification": summary})
    return blockers


def get_upcoming_deadlines(conn: sqlite3.Connection, reference_date: date | None = None, window_days: int = DEADLINE_WINDOW_DAYS) -> list[dict]:
    reference_date = reference_date or date.today()
    end = reference_date + timedelta(days=window_days)
    rows = conn.execute(
        """
        SELECT se.signal_id, se.entity_type, se.normalized_value, s.title
        FROM signal_entities se JOIN signals s ON s.id = se.signal_id
        WHERE se.entity_type IN ('effective_date', 'deadline')
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
        candidate_entities = _signal_entities_set(conn, signal_id)
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
            other_entities = _signal_entities_set(conn, row["signal_id"])
            if candidate_entities & other_entities:
                matches.append(f"Published {row['published_at']}: {row['url'] or row['signal_id']}")
        if matches:
            warnings[signal_id] = matches
    return warnings


def suggested_action(signal_row: dict, verification: dict | None) -> str:
    if verification is not None and not verification["fully_verified"]:
        return f"Complete verification ({verification['pending']} claim(s) pending) before this can be approved."
    exposure = IndianExposure.from_json(signal_row["exposure_json"]) if signal_row.get("exposure_json") else None
    if exposure and exposure.actionable_this_week == "true":
        return "Publish this week — a deadline or effective date falls within 30 days."
    if signal_row.get("decision") == "lead":
        return "Ready for angle selection and drafting."
    return "Hold on Watchlist until a new development or verification closes the gap."


def assemble_desk_sheet(conn: sqlite3.Connection, reference_date: date | None = None) -> dict:
    reference_date = reference_date or date.today()
    lead, backups = get_lead_and_backups(conn)
    watchlist = get_watchlist(conn)
    blockers = get_verification_blockers(conn)
    deadlines = get_upcoming_deadlines(conn, reference_date)

    candidate_ids = [s["id"] for s in ([lead] if lead else []) + backups]
    memory_warnings = get_content_memory_warnings(conn, candidate_ids)

    blocker_by_id = {b["id"]: b["verification"] for b in blockers}

    return {
        "date": reference_date.isoformat(),
        "lead": lead,
        "lead_action": suggested_action(lead, blocker_by_id.get(lead["id"])) if lead else None,
        "backups": backups,
        "watchlist": watchlist,
        "verification_blockers": blockers,
        "upcoming_deadlines": deadlines,
        "content_memory_warnings": memory_warnings,
    }


def render_desk_sheet_markdown(data: dict) -> str:
    lines = [f"# MAVERICK MORNING DESK — {data['date']}", ""]

    lines.append("## A. Top signal")
    if data["lead"]:
        s = data["lead"]
        lines += [
            f"**{s['title']}**",
            f"- Signal ID: {s['id']}",
            f"- Score: {s['score_final']} ({s['decision']})",
            f"- Category: {s['topic_category']}",
            f"- Primary source: {s['primary_source_url'] or 'not recorded'}",
            f"- Suggested action: {data['lead_action']}",
        ]
    else:
        lines.append("_No signal cleared the gates today._")
    lines.append("")

    lines.append("## B. Backup signals")
    if data["backups"]:
        for s in data["backups"]:
            lines.append(f"- {s['id']} — {s['title']} (score {s['score_final']}, {s['decision']})")
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
    if data["verification_blockers"]:
        for b in data["verification_blockers"]:
            v = b["verification"]
            lines.append(f"- {b['id']} — {b['title']}: {v['pending']} pending, {v['failed']} failed of {v['total']} claims")
    else:
        lines.append("_No blockers — everything in the lead/backup set is fully verified._")
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

    return "\n".join(lines)


def write_desk_sheet(conn: sqlite3.Connection, reference_date: date | None = None) -> tuple[dict, str]:
    data = assemble_desk_sheet(conn, reference_date)
    markdown = render_desk_sheet_markdown(data)
    path = settings.DESK_SHEETS_DIR / f"{data['date']}.md"
    path.write_text(markdown, encoding="utf-8")
    return data, str(path)
