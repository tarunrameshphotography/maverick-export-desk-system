"""Collapse many raw_items into one canonical signal per underlying event
(Section 6). Similarity uses cheap, inspectable signals rather than an
embedding model: title token overlap (Jaccard), shared entities, and a
publication-date window — matching the spec's "canonical URL, source ID,
publication timestamp, content hash, semantic similarity, entity overlap,
topic/category overlap, dates, named instruments, HS codes, countries,
policy names" list without requiring network calls or an LLM."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateparser

from radar.db.connection import next_sequence
from radar.pipeline.entities import extract_entities

STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "at", "by", "with", "from", "as", "be", "was", "were", "will", "this",
    "that", "it", "its", "india", "indian",
}
TITLE_SIMILARITY_THRESHOLD = 0.35
DATE_WINDOW_DAYS = 10


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def title_similarity(title_a: str, title_b: str) -> float:
    """Jaccard similarity over stopword-filtered tokens. 0.0 if either title
    has no meaningful tokens."""
    tokens_a, tokens_b = _title_tokens(title_a), _title_tokens(title_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = dateparser.parse(value)
    except (ValueError, OverflowError):
        return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _within_date_window(a: str | None, b: str | None, days: int = DATE_WINDOW_DAYS) -> bool:
    dt_a, dt_b = _parse_date(a), _parse_date(b)
    if dt_a is None or dt_b is None:
        return True  # unknown dates never block a match on their own
    return abs((dt_a - dt_b).days) <= days


def entity_overlap(entities_a: list[dict], entities_b: list[dict]) -> float:
    """Jaccard similarity over normalized (entity_type, value) pairs."""
    set_a = {(e["entity_type"], e["normalized_value"]) for e in entities_a}
    set_b = {(e["entity_type"], e["normalized_value"]) for e in entities_b}
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def similarity_score(item_a: dict, item_b: dict) -> float:
    """Combined score in [0, 1]. Title similarity carries most of the weight;
    entity overlap is a secondary confirming signal; dates outside the window
    veto a match outright regardless of text similarity."""
    if not _within_date_window(item_a.get("published_at"), item_b.get("published_at")):
        return 0.0
    title_sim = title_similarity(item_a["title"], item_b["title"])
    ent_a = extract_entities(item_a["title"] + " " + (item_a.get("body_text") or ""))
    ent_b = extract_entities(item_b["title"] + " " + (item_b.get("body_text") or ""))
    entity_sim = entity_overlap(ent_a, ent_b)
    return 0.7 * title_sim + 0.3 * entity_sim


def cluster_raw_items(raw_items: list[dict], threshold: float = TITLE_SIMILARITY_THRESHOLD) -> list[list[dict]]:
    """Greedy single-link clustering: each item joins the first cluster whose
    representative (its first member) it's similar enough to, else starts a
    new cluster. Deterministic given a stable input order."""
    clusters: list[list[dict]] = []
    for item in raw_items:
        placed = False
        for cluster in clusters:
            if similarity_score(item, cluster[0]) >= threshold:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def create_signal_for_cluster(conn: sqlite3.Connection, cluster: list[dict], now_iso: str) -> str:
    """Persists one signals row for a cluster of raw_items, links raw_items to
    it, and returns the new signal_id (e.g. 'SIG-2026-0001')."""
    year = datetime.now(timezone.utc).year
    seq = next_sequence(conn, f"signal_{year}")
    signal_id = f"SIG-{year}-{seq:04d}"

    representative = min(
        cluster, key=lambda i: i.get("published_at") or "9999"
    )  # earliest-published item names the signal and its primary source
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, source_published_at,
                              primary_source_url, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'TRIAGED', ?, ?)
        """,
        (
            signal_id,
            representative["title"],
            now_iso,
            representative.get("published_at"),
            representative.get("canonical_url") or representative.get("url"),
            now_iso,
            now_iso,
        ),
    )
    for item in cluster:
        conn.execute(
            "UPDATE raw_items SET signal_id = ?, status = 'CLUSTERED' WHERE id = ?",
            (signal_id, item["id"]),
        )
    return signal_id


def dedupe_and_cluster_new_items(conn: sqlite3.Connection, now_iso: str) -> list[str]:
    """Pulls every raw_item with status='NEW', clusters them, and creates one
    signals row per cluster. Returns the list of new signal_ids."""
    rows = conn.execute("SELECT * FROM raw_items WHERE status = 'NEW'").fetchall()
    items = [dict(row) for row in rows]
    if not items:
        return []
    items.sort(key=lambda i: i.get("published_at") or "")
    clusters = cluster_raw_items(items)
    signal_ids = [create_signal_for_cluster(conn, cluster, now_iso) for cluster in clusters]
    conn.commit()
    return signal_ids
