"""WTO ePing (SPS/TBT notification alerts) email-digest collector.

ePing has no RSS feed or public API — confirmed live on 15 Sep 2026 while
registering an inbox for this collector (registration at eping.wto.org asks
for product/HS/market criteria and an alert frequency, daily or weekly, and
delivers matching notifications only as an email digest to that address; no
feed alternative exists in the registration flow or its documented FAQs).
So "collecting" this source means polling a dedicated inbox over IMAP for
digest mail and parsing each digest's per-notification blocks, rather than
fetching a page or hitting an API like every other collector in this
package.

CAVEAT (2026-09-15): the parser below is built from ePing's *publicly
documented* notification schema (product description, HS/ICS code,
notifying Member, TBT or SPS committee, the notification's document symbol
e.g. "G/TBT/N/USA/1234", a comment-period deadline, and a permalink) —
**not** from a real digest email. The dedicated inbox
(maverickminds.cs@gmail.com) only just finished registering and the first
daily digest has not arrived yet. It tolerates both an HTML digest and a
plain-text one, since which one ePing actually sends is unknown. When a
real digest arrives, re-run tests/test_eping_collector.py against it and
adjust the block/regex parsing below if the real layout differs —
CollectorError messages are written to include a snippet of the unparsed
body specifically to make that fast.

Every notification's document symbol anchors one parsed block: text is
split at each "G/TBT/N/..." or "G/SPS/N/..." occurrence, so this assumes
(per the documented schema) each notification block contains exactly one
such symbol. A digest with no recognisable symbol at all — from a sender
that matched EPING_SENDER_DOMAIN — raises CollectorError rather than
silently returning nothing, exactly like pagewatch.py's "layout may have
changed" handling.
"""
from __future__ import annotations

import email as email_module
import html as html_module
import imaplib
import re
from datetime import datetime
from email.header import decode_header
from email.message import Message
from html.parser import HTMLParser

from dateutil import parser as dateparser

from radar import settings
from radar.collectors.base import Collector, CollectorError, RawItem

DOC_SYMBOL = re.compile(r"G/(TBT|SPS)/N/[A-Z]{1,3}/\d+(?:/(?:Rev|Add|Suppl)\.\d+)*", re.IGNORECASE)
NOTIFICATION_LINK = re.compile(r"^https?://(?:eping\.wto\.org|epingalert\.org|docs\.wto\.org|members\.wto\.org)", re.IGNORECASE)
BRACKET_LINK = re.compile(r"\[(https?://\S+?)\]")
RAW_LINK = re.compile(r"https?://\S+")
DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{4}\b",
        re.IGNORECASE,
    ),
]
TITLE_CONTEXT_CHARS = 160
BODY_MAX_CHARS = 2000


class _InlineLinkHTMLParser(HTMLParser):
    """Flattens HTML to whitespace-normalized text, rewriting <a href="URL">
    label</a> as "label [URL]" so a link stays attached to the text around
    it. Good enough to recover a notification's permalink without needing
    to understand the digest's actual block/table structure (which is
    unknown — see module docstring), and matches this codebase's existing
    no-bs4 convention (pagewatch.py's _PageParser)."""

    SKIP_TAGS = {"style", "script", "head", "title"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._href: str | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "a" and self._href:
            self._parts.append(f" [{self._href}] ")
            self._href = None

    def handle_data(self, data: str) -> None:
        # <style>/<script>/<head> content is never real body text — a real
        # digest email (like the WTO account-activation email observed live,
        # 15 Sep 2026) inlines a full CSS reset in a <style> block, which
        # would otherwise flood every notification's context/body with CSS.
        if self._skip_depth == 0:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in parts
    )


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_bodies(message: Message) -> tuple[str | None, str | None]:
    """Returns (html_body, text_body); either may be None. Prefers the first
    part of each type found, matching how mail clients pick a part to show."""
    html_body: str | None = None
    text_body: str | None = None
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        if content_type == "text/html" and html_body is None:
            html_body = _decode_payload(part)
        elif content_type == "text/plain" and text_body is None:
            text_body = _decode_payload(part)
    return html_body, text_body


def _flatten_html(html_source: str) -> str:
    parser = _InlineLinkHTMLParser()
    parser.feed(html_source)
    return html_module.unescape(parser.text)


def _clean_url(url: str) -> str:
    return url.rstrip("])>.,;\"'")


def _links_in(text: str) -> list[str]:
    bracketed = [m.group(1) for m in BRACKET_LINK.finditer(text)]
    if bracketed:
        return [_clean_url(u) for u in bracketed]
    return [_clean_url(m.group(0)) for m in RAW_LINK.finditer(text)]


