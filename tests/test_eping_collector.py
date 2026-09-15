"""Tests for radar/collectors/eping.py.

All IMAP interaction is mocked (imaplib.IMAP4_SSL is monkeypatched to a fake
in-memory server) and the digest fixtures below are hand-built from ePing's
*publicly documented* notification schema — no real ePing digest has been
seen (the dedicated inbox only just finished registering, see
CLAUDE.md / sources.yaml's wto_eping notes). These are mock/fixture tests
only, not a live-verified parser; they should be re-checked once a real
digest arrives.
"""
from __future__ import annotations

import email
import imaplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from radar import settings
from radar.collectors import eping
from radar.collectors.base import CollectorError

SOURCE = {"id": "wto_eping"}


def _configure_credentials(monkeypatch, user="maverickminds.cs@gmail.com", password="app-password"):
    monkeypatch.setattr(settings, "EPING_IMAP_USER", user)
    monkeypatch.setattr(settings, "EPING_IMAP_PASSWORD", password)
    monkeypatch.setattr(settings, "EPING_IMAP_HOST", "imap.gmail.com")
    monkeypatch.setattr(settings, "EPING_IMAP_PORT", 993)
    monkeypatch.setattr(settings, "EPING_SENDER_DOMAIN", "epingalert.org")


def _html_message(subject: str, html_body: str) -> bytes:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "ePing Alerts <alerts@epingalert.org>"
    msg.attach(MIMEText("Plain-text fallback not used in this fixture.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg.as_bytes()


def _text_message(subject: str, text_body: str) -> bytes:
    msg = MIMEText(text_body, "plain")
    msg["Subject"] = subject
    msg["From"] = "ePing Alerts <alerts@epingalert.org>"
    return msg.as_bytes()


class _FakeIMAP:
    """Stands in for imaplib.IMAP4_SSL. `raw_messages` is a list of message
    bytes returned in order for successive UNSEEN message ids."""

    instances: list["_FakeIMAP"] = []

    def __init__(self, host, port, raw_messages=None, login_password="app-password",
                 fail_connect=False, fail_login=False, fail_search=False):
        if fail_connect:
            raise OSError("connection refused")
        self.host = host
        self.port = port
        self.raw_messages = raw_messages or []
        self._login_password = login_password
        self._fail_login = fail_login
        self._fail_search = fail_search
        self.logged_out = False
        _FakeIMAP.instances.append(self)

    def login(self, user, password):
        if self._fail_login or password != self._login_password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return ("OK", [b"Logged in"])

    def select(self, mailbox):
        return ("OK", [str(len(self.raw_messages)).encode()])

    def search(self, charset, *criteria):
        if self._fail_search:
            raise imaplib.IMAP4.error("search failed")
        if not self.raw_messages:
            return ("OK", [b""])
        ids = b" ".join(str(i + 1).encode() for i in range(len(self.raw_messages)))
        return ("OK", [ids])

    def fetch(self, msg_id, parts):
        idx = int(msg_id) - 1
        raw = self.raw_messages[idx]
        return ("OK", [(b"1 (RFC822 {%d})" % len(raw), raw)])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"Logging out"])


def _patch_imap(monkeypatch, **kwargs):
    _FakeIMAP.instances = []
    monkeypatch.setattr(eping.imaplib, "IMAP4_SSL", lambda host, port: _FakeIMAP(host, port, **kwargs))


# --- fixtures built from the documented ePing schema -----------------------

HTML_DIGEST = """
<html><body>
<h2>ePing Daily Alert</h2>
<table>
<tr><td>
<p>Product: Processed meat products (HS 1601). Notifying Member: United States.
Committee: TBT. Distributed: 2026-09-14.
<a href="https://eping.wto.org/en/Notification/Details/12345">G/TBT/N/USA/1234</a>
</p>
</td></tr>
<tr><td>
<p>Product: Fresh mangoes. Notifying Member: European Union. Committee: SPS.
Distributed: 12 September 2026.
<a href="https://eping.wto.org/en/Notification/Details/67890">G/SPS/N/EU/567</a>
</p>
</td></tr>
</table>
</body></html>
"""

