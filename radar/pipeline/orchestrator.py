"""Ties Steps 2-6 of the daily workflow (Section 17) together: normalize/dedupe
already happened at collection time (registry) and clustering time (dedupe.py);
this module runs classify -> extract entities -> analyze exposure -> compute
saturation -> score for every signal still sitting in TRIAGED status."""
from __future__ import annotations

import sqlite3

from radar.pipeline.classify import classify_topic_category
from radar.pipeline.dedupe import dedupe_and_cluster_new_items
from radar.pipeline.entities import extract_entities, persist_entities
from radar.pipeline.exposure import IndianExposure, analyze_indian_exposure
from radar.pipeline.saturation import compute_saturation
from radar.pipeline.scoring import score_signal


def _signal_text_and_sources(conn: sqlite3.Connection, signal_id: str) -> tuple[str, bool, int]:
    rows = conn.execute(
        """
        SELECT ri.title, ri.body_text, s.kind
        FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ?
        """,
        (signal_id,),
    ).fetchall()
    text = " ".join(f"{r['title']} {r['body_text'] or ''}" for r in rows)
    has_primary = any(r["kind"] == "primary" for r in rows)
    secondary_count = sum(1 for r in rows if r["kind"] == "secondary")
    return text, has_primary, secondary_count


def process_signal(conn: sqlite3.Connection, signal_id: str, now_iso: str) -> dict:
    text, has_primary, secondary_count = _signal_text_and_sources(conn, signal_id)
    entities = extract_entities(text)

    already_extracted = conn.execute(
        "SELECT COUNT(*) AS n FROM signal_entities WHERE signal_id = ?", (signal_id,)
    ).fetchone()["n"]
    if not already_extracted:
        persist_entities(conn, signal_id, entities)

    category = classify_topic_category(entities)
    exposure = analyze_indian_exposure(text, entities)
    saturation = compute_saturation(conn, signal_id)

    conn.execute(
        "UPDATE signals SET topic_category = ?, exposure_json = ? WHERE id = ?",
        (category, exposure.to_json(), signal_id),
    )
    conn.commit()

    breakdown = score_signal(
        conn, signal_id, text, exposure, has_primary, secondary_count,
        saturation["score"], len(entities), now_iso,
    )
    return {"signal_id": signal_id, "category": category, "exposure": exposure, "saturation": saturation, "score": breakdown}


def run_pipeline(conn: sqlite3.Connection, now_iso: str) -> dict:
    new_signal_ids = dedupe_and_cluster_new_items(conn, now_iso)

    pending = conn.execute("SELECT id FROM signals WHERE status = 'TRIAGED'").fetchall()
    pending_ids = [row["id"] for row in pending]

    results = [process_signal(conn, sid, now_iso) for sid in pending_ids]
    high_score = sum(1 for r in results if r["score"]["decision"] in ("lead", "secondary"))
    return {
        "new_signals": new_signal_ids,
        "processed": len(results),
        "high_score_signals": high_score,
        "results": results,
    }
