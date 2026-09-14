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


def test_anchor_mode_with_link_filter_keeps_only_matching_hrefs(monkeypatch):
    # DGTR's case-listing page is mostly site navigation chrome; only links
    # into /anti-dumping-cases/... are actual investigation records.
    html = (
        '<a href="/en/about-department">About the Department is a long enough link text</a>'
        '<a href="/en/anti-dumping-cases/widgets-from-china">Anti-dumping investigation on Widgets from China</a>'
    )
    items = _collect(
        monkeypatch, html,
        {"id": "dgtr_cases", "url": "https://www.dgtr.gov.in/en/x", "parse": "anchors",
         "link_must_contain": "/anti-dumping-cases/"},
    )
    assert len(items) == 1
    assert items[0].title == "Anti-dumping investigation on Widgets from China"
    assert items[0].url == "https://www.dgtr.gov.in/en/anti-dumping-cases/widgets-from-china"


def test_anchor_mode_with_link_filter_and_zero_matches_is_a_failed_source(monkeypatch):
    html = '<a href="/en/about-department">About the Department is a long enough link text</a>'
    with pytest.raises(CollectorError, match="layout"):
        _collect(
            monkeypatch, html,
            {"id": "dgtr_cases", "url": "https://www.dgtr.gov.in/en/x", "parse": "anchors",
             "link_must_contain": "/anti-dumping-cases/"},
        )


def test_anchor_mode_carries_authority_into_body_text_for_entity_extraction(monkeypatch):
    # DGTR's own case titles rarely say "India" or "DGTR" outright (e.g. "Anti-dumping
    # investigation on imports of Medical Examination Rubber Gloves from Malaysia and
    # Thailand") -- without an authority tag, entity extraction has nothing to key off
    # and G2 (Indian implication) wrongly fails every DGTR case.
    html = ('<a href="/en/anti-dumping-cases/gloves">Anti-dumping investigation on imports of '
            'Medical Examination Rubber Gloves from Malaysia and Thailand</a>')
    items = _collect(
        monkeypatch, html,
        {"id": "dgtr_cases", "url": "https://www.dgtr.gov.in/en/x", "parse": "anchors",
         "link_must_contain": "/anti-dumping-cases/", "authority": "DGTR"},
    )
    assert len(items) == 1
    assert "DGTR" in items[0].body_text
    values = {(e["entity_type"], e["normalized_value"]) for e in extract_entities(items[0].title + " " + items[0].body_text)}
    assert ("authority", "DGTR") in values


def test_anchor_mode_without_authority_config_leaves_body_text_empty(monkeypatch):
    html = '<a href="/b">Amendment in Para 2.93 of the Handbook of Procedures</a>'
    items = _collect(monkeypatch, html, {"id": "x", "url": "https://example.gov.in/", "parse": "anchors"})
    assert items[0].body_text == ""


def test_is_stale_respects_lookback_and_keeps_undated_items():
    today = date(2026, 9, 14)
    assert is_stale(RawItem("s", "t", "u", published_at="2026-08-01"), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at="2026-09-01"), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at=None), 21, today)
    assert not is_stale(RawItem("s", "t", "u", published_at="Mon, 14 Sep 2026 06:00:00 GMT"), 21, today)
