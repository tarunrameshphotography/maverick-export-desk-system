import pytest

from radar.pipeline.verify import (
    build_claims_table,
    extract_candidate_claims,
    mark_claim,
    render_verification_worksheet,
    verification_status_summary,
)


def test_extract_candidate_claims_finds_numeric_and_date_sentences():
    text = (
        "This is background chatter. "
        "RoDTEP rates are extended to 30 September 2026. "
        "The final CVD rate is 25.29%. "
        "The scheme is popular among exporters."
    )
    claims = extract_candidate_claims(text)
    assert any("30 September 2026" in c for c in claims)
    assert any("25.29%" in c for c in claims)
    assert not any("popular among exporters" in c for c in claims)


def test_extract_candidate_claims_deduplicates():
    text = "Rates extended to 30 September 2026. Rates extended to 30 September 2026."
    assert len(extract_candidate_claims(text)) == 1


def _seed_signal_with_raw_item(conn, signal_id="SIG-V-0001"):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, created_at, updated_at, primary_source_url)
        VALUES (?, 'RoDTEP extension', '2026-08-15', 'incentives', 'TRIAGED', '2026-08-15', '2026-08-15', 'https://dgft.gov.in/x')
        """,
        (signal_id,),
    )
    conn.execute(
        """
        INSERT INTO raw_items (source_id, url, canonical_url, title, body_text, published_at,
                                collected_at, content_hash, signal_id, status)
        VALUES ('dgft_notifications', 'https://dgft.gov.in/x', 'https://dgft.gov.in/x',
                'RoDTEP extension', 'Rates extended to 30 September 2026 per Notification 74/2025-26.',
                '2026-08-15', '2026-08-15', 'hash1', ?, 'CLUSTERED')
        """,
        (signal_id,),
    )
    conn.commit()
    return signal_id


def test_build_claims_table_inserts_pending_claims(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    inserted = build_claims_table(conn, signal_id)
    assert inserted > 0
    rows = conn.execute("SELECT * FROM claims WHERE signal_id = ?", (signal_id,)).fetchall()
    assert all(r["status"] == "pending" for r in rows)
    assert all(r["claim_type"] == "verify" for r in rows)
    assert all(r["source_type"] == "primary" for r in rows)


def test_build_claims_table_is_idempotent(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    second_pass = build_claims_table(conn, signal_id)
    assert second_pass == 0


def test_mark_claim_verified_requires_quote(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    claim_id = conn.execute("SELECT id FROM claims WHERE signal_id = ?", (signal_id,)).fetchone()["id"]

    with pytest.raises(ValueError):
        mark_claim(conn, claim_id, "verified", "2026-09-14T08:00:00")


def test_mark_claim_verified_with_quote_succeeds(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    claim_id = conn.execute("SELECT id FROM claims WHERE signal_id = ?", (signal_id,)).fetchone()["id"]

    mark_claim(
        conn, claim_id, "verified", "2026-09-14T08:00:00",
        claim_type="fact", source_quote="Rates extended to 30 September 2026", verified_by="analyst",
    )
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    assert row["status"] == "verified"
    assert row["claim_type"] == "fact"
    assert row["source_quote"] == "Rates extended to 30 September 2026"
    assert row["verified_at"] == "2026-09-14T08:00:00"


def test_mark_claim_rejects_invalid_status(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    claim_id = conn.execute("SELECT id FROM claims WHERE signal_id = ?", (signal_id,)).fetchone()["id"]
    with pytest.raises(ValueError):
        mark_claim(conn, claim_id, "not_a_real_status", "2026-09-14T08:00:00")


def test_verification_status_summary_reports_pending_and_verified(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    claim_id = conn.execute("SELECT id FROM claims WHERE signal_id = ?", (signal_id,)).fetchone()["id"]
    mark_claim(conn, claim_id, "verified", "2026-09-14T08:00:00", claim_type="fact", source_quote="quote")

    summary = verification_status_summary(conn, signal_id)
    assert summary["total"] >= 1
    assert summary["verified"] == 1


def test_render_verification_worksheet_includes_claims(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    signal_id = _seed_signal_with_raw_item(conn)
    build_claims_table(conn, signal_id)
    worksheet = render_verification_worksheet(conn, signal_id)
    assert signal_id in worksheet
    assert "Claim #" in worksheet
