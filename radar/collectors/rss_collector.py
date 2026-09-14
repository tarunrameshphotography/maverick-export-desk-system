"""Generic RSS/Atom collector (feedparser). Handles both static feed URLs
(PIB, RBI, ET, Business Standard, WTO news) and Google News RSS search
queries, which are built from source['query'] at request time."""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, urlsplit

import feedparser
import requests

from radar.collectors.base import BROWSER_UA, Collector, CollectorError, RawItem

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"
TIMEOUT_SECONDS = 20


def _strip_html(text: str) -> str:
    """Feed summaries often carry markup (Google News wraps them in <a>/<font>)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


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
        # Fetch with requests, not feedparser: feedparser has no timeout, and
        # several Indian publishers (PIB, Business Standard) serve an HTML page
        # instead of the feed to non-browser user agents.
        try:
            resp = requests.get(feed_url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": BROWSER_UA})
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CollectorError(f"{source['id']}: feed fetch failed: {exc}") from exc
        parsed = feedparser.parse(resp.content)

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
            outlet = (entry.get("source") or {}).get("href")  # Google News names the real outlet here
            items.append(
                RawItem(
                    source_id=source["id"],
                    title=title,
                    url=link,
                    body_text=_strip_html(summary),
                    published_at=published,
                    publisher=urlsplit(outlet).netloc.removeprefix("www.") if outlet else None,
                )
            )
        return items
