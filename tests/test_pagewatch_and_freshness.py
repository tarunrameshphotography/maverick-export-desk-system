from datetime import date

import pytest

from radar.collectors import pagewatch
from radar.collectors.base import CollectorError, RawItem
from radar.collectors.registry import is_stale
from radar.pipeline.entities import extract_entities

# Shape copied from the live DGFT notification listing (14 Sep 2026).
DGFT_TABLE = """
<html><body><nav><a href="/CP/">GOVERNMENT OF INDIA</a></nav>
<table>
<tr><th>S.No</th><th>Notification No</th><th>Year</th><th>Description</th><th>Date</th><th>Attachment</th></tr>
<tr><td>1</td><td>34/2026-27</td><td>2026-27</td>
    <td>Amendment in the Export Policy of Wheat Flour and related products - reg.</td><td>24/08/2026</td>
    <td><a href="https://content.dgft.gov.in/x/Scan wheat flour english.pdf">Download (Type : PDF)</a>
        <a href="https://content.dgft.gov.in/x/Scan wheat flour hindi.pdf">Download (Type : PDF)</a></td></tr>
<tr><td>37</td><td>74/2025-26</td><td>2025-26</td>
    <td>Continuation of RoDTEP Scheme beyond March 31, 2026</td><td>31/03/2026</td>
    <td><a href="https://content.dgft.gov.in/x/Notification No 74.pdf">Download</a></td></tr>
</table></body></html>
"""
SOURCE = {"id": "dgft_notifications", "url": "https://www.dgft.gov.in/CP/?opt=notification",
          "parse": "table_rows", "doc_label": "Notification", "authority": "DGFT"}


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _collect(monkeypatch, html, source=SOURCE):
    monkeypatch.setattr(pagewatch.requests, "get", lambda *a, **k: _Resp(html))
    return pagewatch.PagewatchCollector().collect(source)


def test_table_rows_become_one_item_per_instrument(monkeypatch):
    items = _collect(monkeypatch, DGFT_TABLE)
    assert len(items) == 2  # header row and nav chrome are ignored
    wheat = items[0]
    assert wheat.title == "DGFT Notification 34/2026-27: Amendment in the Export Policy of Wheat Flour and related products - reg."
    assert wheat.published_at == "2026-08-24"  # dd/mm/yyyy read day-first, stored ISO
    assert wheat.url.endswith("english.pdf")  # English PDF preferred over Hindi


def test_table_row_body_makes_doc_number_and_date_extractable(monkeypatch):
    rodtep = _collect(monkeypatch, DGFT_TABLE)[1]
    values = {(e["entity_type"], e["normalized_value"]) for e in extract_entities(rodtep.title + " " + rodtep.body_text)}
    assert ("regulation", "Notification 74/2025-26") in values
    assert ("scheme", "RoDTEP") in values
    assert ("announcement_date", "2026-03-31") in values


def test_layout_change_is_a_failed_source_not_silence(monkeypatch):
    with pytest.raises(CollectorError, match="layout"):
        _collect(monkeypatch, "<html><body><p>Site under maintenance</p></body></html>")


def test_anchor_mode_still_filters_short_chrome(monkeypatch):
    html = '<a href="/a">Home</a><a href="/b">Amendment in Para 2.93 of the Handbook of Procedures</a>'
    items = _collect(monkeypatch, html, {"id": "x", "url": "https://example.gov.in/", "parse": "anchors"})
    assert [i.title for i in items] == ["Amendment in Para 2.93 of the Handbook of Procedures"]


def test_is_stale_respects_lookback_and_keeps_undated_items():
    today = date(2026, 9, 14)
    assert is_stale(RawItem("s", "t", "u", published_at="2026-08-01"), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at="2026-09-01"), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at=None), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at="Mon, 14 Sep 2026 06:00:00 GMT"), 21, today)
