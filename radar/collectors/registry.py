"""Dispatches each active source to the right collector and writes raw_items.
One dead source must never stop the run (Section 29 debuggability / Section 32
"failed-source handling", "retry handling")."""
from __future__ import annotations

import sqlite3
from datetime import date
from urllib.parse import urlsplit

from dateutil import parser as dateparser

from radar import settings
from radar.collectors.base import CollectorError, RawItem, now_iso
from radar.collectors.cbic import CbicCollector
from radar.collectors.federal_register import FederalRegisterCollector
from radar.collectors.pagewatch import PagewatchCollector
from radar.collectors.rss_collector import RssCollector
from radar.collectors.sample_data import SampleDataCollector
from radar.pipeline.normalize import canonicalize_url, compute_content_hash

_COLLECTORS_BY_METHOD = {
    "rss": RssCollector(),
    "api": FederalRegisterCollector(),
    "pagewatch": PagewatchCollector(),
    "cbic_api": CbicCollector(),
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
    publisher = item.publisher or urlsplit(canonical_url).netloc or None
    conn.execute(
        """
        INSERT INTO raw_items (source_id, url, canonical_url, title, body_text,
                                published_at, collected_at, content_hash, language, status, publisher)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
        """,
        (
            item.source_id, item.url, canonical_url, item.title, item.body_text,
            item.published_at, now_iso(), content_hash, item.language, publisher,
        ),
    )
    return True


def add_manual_lead(
    conn: sqlite3.Connection,
    source_id: str,
    title: str,
    url: str,
    body_text: str = "",
    published_at: str | None = None,
    publisher: str | None = None,
) -> int | None:
    """Manual intake for sources with no legitimate automated access (social
    posts, field notes, paywalled articles). The lead becomes an ordinary NEW
    raw_item, so the next pipeline run clusters, scores and verifies it like
    anything collected. Returns the raw_item id, or None if it was a duplicate."""
    source = next((s for s in settings.sources() if s["id"] == source_id), None)
    if source is None:
        raise ValueError(f"Unknown source: {source_id}")
    if source["method"] != "manual":
        raise ValueError(f"{source_id} is collected automatically ({source['method']}); manual intake is for method: manual sources.")
    item = RawItem(source_id=source_id, title=title, url=url, body_text=body_text or "",
                   published_at=published_at, publisher=publisher)
    if not insert_raw_item(conn, item):
        return None
    conn.commit()
    return conn.execute("SELECT MAX(id) AS id FROM raw_items WHERE source_id = ?", (source_id,)).fetchone()["id"]


def is_stale(item: RawItem, lookback_days: int, today: date | None = None) -> bool:
    """True when the item is dated and older than the lookback window. Undated
    items are never stale — their age can't be judged, so they're kept."""
    if not item.published_at:
        return False
    try:
        published = dateparser.parse(item.published_at)
    except (ValueError, OverflowError):
        return False
    today = today or date.today()
    return (today - published.date()).days > lookback_days


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
    lookback_days = settings.collection_config()["lookback_days"]
    sources_checked = 0
    items_collected = 0
    duplicates_found = 0
    stale_skipped = 0
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
            # The sample fixture is a fixed scenario dated Aug-Sep 2026; freshness
            # only applies to live collection.
            if not use_sample and is_stale(item, lookback_days):
                stale_skipped += 1
                continue
            if insert_raw_item(conn, item):
                items_collected += 1
            else:
                duplicates_found += 1

    conn.commit()
    return {
        "sources_checked": sources_checked,
        "items_collected": items_collected,
        "duplicates_found": duplicates_found,
        "stale_skipped": stale_skipped,
        "errors": errors,
    }
