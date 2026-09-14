"""Under-coverage / saturation check (Section 14, Part 12 saturation bands).
Counts how many *other* raw_items (news/secondary sources) in the last 72h
share this signal's scheme/authority/country entities — a measurable proxy
for "is this already saturated", never a claim that "nobody else reported it".
"""
from __future__ import annotations

import sqlite3

from radar import settings


def saturation_band_score(item_count: int) -> tuple[int, str]:
    for band in settings.scoring_weights()["saturation_bands"]:
        if item_count <= band["max_items"]:
            return band["score"], band["label"]
    return 1, "heavily saturated"


def count_matching_secondary_items_72h(conn: sqlite3.Connection, signal_id: str) -> int:
    """Counts raw_items from secondary/news sources, excluding this signal's
    own cluster, that share at least one scheme/authority/country entity with
    this signal and were collected within 72 hours of this signal's
    first_seen_at."""
    signal = conn.execute("SELECT first_seen_at FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if signal is None:
        return 0

    entity_values = {
        row["normalized_value"]
        for row in conn.execute(
            "SELECT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type IN ('scheme', 'authority', 'country')",
            (signal_id,),
        ).fetchall()
    }
    if not entity_values:
        return 0

    placeholders = ",".join("?" for _ in entity_values)
    rows = conn.execute(
        f"""
        SELECT DISTINCT ri.id
        FROM raw_items ri
        JOIN sources s ON s.id = ri.source_id
        JOIN signal_entities se ON se.signal_id = ri.signal_id
        WHERE s.kind = 'secondary'
          AND ri.signal_id != ?
          AND se.normalized_value IN ({placeholders})
          AND ABS(julianday(?) - julianday(ri.collected_at)) * 24 <= 72
        """,
        (signal_id, *entity_values, signal["first_seen_at"]),
    ).fetchall()
    return len(rows)


def compute_saturation(conn: sqlite3.Connection, signal_id: str) -> dict:
    if not settings.feature_flags().get("saturation_live_query", True):
        return {"count": 0, "score": 5, "label": "under-covered (saturation check disabled)"}
    count = count_matching_secondary_items_72h(conn, signal_id)
    score, label = saturation_band_score(count)
    return {"count": count, "score": score, "label": label}
