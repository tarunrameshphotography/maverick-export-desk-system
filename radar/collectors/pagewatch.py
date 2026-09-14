"""Page watcher for official sites without RSS (DGFT and similar).

Two parse modes, chosen per source in config/sources.yaml (`parse:`):

- table_rows (DGFT): each <tr> in a listing table is one instrument —
  number, description, date, PDF link. The row becomes a RawItem whose body
  names the instrument ("Notification 74/2025-26 dated 31/03/2026: ...") so
  the entity extractor can pick up the doc number and date.
- anchors (fallback): every link with meaningful text. Noisier; only for
  pages that really are just lists of links.

"New since last run" is not tracked here: the registry skips anything whose
content hash is already in raw_items, for every collector alike.
"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from radar.collectors.base import BROWSER_UA, Collector, CollectorError, RawItem

TIMEOUT_SECONDS = 20
MIN_LINK_TEXT_LENGTH = 12  # filters out nav/footer chrome like "Home", "Sitemap"
DOC_NUMBER_CELL = re.compile(r"^\d{1,3}/\d{4}-\d{2}$")
DATE_CELL = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{4}$")


class _PageParser(HTMLParser):
    """Collects anchors and table rows (cell texts + hrefs) in one pass."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self.rows: list[dict] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._row: dict | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []
            if self._row is not None and self._href:
                self._row["hrefs"].append(self._href)
        elif tag == "tr":
            self._row = {"cells": [], "hrefs": []}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._href is not None:
            self._anchor_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join("".join(self._anchor_text).split())))
            self._href = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _iso_from_day_first(value: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def rows_to_items(source: dict, page_url: str, rows: list[dict]) -> list[RawItem]:
    label = source.get("doc_label", "Notification")
    authority = source.get("authority", "")
    items = []
    for row in rows:
        cells = [c for c in row["cells"] if c]
        number = next((c for c in cells if DOC_NUMBER_CELL.match(c)), None)
        date_raw = next((c for c in cells if DATE_CELL.match(c)), None)
        if not number or not row["hrefs"]:
            continue
        description = max(
            (c for c in cells if c not in (number, date_raw) and not c.isdigit() and not re.fullmatch(r"\d{4}-\d{2}", c)),
            key=len, default="",
        )
        if not description:
            continue
        pdfs = [h for h in row["hrefs"] if h.lower().endswith(".pdf")]
        english = [h for h in pdfs if "eng" in h.lower()]
        link = urljoin(page_url, (english or pdfs or row["hrefs"])[0])
        iso_date = _iso_from_day_first(date_raw) if date_raw else None
        dated = f" dated {date_raw}" if date_raw else ""
        items.append(RawItem(
            source_id=source["id"],
            title=f"{authority} {label} {number}: {description}".strip(),
            url=link,
            body_text=f"{authority} {label} {number}{dated}: {description}".strip(),
            published_at=iso_date,
        ))
    return items


class PagewatchCollector(Collector):
    method = "pagewatch"

    def collect(self, source: dict) -> list[RawItem]:
        url = source["url"]
        try:
            resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": BROWSER_UA})
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CollectorError(f"{source['id']}: page fetch failed: {exc}") from exc

        parser = _PageParser()
        try:
            parser.feed(resp.text)
        except Exception as exc:
            raise CollectorError(f"{source['id']}: HTML parse failed: {exc}") from exc

        if source.get("parse", "anchors") == "table_rows":
            items = rows_to_items(source, url, parser.rows)
            if not items:
                # The page loaded but its table no longer matches — a layout change
                # must surface as a failed source, not as a silent "nothing new".
                raise CollectorError(f"{source['id']}: no instrument rows found; page layout may have changed")
            return items

        items: list[RawItem] = []
        seen_urls: set[str] = set()
        for href, text in parser.anchors:
            if not href or not text or len(text) < MIN_LINK_TEXT_LENGTH:
                continue
            absolute_url = urljoin(url, href)
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            items.append(RawItem(source_id=source["id"], title=text, url=absolute_url))
        return items
