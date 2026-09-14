"""Primary-document fetch and preservation (Section 26, Section 11).

For every primary source in a signal, fetch the underlying document — not the
headline, not a search summary — and keep a hashed copy under
data/evidence/<signal_id>/. Verification then checks that a claim's quoted
passage actually appears in that stored copy, so "where did this come from?"
always has a checkable answer.

What can't be read automatically is recorded as such, never silently skipped:
PDFs (no PDF dependency in v1; DGFT publishes scans anyway) and aggregator
redirect links (Google News) are marked not_machine_readable with a reason.
"""
from __future__ import annotations

import hashlib
import html
import re
import sqlite3
from urllib.parse import urlsplit

import requests

from radar import settings
from radar.collectors.base import BROWSER_UA
from radar.pipeline.normalize import canonicalize_url

TIMEOUT_SECONDS = 30
FR_DOC_NUMBER = re.compile(r"federalregister\.gov/documents/\d{4}/\d{2}/\d{2}/(\d{4}-\d{4,6})/")


class NotMachineReadable(Exception):
    pass


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def static_unreadable_reason(url: str) -> str | None:
    """The URL-shape half of fetch_text's checks, with no network call — so a
    caller (the Desk Sheet) can flag a document as needing a manual read
    before anyone has run `fetch` on it, not only after."""
    host = urlsplit(url).netloc.lower()
    if host.endswith("news.google.com"):
        return "aggregator redirect link — open it and store the publisher's own URL"
    if urlsplit(url).path.lower().endswith(".pdf"):
        return "PDF — v1 has no PDF reader (DGFT PDFs are often scans); read it and quote by hand"
    return None


def fetch_text(url: str) -> tuple[str, str]:
    """Returns (plain text, URL actually fetched). Raises NotMachineReadable
    with a reason when the document exists but can't be read automatically."""
    reason = static_unreadable_reason(url)
    if reason:
        raise NotMachineReadable(reason)

    match = FR_DOC_NUMBER.search(url)
    if match:
        # The Federal Register API serves the official full text of the document.
        meta = requests.get(
            f"https://www.federalregister.gov/api/v1/documents/{match.group(1)}.json",
            params={"fields[]": ["raw_text_url"]}, timeout=TIMEOUT_SECONDS,
        )
        meta.raise_for_status()
        raw_url = meta.json()["raw_text_url"]
        resp = requests.get(raw_url, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return " ".join(resp.text.split()), raw_url

    resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": BROWSER_UA})
    resp.raise_for_status()
    if "pdf" in resp.headers.get("content-type", "").lower():
        raise NotMachineReadable("served as PDF — read it and quote by hand")
    return _strip_html(resp.text), resp.url


def store_evidence(conn: sqlite3.Connection, signal_id: str, url: str, now_iso: str) -> dict:
    canonical = canonicalize_url(url)
    existing = conn.execute(
        "SELECT * FROM evidence_docs WHERE signal_id = ? AND canonical_url = ?", (signal_id, canonical)
    ).fetchone()
    if existing and existing["status"] == "stored":
        return dict(existing)

    row = {"signal_id": signal_id, "url": url, "canonical_url": canonical, "fetched_at": now_iso,
           "fetched_url": None, "path": None, "sha256": None, "chars": None, "status": "failed", "note": None}
    try:
        text, fetched_url = fetch_text(url)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        folder = settings.DATA_DIR / "evidence" / signal_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest[:12]}.txt"
        path.write_text(f"SOURCE: {url}\nFETCHED: {fetched_url} at {now_iso}\nSHA256: {digest}\n\n{text}", encoding="utf-8")
        row.update(fetched_url=fetched_url, path=str(path), sha256=digest, chars=len(text), status="stored")
    except NotMachineReadable as exc:
        row.update(status="not_machine_readable", note=str(exc))
    except (requests.RequestException, KeyError, ValueError) as exc:
        row.update(status="failed", note=f"fetch failed: {exc}"[:500])

    conn.execute(
        """
        INSERT INTO evidence_docs (signal_id, url, canonical_url, fetched_url, fetched_at, path, sha256, chars, status, note)
        VALUES (:signal_id, :url, :canonical_url, :fetched_url, :fetched_at, :path, :sha256, :chars, :status, :note)
        ON CONFLICT(signal_id, canonical_url) DO UPDATE SET
            fetched_url = excluded.fetched_url, fetched_at = excluded.fetched_at, path = excluded.path,
            sha256 = excluded.sha256, chars = excluded.chars, status = excluded.status, note = excluded.note
        """,
        row,
    )
    conn.commit()
    return row


def fetch_signal_evidence(conn: sqlite3.Connection, signal_id: str, now_iso: str) -> list[dict]:
    """Fetches every primary-source document in the signal (plus its primary link)."""
    urls = [r["url"] for r in conn.execute(
        """
        SELECT DISTINCT ri.url FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ? AND s.kind = 'primary'
        """,
        (signal_id,),
    ).fetchall()]
    primary = conn.execute("SELECT primary_source_url FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if primary and primary["primary_source_url"]:
        urls.append(primary["primary_source_url"])
    seen, results = set(), []
    for url in urls:
        if canonicalize_url(url) in seen:
            continue
        seen.add(canonicalize_url(url))
        results.append(store_evidence(conn, signal_id, url, now_iso))
    return results


def _normalize_for_match(text: str) -> str:
    text = text.replace("``", '"').replace("''", '"').replace("“", '"').replace("”", '"')
    text = text.replace("’", "'").replace("‘", "'")
    return " ".join(text.lower().split())


def stored_text(conn: sqlite3.Connection, signal_id: str, source_url: str) -> str | None:
    row = conn.execute(
        "SELECT path FROM evidence_docs WHERE signal_id = ? AND canonical_url = ? AND status = 'stored'",
        (signal_id, canonicalize_url(source_url)),
    ).fetchone()
    if row is None or not row["path"]:
        return None
    with open(row["path"], encoding="utf-8") as fh:
        return fh.read().split("\n\n", 1)[-1]


def quote_found(conn: sqlite3.Connection, signal_id: str, source_url: str, quote: str) -> bool | None:
    """True/False if a stored copy of source_url exists for this signal; None if not."""
    text = stored_text(conn, signal_id, source_url)
    if text is None:
        return None
    return _normalize_for_match(quote) in _normalize_for_match(text)


def excerpts(text: str, pattern: re.Pattern, limit: int = 12, width: int = 260) -> list[str]:
    """Sentences worth quoting, to speed up the analyst's pass through a long document."""
    sentences = re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)
    out = []
    for s in sentences:
        if pattern.search(s) and 40 <= len(s):
            out.append(s[:width] + ("…" if len(s) > width else ""))
        if len(out) >= limit:
            break
    return out
