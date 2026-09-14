"""Under-coverage / saturation check (Section 14, Part 12 saturation bands).

Counts secondary (news/commentary) items from the last 72 hours that cover the
same story: the secondary items already clustered into this signal, plus
secondary items in *other* signals that share a distinctive entity with it
(an instrument, notification number, HS code or product). Countries and
generic authorities ("India", "DGFT") are deliberately not matching keys —
nearly every item mentions them, which would rate everything saturated.

This is a measurable proxy for "already widely covered", never evidence that
"nobody else reported it" (Section 14).
"""
from __future__ import annotations

import sqlite3

from radar import settings

DISTINCTIVE_ENTITY_TYPES = ("scheme", "regulation", "trade_agreement", "hs_code", "product")
WINDOW_HOURS = 72


def saturation_band_score(item_count: int) -> tuple[int, str]:
    for band in settings.scoring_weights()["saturation_bands"]:
        if item_count <= band["max_items"]:
            return band["score"], band["label"]
    return 1, "heavily saturated"


def count_matching_secondary_items_72h(conn: sqlite3.Connection, signal_id: str) -> int:
    signal = conn.execute("SELECT first_seen_at FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if signal is None:
        return 0
    type_placeholders = ",".join("?" for _ in DISTINCTIVE_ENTITY_TYPES)
    distinctive = [
        r["normalized_value"]
        for r in conn.execute(
            f"SELECT DISTINCT normalized_value FROM signal_entities WHERE signal_id = ? "
            f"AND entity_type IN ({type_placeholders})",
            (signal_id, *DISTINCTIVE_ENTITY_TYPES),
        ).fetchall()
    ]
    in_window = "ABS(julianday(?) - julianday(ri.collected_at)) * 24 <= ?"

    own = {
        r["id"] for r in conn.execute(
            f"""
            SELECT ri.id FROM raw_items ri JOIN sources s ON s.id = ri.source_id
            WHERE s.kind = 'secondary' AND ri.signal_id = ? AND {in_window}
            """,
            (signal_id, signal["first_seen_at"], WINDOW_HOURS),
        ).fetchall()
    }
    related: set[int] = set()
    if distinctive:
        value_placeholders = ",".join("?" for _ in distinctive)
        related = {
            r["id"] for r in conn.execute(
                f"""
                SELECT DISTINCT ri.id
                FROM raw_items ri
                JOIN sources s ON s.id = ri.source_id
                JOIN signal_entities se ON se.signal_id = ri.signal_id
                WHERE s.kind = 'secondary' AND ri.signal_id != ?
                  AND se.entity_type IN ({type_placeholders})
                  AND se.normalized_value IN ({value_placeholders})
                  AND {in_window}
                """,
                (signal_id, *DISTINCTIVE_ENTITY_TYPES, *distinctive, signal["first_seen_at"], WINDOW_HOURS),
            ).fetchall()
        }
    return len(own | related)


def compute_saturation(conn: sqlite3.Connection, signal_id: str) -> dict:
    if not settings.feature_flags().get("saturation_live_query", True):
        return {"count": 0, "score": 5, "label": "under-covered (saturation check disabled)"}
    count = count_matching_secondary_items_72h(conn, signal_id)
    score, label = saturation_band_score(count)
    return {"count": count, "score": score, "label": label}
