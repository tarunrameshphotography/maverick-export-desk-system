import pytest

from radar import settings
from radar.pipeline import evidence
from radar.pipeline.verify import add_claim, build_claims_table, mark_claim

NOW = "2026-09-14T08:00:00"
FR_URL = ("https://www.federalregister.gov/documents/2026/09/14/2026-18707/"
          "certain-linear-hydraulic-cylinders-and-parts-thereof-from-the-peoples-republic-of-china-india-and")
FR_TEXT = ("The products subject to these investigations are currently classified in the HTSUS under "
           "statistical reporting numbers 8412.21.0015, 8412.21.0030, 8412.21.0045. "
           "On July 29, 2026, the U.S. Department of Commerce (Commerce) received countervailing duty (CVD) "
           "petitions concerning imports of certain linear hydraulic cylinders.")


@pytest.fixture()
def seeded(conn, tmp_path, monkeypatch):
    from radar.db.connection import seed_sources

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    seed_sources(conn)
    conn.execute(
        "INSERT INTO signals (id, title, first_seen_at, status, primary_source_url, created_at, updated_at) "
        "VALUES ('SIG-E', 'Hydraulic cylinders CVD initiation', ?, 'SCORED', ?, ?, ?)",
        (NOW, evidence.canonicalize_url(FR_URL), NOW, NOW),
    )
    conn.execute(
        "INSERT INTO raw_items (source_id, url, canonical_url, title, body_text, published_at, collected_at, "
        "content_hash, signal_id, status) VALUES ('federal_register_india', ?, ?, 'Hydraulic cylinders', "
        "'Commerce received petitions on July 29, 2026.', '2026-09-14', ?, 'h', 'SIG-E', 'CLUSTERED')",
        (FR_URL, evidence.canonicalize_url(FR_URL), NOW),
    )
    conn.commit()
    return conn


def test_federal_register_url_fetches_official_full_text(seeded, monkeypatch):
    calls = []

    class Resp:
        def __init__(self, payload=None, text=""):
            self._payload, self.text, self.headers, self.url = payload, text, {}, "x"

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("2026-18707.json"):
            return Resp({"raw_text_url": "https://www.federalregister.gov/documents/full_text/text/2026-18707.txt"})
        return Resp(text=FR_TEXT)

    monkeypatch.setattr(evidence.requests, "get", fake_get)
    docs = evidence.fetch_signal_evidence(seeded, "SIG-E", NOW)
    assert [d["status"] for d in docs] == ["stored"]
    assert calls[0].endswith("/api/v1/documents/2026-18707.json")
    assert "8412.21.0015" in evidence.stored_text(seeded, "SIG-E", FR_URL)


def _store(seeded, monkeypatch):
    monkeypatch.setattr(evidence, "fetch_text", lambda url: (FR_TEXT, url))
    evidence.fetch_signal_evidence(seeded, "SIG-E", NOW)


def test_verbatim_quote_is_machine_checked(seeded, monkeypatch):
    _store(seeded, monkeypatch)
    cid = add_claim(seeded, "SIG-E", "Petitions were filed on 29 July 2026", "fact", FR_URL, "primary", NOW,
                    source_quote="On July 29, 2026, the U.S. Department of Commerce (Commerce) received countervailing duty (CVD) petitions")
    notes = seeded.execute("SELECT notes FROM claims WHERE id = ?", (cid,)).fetchone()["notes"]
    assert "machine-checked" in notes and "NOT" not in notes


def test_paraphrased_quote_is_refused(seeded, monkeypatch):
    _store(seeded, monkeypatch)
    with pytest.raises(ValueError, match="not found"):
        add_claim(seeded, "SIG-E", "x", "fact", FR_URL, "primary", NOW,
                  source_quote="Commerce got petitions at the end of July")


def test_mark_claim_also_checks_the_quote(seeded, monkeypatch):
    _store(seeded, monkeypatch)
    build_claims_table(seeded, "SIG-E")
    claim_id = seeded.execute("SELECT id FROM claims WHERE signal_id = 'SIG-E'").fetchone()["id"]
    with pytest.raises(ValueError, match="not found"):
        mark_claim(seeded, claim_id, "verified", NOW, claim_type="fact", source_quote="an invented passage")
    mark_claim(seeded, claim_id, "verified", NOW, claim_type="fact", source_quote="8412.21.0015, 8412.21.0030")


def test_unfetchable_source_is_labelled_not_silently_trusted(seeded):
    cid = add_claim(seeded, "SIG-E", "x", "fact", "https://content.dgft.gov.in/x/Notification 34.pdf", "primary", NOW,
                    source_quote="Export of wheat flour is Prohibited")
    assert "NOT machine-checked" in seeded.execute("SELECT notes FROM claims WHERE id = ?", (cid,)).fetchone()["notes"]


def test_pdf_and_aggregator_links_are_recorded_as_not_machine_readable(seeded):
    pdf = evidence.store_evidence(seeded, "SIG-E", "https://content.dgft.gov.in/x/Notif 34.pdf", NOW)
    gnews = evidence.store_evidence(seeded, "SIG-E", "https://news.google.com/rss/articles/abc", NOW)
    assert pdf["status"] == gnews["status"] == "not_machine_readable"
    assert "PDF" in pdf["note"] and "aggregator" in gnews["note"]
