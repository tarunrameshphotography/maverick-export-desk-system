"""Generic RSS/Atom collector (feedparser). Handles both static feed URLs
(PIB, RBI, ET, Business Standard, WTO news) and Google News RSS search
queries, which are built from source['query'] at request time."""
from __future__ import annotations

from urllib.parse import quote_plus

import feedparser

from radar.collectors.base import Collector, CollectorError, RawItem

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"


class RssCollector(Collector):
    method = "rss"

    def _feed_url(self, source: dict) -> str:
        url = source["url"]
        query = source.get("query")
        if url.startswith(GOOGLE_NEWS_BASE) and query:
            return f"{GOOGLE_NEWS_BASE}?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        return url

    def collect(self, source: dict) -> list[RawItem]:
        feed_url = self._feed_url(source)
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # feedparser rarely raises, but be defensive
            raise CollectorError(f"{source['id']}: feed fetch failed: {exc}") from exc

        if parsed.bozo and not parsed.entries:
            raise CollectorError(f"{source['id']}: feed parse error: {parsed.bozo_exception}")

        items: list[RawItem] = []
        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = entry.get("summary", "") or entry.get("description", "")
            published = entry.get("published") or entry.get("updated")
            items.append(
                RawItem(
                    source_id=source["id"],
                    title=title,
                    url=link,
                    body_text=summary,
                    published_at=published,
                )
            )
        return items
