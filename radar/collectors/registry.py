"""Dispatches each active source to the right collector and writes raw_items.
One dead source must never stop the run (Section 29 debuggability / Section 32
"failed-source handling", "retry handling")."""
from __future__ import annotations

import sqlite3

from radar import settings
from radar.collectors.base import CollectorError, RawItem, now_iso
from radar.collectors.federal_register import FederalRegisterCollector
from radar.collectors.pagewatch import PagewatchCollector
from radar.collectors.rss_collector import RssCollector
from radar.collectors.sample_data import SampleDataCollector
from radar.pipeline.normalize import canonicalize_url, compute_content_hash

_COLLECTORS_BY_METHOD = {
    "rss": RssCollector(),
    "api": FederalRegisterCollector(),
    "pagewatch": PagewatchCollector(),
}

MAX_RETRIES = 2


def _active_sources() -> list[dict]:
    return [s for s in settings.sources() if s.get("active", True)]


def insert_raw_item(conn: sqlite3.Connection, item: RawItem) -> bool:
    """Insert item if its content_hash isn't already present. Returns True if
    a new row was inserted, False if it was a duplicate of an existing raw_item."""
    canonical_url = canonicalize_url(item.url)
    content_hash = compute_content_hash(canonical_url, item.title)
    existing = conn.execute(
        "SELECT id FROM raw_items WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """
        INSERT INTO raw_items (source_id, url, canonical_url, title, body_text,
                                published_at, collected_at, content_hash, language, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW')
        """,
        (
            item.source_id, item.url, canonical_url, item.title, item.body_text,
            item.published_at, now_iso(), content_hash, item.language,
        ),
    )
    return True


def _collect_with_retries(collector, source: dict) -> list[RawItem]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return collector.collect(source)
        except CollectorError as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def run_collection(conn: sqlite3.Connection, run_id: int, use_sample: bool = False) -> dict:
    """Collects from every active source (or the sample fixture), inserts new
    raw_items, and logs failures to source_failures. Returns summary counts
    used to populate system_runs."""
    sample_collector = SampleDataCollector()
    sources_checked = 0
    items_collected = 0
    duplicates_found = 0
    errors: list[str] = []

    for source in _active_sources():
        sources_checked += 1
        collector = sample_collector if use_sample else _COLLECTORS_BY_METHOD.get(source["method"])
        if collector is None:
            continue  # method not yet automated (e.g. email/manual) — registered, not collected
        try:
            raw_items = _collect_with_retries(collector, source)
        except CollectorError as exc:
            errors.append(str(exc))
            conn.execute(
                """
                INSERT INTO source_failures (source_id, run_id, occurred_at, error_text, retry_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source["id"], run_id, now_iso(), str(exc), MAX_RETRIES),
            )
            continue

        for item in raw_items:
            if insert_raw_item(conn, item):
                items_collected += 1
            else:
                duplicates_found += 1

    conn.commit()
    return {
        "sources_checked": sources_checked,
        "items_collected": items_collected,
        "duplicates_found": duplicates_found,
        "errors": errors,
    }