TEXT_DIGEST = """ePing Daily Alert

Product: Organic cotton yarn. Notifying Member: Japan. Committee: TBT.
Distributed: 2026-09-13.
Notification: G/TBT/N/JPN/999
Link: https://eping.wto.org/en/Notification/Details/999
"""


def test_html_digest_parses_into_two_raw_items(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, raw_messages=[_html_message("ePing Daily Alert", HTML_DIGEST)])

    items = eping.EpingCollector().collect(SOURCE)

    assert len(items) == 2
    first, second = items
    assert "G/TBT/N/USA/1234" in first.title
    assert "meat" in first.title.lower()
    assert first.url == "https://eping.wto.org/en/Notification/Details/12345"
    assert first.published_at == "2026-09-14"
    assert first.publisher == "epingalert.org"
    assert second.url == "https://eping.wto.org/en/Notification/Details/67890"
    assert second.published_at == "2026-09-12"


def test_plain_text_digest_parses(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, raw_messages=[_text_message("ePing Daily Alert", TEXT_DIGEST)])

    items = eping.EpingCollector().collect(SOURCE)

    assert len(items) == 1
    assert items[0].url == "https://eping.wto.org/en/Notification/Details/999"
    assert "G/TBT/N/JPN/999" in items[0].title
    assert items[0].published_at == "2026-09-13"


def test_no_unseen_messages_returns_empty_list_not_an_error(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, raw_messages=[])

    items = eping.EpingCollector().collect(SOURCE)

    assert items == []


def test_missing_credentials_raises_before_connecting(monkeypatch):
    monkeypatch.setattr(settings, "EPING_IMAP_USER", None)
    monkeypatch.setattr(settings, "EPING_IMAP_PASSWORD", None)

    def _boom(*a, **k):
        raise AssertionError("should not attempt an IMAP connection without credentials")

    monkeypatch.setattr(eping.imaplib, "IMAP4_SSL", _boom)

    with pytest.raises(CollectorError):
        eping.EpingCollector().collect(SOURCE)


def test_connection_failure_raises_collector_error(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, fail_connect=True)

    with pytest.raises(CollectorError):
        eping.EpingCollector().collect(SOURCE)


def test_login_failure_raises_collector_error(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, fail_login=True)

    with pytest.raises(CollectorError):
        eping.EpingCollector().collect(SOURCE)


def test_search_failure_raises_collector_error(monkeypatch):
    _configure_credentials(monkeypatch)
    _patch_imap(monkeypatch, fail_search=True)

    with pytest.raises(CollectorError):
        eping.EpingCollector().collect(SOURCE)


def test_malformed_digest_raises_collector_error(monkeypatch):
    _configure_credentials(monkeypatch)
    garbled = _html_message("ePing Daily Alert", "<html><body><p>Nothing recognisable here.</p></body></html>")
    _patch_imap(monkeypatch, raw_messages=[garbled])

    with pytest.raises(CollectorError, match="ePing digest"):
        eping.EpingCollector().collect(SOURCE)


def test_notification_block_without_any_link_is_skipped(monkeypatch):
    _configure_credentials(monkeypatch)
    html_body = (
        "<html><body><p>Product: Widgets. Notifying Member: Canada. Committee: TBT. "
        "G/TBT/N/CAN/1 has no link at all.</p>"
        '<p>Product: Bolts. Notifying Member: Canada. Committee: TBT. '
        '<a href="https://eping.wto.org/en/Notification/Details/2">G/TBT/N/CAN/2</a></p></body></html>'
    )
    _patch_imap(monkeypatch, raw_messages=[_html_message("ePing Daily Alert", html_body)])

    items = eping.EpingCollector().collect(SOURCE)

    assert len(items) == 1
    assert items[0].url == "https://eping.wto.org/en/Notification/Details/2"


def test_logout_is_called_even_on_parse_error(monkeypatch):
    _configure_credentials(monkeypatch)
    garbled = _html_message("ePing Daily Alert", "<html><body><p>Nothing recognisable here.</p></body></html>")
    _patch_imap(monkeypatch, raw_messages=[garbled])

    with pytest.raises(CollectorError):
        eping.EpingCollector().collect(SOURCE)

    assert _FakeIMAP.instances[0].logged_out is True