def _pick_link(block: str) -> str | None:
    links = _links_in(block)
    for url in links:
        if NOTIFICATION_LINK.match(url):
            return url
    return links[0] if links else None


def _find_date(block: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(block)
        if not match:
            continue
        try:
            return dateparser.parse(match.group(0)).date().isoformat()
        except (ValueError, OverflowError):
            continue
    return None


def _make_title(preceding: str, doc_symbol: str, committee: str) -> str:
    # The documented ePing schema puts the product/measure description
    # right before a notification's document symbol; the tail of that text
    # (closest to the symbol) is kept since a digest stacks several
    # notifications back-to-back and the tail is least likely to still be
    # the previous notification's trailing text.
    snippet = preceding[-TITLE_CONTEXT_CHARS:].strip(" -–—:|")
    label = committee.upper()
    return f"WTO {label} notification {doc_symbol}: {snippet}" if snippet else f"WTO {label} notification {doc_symbol}"


def _notifications_from_text(text: str) -> list[dict]:
    matches = list(DOC_SYMBOL.finditer(text))
    notifications: list[dict] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        context_start = matches[i - 1].end() if i > 0 else max(0, start - 300)
        preceding = text[context_start:start].strip()
        link = _pick_link(block) or _pick_link(preceding)
        if not link:
            continue
        doc_symbol = match.group(0)
        committee = match.group(1)
        full_text = (preceding + " " + block).strip()
        notifications.append({
            "title": _make_title(preceding, doc_symbol, committee),
            "url": link,
            "body": full_text[:BODY_MAX_CHARS],
            # The comment-deadline/distribution date sits in the descriptive
            # text before a notification's symbol (per the documented
            # schema), not after it, so search the combined text.
            "published_at": _find_date(full_text),
        })
    return notifications


def _parse_digest(source_id: str, message: Message) -> list[RawItem]:
    html_body, text_body = _extract_bodies(message)
    flat_text = _flatten_html(html_body) if html_body else (text_body or "")
    notifications = _notifications_from_text(flat_text)
    if not notifications:
        subject = _decode_header_value(message.get("Subject"))
        snippet = flat_text[:300].strip()
        raise CollectorError(
            f"{source_id}: ePing digest {subject!r} matched no notification pattern "
            f"(no G/TBT/N or G/SPS/N document symbol with a usable link found) — "
            f"digest layout may have changed from the documented schema; "
            f"body starts: {snippet!r}"
        )
    return [
        RawItem(
            source_id=source_id,
            title=note["title"],
            url=note["url"],
            body_text=note["body"],
            published_at=note["published_at"],
            publisher=settings.EPING_SENDER_DOMAIN,
        )
        for note in notifications
    ]


class EpingCollector(Collector):
    method = "email_imap"

    def collect(self, source: dict) -> list[RawItem]:
        if not settings.EPING_IMAP_USER or not settings.EPING_IMAP_PASSWORD:
            raise CollectorError(
                f"{source['id']}: EPING_IMAP_USER/EPING_IMAP_PASSWORD not configured (see .env.example)"
            )

        try:
            conn = imaplib.IMAP4_SSL(settings.EPING_IMAP_HOST, settings.EPING_IMAP_PORT)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise CollectorError(f"{source['id']}: IMAP connection to {settings.EPING_IMAP_HOST} failed: {exc}") from exc

        try:
            try:
                conn.login(settings.EPING_IMAP_USER, settings.EPING_IMAP_PASSWORD)
            except imaplib.IMAP4.error as exc:
                raise CollectorError(f"{source['id']}: IMAP login failed: {exc}") from exc

            try:
                status, _ = conn.select("INBOX")
                if status != "OK":
                    raise CollectorError(f"{source['id']}: could not select INBOX")

                status, data = conn.search(None, "UNSEEN", "FROM", f'"{settings.EPING_SENDER_DOMAIN}"')
                if status != "OK":
                    raise CollectorError(f"{source['id']}: IMAP search failed")
            except imaplib.IMAP4.error as exc:
                raise CollectorError(f"{source['id']}: IMAP search failed: {exc}") from exc

            message_ids = data[0].split() if data and data[0] else []
            items: list[RawItem] = []
            for msg_id in message_ids:
                try:
                    status, msg_data = conn.fetch(msg_id, "(RFC822)")
                except imaplib.IMAP4.error as exc:
                    raise CollectorError(f"{source['id']}: IMAP fetch failed for message {msg_id!r}: {exc}") from exc
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    raise CollectorError(f"{source['id']}: IMAP fetch failed for message {msg_id!r}")
                message = email_module.message_from_bytes(msg_data[0][1])
                items.extend(_parse_digest(source["id"], message))
            return items
        finally:
            try:
                conn.logout()
            except Exception:
                pass
