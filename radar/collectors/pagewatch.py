"""Generic page-change watcher for sites without RSS (DGFT, CBIC, ICEGATE, DGTR).

Design choice: rather than maintaining our own before/after HTML snapshot
diff, this collector extracts every plausible notification link from the
page on each run and returns all of them as RawItems. "New since last run"
is then just "not already in raw_items by content_hash" — a property the
registry's insert step already enforces for every collector. This avoids a
second, parallel notion of freshness (Section: prefer small, inspectable
decisions over speculative machinery).
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from radar.collectors.base import Collector, CollectorError, RawItem

TIMEOUT_SECONDS = 20
MIN_LINK_TEXT_LENGTH = 12  # filters out nav/footer chrome like "Home", "Sitemap"


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            self._current_href = href
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            self.anchors.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


class PagewatchCollector(Collector):
    method = "pagewatch"

    def collect(self, source: dict) -> list[RawItem]:
        url = source["url"]
        try:
            resp = requests.get(
                url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0 (compatible; MaverickRadar/1.0)"}
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CollectorError(f"{source['id']}: page fetch failed: {exc}") from exc

        parser = _AnchorExtractor()
        try:
            parser.feed(resp.text)
        except Exception as exc:
            raise CollectorError(f"{source['id']}: HTML parse failed: {exc}") from exc

        items: list[RawItem] = []
        seen_urls: set[str] = set()
        for href, text in parser.anchors:
            if not href or not text or len(text) < MIN_LINK_TEXT_LENGTH:
                continue
            absolute_url = urljoin(url, href)
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            items.append(
                RawItem(
                    source_id=source["id"],
                    title=text,
                    url=absolute_url,
                    body_text="",
                    published_at=None,
                )
            )
        return items
