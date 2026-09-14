"""Ties Steps 2-6 of the daily workflow (Section 17) together: normalize/dedupe
already happened at collection time (registry) and clustering time (dedupe.py);
this module runs classify -> extract entities -> analyze exposure -> compute
saturation -> score for every signal still sitting in TRIAGED status, and
rescore_signal() re-runs the same steps after verification or angle work."""
from __future__ import annotations

import sqlite3
from datetime import date
from urllib.parse import urlsplit

from radar.pipeline.classify import classify_topic_category
from radar.pipeline.dedupe import dedupe_and_cluster_new_items
from radar.pipeline.entities import extract_entities, persist_entities
from radar.pipeline.exposure import analyze_indian_exposure
from radar.pipeline.saturation import compute_saturation
from radar.pipeline.scoring import score_signal


def _signal_text_and_sources(conn: sqlite3.Connection, signal_id: str) -> tuple[str, bool, int]:
    """Returns (combined text, has primary evidence, number of independent secondary publishers).

    Primary evidence = a raw item collected from a primary source, OR a claim an
    analyst verified against a primary document (Part 12: confidence 1.0 is
    'primary-verified'). Secondary independence is counted by publisher domain,
    so two items from one outlet never satisfy the two-source rule."""
    rows = conn.execute(
        """
        SELECT ri.title, ri.body_text, ri.canonical_url, ri.url, s.kind
        FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ?
        """,
        (signal_id,),
    ).fetchall()
    text = " ".join(f"{r['title']} {r['body_text'] or ''}" for r in rows)
    verified_primary = conn.execute(
        "SELECT COUNT(*) AS n FROM claims WHERE signal_id = ? AND status = 'verified' AND source_type = 'primary'",
        (signal_id,),
    ).fetchone()["n"]
    has_primary = any(r["kind"] == "primary" for r in rows) or verified_primary > 0
    secondary_publishers = {
        urlsplit(r["canonical_url"] or r["url"] or "").netloc for r in rows if r["kind"] == "secondary"
    } - {""}
    return text, has_primary, len(secondary_publishers)


def process_signal(conn: sqlite3.Connection, signal_id: str, now_iso: str, reference_date: date | None = None) -> dict:
    text, has_primary, secondary_count = _signal_text_and_sources(conn, signal_id)
    entities = extract_entities(text)

    already_extracted = conn.execute(
        "SELECT COUNT(*) AS n FROM signal_entities WHERE signal_id = ?", (signal_id,)
    ).fetchone()["n"]
    if not already_extracted:
        persist_entities(conn, signal_id, entities)

    category = classify_topic_category(entities)
    exposure = analyze_indian_exposure(text, entities, reference_date=reference_date)
    saturation = compute_saturation(conn, signal_id)

    dates = {e["entity_type"]: e["normalized_value"] for e in entities
             if e["entity_type"] in ("effective_date", "deadline", "announcement_date")}
    conn.execute(
        """
        UPDATE signals SET topic_category = ?, exposure_json = ?, saturation_count_72h = ?, saturation_label = ?,
                           effective_date = COALESCE(effective_date, ?), deadline_date = COALESCE(deadline_date, ?),
                           announcement_date = COALESCE(announcement_date, ?)
        WHERE id = ?
        """,
        (category, exposure.to_json(), saturation["count"], saturation["label"],
         dates.get("effective_date"), dates.get("deadline"), dates.get("announcement_date"), signal_id),
    )
    conn.commit()

    breakdown = score_signal(
        conn, signal_id, text, exposure, has_primary, secondary_count,
        saturation["score"], len(entities), now_iso,
    )
    return {"signal_id": signal_id, "category": category, "exposure": exposure, "saturation": saturation, "score": breakdown}


def rescore_signal(conn: sqlite3.Connection, signal_id: str, now_iso: str, reference_date: date | None = None) -> dict:
    """Re-scores after new evidence (a verified primary claim lifts the confidence
    multiplier to 1.0) or a selected angle (lifts angle strength). The signal's
    workflow status is left alone — see score_signal."""
    if conn.execute("SELECT 1 FROM signals WHERE id = ?", (signal_id,)).fetchone() is None:
        raise ValueError(f"No such signal: {signal_id}")
    return process_signal(conn, signal_id, now_iso, reference_date)


def run_pipeline(conn: sqlite3.Connection, now_iso: str, reference_date: date | None = None) -> dict:
    new_signal_ids = dedupe_and_cluster_new_items(conn, now_iso)

    pending = conn.execute("SELECT id FROM signals WHERE status = 'TRIAGED'").fetchall()
    pending_ids = [row["id"] for row in pending]

    results = [process_signal(conn, sid, now_iso, reference_date) for sid in pending_ids]
    high_score = sum(1 for r in results if r["score"]["decision"] in ("lead", "secondary"))
    return {
        "new_signals": new_signal_ids,
        "processed": len(results),
        "high_score_signals": high_score,
        "results": results,
    }
