import pytest

from radar.collectors import cbic
from radar.collectors.base import CollectorError

SOURCE = {"id": "cbic_customs_notifications", "tax": "customs"}

# Shape copied from the live taxinformation.cbic.gov.in
# fetchUpdatesByTaxId/1000002 response (14 Sep 2026).
PAYLOAD = [
    {
        "id": 1003341,
        "updatedDate": "03-Sep-2026",
        "notificationNo": "39/2026",
        "notificationName": "Amendment to Circular No. 08/2026-Customs dated 28.02.2026 - Rationalization of "
                             "documentation requirements under the Eligible Manufacturer Importer (EMI) Scheme",
        "updateType": "Circular",
    },
    {
        "id": 1003343,
        "updatedDate": "03-Sep-2026",
        "notificationNo": "41/2026",
        "notificationName": "National Assessment Centre (NAC) Portal for Trade and department for effective "
                             "dissemination of information",
        "updateType": "Circular",
    },
    {
        "id": 1003999,
        "updatedDate": "03-Sep-2026",
        "notificationNo": "",
        "notificationName": "",
        "updateType": "Circular",
    },
]


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def _collect(monkeypatch, payload, source=SOURCE):
    monkeypatch.setattr(cbic.requests, "get", lambda *a, **k: _Resp(payload))
    return cbic.CbicCollector().collect(source)


def test_listing_becomes_one_item_per_entry(monkeypatch):
    items = _collect(monkeypatch, PAYLOAD)
    assert len(items) == 2  # the entry with no name is skipped
    first = items[0]
    assert first.title == (
        "CBIC Circular 39/2026: Amendment to Circular No. 08/2026-Customs dated 28.02.2026 - "
        "Rationalization of documentation requirements under the Eligible Manufacturer Importer (EMI) Scheme"
    )
    assert first.published_at == "2026-09-03"
    assert first.url == cbic.PORTAL_URL


def test_body_text_carries_the_exact_reference_for_manual_lookup(monkeypatch):
    items = _collect(monkeypatch, PAYLOAD)
    assert "Circular No. 39/2026" in items[0].body_text
    assert "03-Sep-2026" in items[0].body_text
    assert "no direct document link" in items[0].body_text


def test_entry_without_a_name_is_skipped(monkeypatch):
    items = _collect(monkeypatch, PAYLOAD)
    assert all(item.title for item in items)
    assert len(items) == 2


def test_unparseable_date_is_kept_undated(monkeypatch):
    payload = [{"notificationNo": "1/2026", "notificationName": "Test notice", "updateType": "Notification",
                "updatedDate": "not-a-date"}]
    items = _collect(monkeypatch, payload)
    assert items[0].published_at is None


def test_request_failure_raises_collector_error(monkeypatch):
    def _boom(*a, **k):
        raise __import__("requests").RequestException("timeout")

    monkeypatch.setattr(cbic.requests, "get", _boom)
    with pytest.raises(CollectorError):
        cbic.CbicCollector().collect(SOURCE)


def test_invalid_json_raises_collector_error(monkeypatch):
    class _BadJson(_Resp):
        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr(cbic.requests, "get", lambda *a, **k: _BadJson(None))
    with pytest.raises(CollectorError):
        cbic.CbicCollector().collect(SOURCE)
