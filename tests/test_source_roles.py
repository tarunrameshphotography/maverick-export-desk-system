import pytest

from radar import settings
from radar.collectors import registry
from radar.collectors.base import RawItem
from radar.db.connection import seed_sources
from radar.pipeline import source_roles, verify
from radar.pipeline.dedupe import dedupe_and_cluster_new_items
from radar.pipeline.orchestrator import _signal_text_and_sources

NOW = "2026-09-15T06:00:00+05:30"


@pytest.fixture(autouse=True)
def _seeded(conn):
    seed_sources(conn)


REUTERS = "https://www.reuters.com/world/india/india-extends-rodtep-2026-09-14/"


def _signal_with_items(conn, items):
    conn.execute(
        "INSERT INTO signals (id, title, first_seen_at, status, created_at, updated_at) "
        "VALUES ('S', 't', 'x', 'TRIAGED', 'x', 'x')"
    )
    for n, (source_id, title, url, publisher, body) in enumerate(items):
        conn.execute(
            "INSERT INTO raw_items (source_id, url, canonical_url, title, body_text, publisher, collected_at, "
            "content_hash, signal_id, status) VALUES (?, ?, ?, ?, ?, ?, 'x', ?, 'S', 'CLUSTERED')",
            (source_id, url, url, title, body, publisher, f"h{n}"),
        )
    conn.commit()


def _row(source_id, title, publisher, body=""):
    return {"source_id": source_id, "kind": "secondary", "title": title, "publisher": publisher,
            "body_text": body, "url": f"https://{publisher}/x", "canonical_url": None}


def _seed_sample(conn):
    run_id = conn.execute(
        "INSERT INTO system_runs (run_type, started_at) VALUES ('manual', '2026-09-14T05:30:00')"
    ).lastrowid
    registry.run_collection(conn, run_id, use_sample=True)
    dedupe_and_cluster_new_items(conn, NOW)


# --- configuration ---

def test_every_source_kind_matches_its_tier_and_every_tier_rates_four_roles():
    tiers = settings.source_tiers()
    for tier in tiers.values():
        assert {"discovery", "context", "saturation"} <= set(tier["roles"])
        assert tier["evidence"] in ("primary", "secondary", "lead_only")
    for source in settings.sources():
        profile = source_roles.source_profile(source)
        assert profile["kind"] == source["kind"], source["id"]


def test_news_is_discovery_confirmation_context_and_saturation_not_noise():
    reuters = source_roles.profile_for("google_news_reuters_india_trade", "secondary")
    assert reuters["discovery"] == "high" and reuters["context"] == "high" and reuters["saturation"] == "high"
    assert reuters["evidence"] == "secondary" and reuters["corroborates"] is True
    dgft = source_roles.profile_for("dgft_notifications", "primary")
    assert dgft["evidence"] == "primary" and dgft["context"] == "low"


def test_low_quality_reposts_and_social_posts_stay_low_trust():
    repost = source_roles.profile_for("unverified_reposts", "secondary")
    social = source_roles.profile_for("expert_social_posts", "secondary")
    assert repost["evidence"] == social["evidence"] == "lead_only"
    assert repost["corroborates"] is social["corroborates"] is False
    assert repost["discovery"] == "low" and social["discovery"] == "high"
    reposts = [_row("unverified_reposts", f"RoDTEP scrapped, forward this {n}", f"acct{n}.example") for n in range(4)]
    assert source_roles.corroborating_origin_count(reposts) == 0


def test_inactive_and_manual_sources_are_still_registered_so_gaps_stay_visible(conn):
    row = conn.execute("SELECT active, method, notes FROM sources WHERE id = 'eu_official_journal'").fetchone()
    assert row["active"] == 0 and row["method"] == "manual" and "No feed verified" in row["notes"]


# --- discovery: secondary news and social leads enter the Radar ---

def test_secondary_news_alone_enters_the_radar_as_a_reported_signal(conn):
    registry.insert_raw_item(conn, RawItem("google_news_reuters_india_trade", "India weighs curbs on rice exports", REUTERS,
                                           published_at="2026-09-14T10:00:00+05:30", publisher="reuters.com"))
    [signal_id] = dedupe_and_cluster_new_items(conn, NOW)
    assert conn.execute("SELECT lifecycle_stage FROM signals WHERE id = ?", (signal_id,)).fetchone()[0] == "reported"


def test_manual_social_lead_enters_the_same_pipeline_as_a_lead_only(conn):
    raw_id = registry.add_manual_lead(conn, "expert_social_posts", "Hearing EU will tighten basmati MRLs from January",
                                      "https://www.linkedin.com/posts/example-123", publisher="linkedin.com")
    assert conn.execute("SELECT status FROM raw_items WHERE id = ?", (raw_id,)).fetchone()[0] == "NEW"
    [signal_id] = dedupe_and_cluster_new_items(conn, NOW)
    prov = source_roles.signal_provenance(conn, signal_id)
    assert [r["source_id"] for r in prov["leads"]] == ["expert_social_posts"]
    assert prov["primary"] == [] and prov["corroboration"] == []


def test_manual_intake_refuses_automated_sources_and_ignores_duplicates(conn):
    with pytest.raises(ValueError, match="collected automatically"):
        registry.add_manual_lead(conn, "dgft_notifications", "x", "https://dgft.gov.in/x")
    with pytest.raises(ValueError, match="Unknown source"):
        registry.add_manual_lead(conn, "no_such_source", "x", "https://x")
    args = ("field_notes", "CHA reports Nhava Sheva holds on RoSCTL scrips", "field-note://2026-09-15-01")
    assert registry.add_manual_lead(conn, *args) is not None
    assert registry.add_manual_lead(conn, *args) is None


# --- evidence: discovery never becomes verification ---

def test_secondary_source_cannot_verify_a_fact_claim(conn):
    _signal_with_items(conn, [("google_news_reuters_india_trade", "RoDTEP extended to March 2027", REUTERS, "reuters.com", "")])
    claim_id = verify.add_claim(conn, "S", "RoDTEP extended to 31 March 2027", "fact", REUTERS, "secondary", NOW)
    with pytest.raises(ValueError, match="only be verified against a primary source"):
        verify.mark_claim(conn, claim_id, "verified", NOW, source_quote="extended to March 2027")
    with pytest.raises(ValueError, match="only be verified against a primary source"):
        verify.add_claim(conn, "S", "x", "fact", REUTERS, "secondary", NOW, source_quote="extended")


def test_collected_secondary_url_cannot_be_relabelled_primary(conn):
    _signal_with_items(conn, [("google_news_reuters_india_trade", "RoDTEP extended", REUTERS, "reuters.com", "")])
    with pytest.raises(ValueError, match="can't be recorded as primary evidence"):
        verify.add_claim(conn, "S", "RoDTEP extended", "fact", REUTERS, "primary", NOW, source_quote="extended")


def test_secondary_source_can_back_an_attributed_interpretation(conn):
    _signal_with_items(conn, [("google_news_reuters_india_trade", "RoDTEP extended", REUTERS, "reuters.com", "")])
    claim_id = verify.add_claim(conn, "S", "Exporters see six months of certainty", "verify", REUTERS, "secondary", NOW)
    verify.mark_claim(conn, claim_id, "verified", NOW, claim_type="interpretation", source_quote="certainty")
    assert conn.execute("SELECT status FROM claims WHERE id = ?", (claim_id,)).fetchone()[0] == "verified"


def test_social_lead_verifies_nothing_not_even_an_opinion(conn):
    url = "https://x.com/tradejournalist/status/1"
    _signal_with_items(conn, [("expert_social_posts", "US to raise shrimp duty next week", url, "x.com", "")])
    claim_id = verify.add_claim(conn, "S", "US will raise shrimp duty", "opinion", url, "secondary", NOW)
    with pytest.raises(ValueError, match="Lead-only"):
        verify.mark_claim(conn, claim_id, "verified", NOW, source_quote="raise shrimp duty")


def test_primary_source_still_verifies_fact_claims(conn):
    url = "https://www.dgft.gov.in/CP/notification-74"
    _signal_with_items(conn, [("dgft_notifications", "Notification 74/2025-26", url, None, "")])
    claim_id = verify.add_claim(conn, "S", "RoDTEP rates extended to 30 September 2026", "fact", url, "primary", NOW)
    verify.mark_claim(conn, claim_id, "verified", NOW, source_quote="extended to 30 September 2026")
    assert conn.execute("SELECT status FROM claims WHERE id = ?", (claim_id,)).fetchone()[0] == "verified"


# --- corroboration: copies are not independent sources ---

def test_syndicated_copies_of_one_wire_story_are_one_origin():
    wire_copies = [_row(src, f"Govt extends RoDTEP to March 2027 {tail}", pub, "NEW DELHI (PTI) The government on Monday...")
                   for src, pub, tail in [("google_news_india_tariffs", "economictimes.indiatimes.com", ""),
                                          ("google_news_rodtep_roscl", "thehindu.com", "- report"),
                                          ("business_standard_economy", "business-standard.com", "")]]
    assert source_roles.corroborating_origin_count(wire_copies) == 1


def test_reuters_and_its_reprints_are_one_origin_but_an_independent_report_adds_one():
    rows = [_row("google_news_reuters_india_trade", "India extends RoDTEP export incentives", "reuters.com")]
    rows += [_row("google_news_india_tariffs", "India extends RoDTEP export incentives", pub)
             for pub in ("theprint.in", "devdiscourse.com", "zeebiz.com", "marketscreener.com")]
    assert source_roles.corroborating_origin_count(rows) == 1
    rows.append(_row("business_standard_economy", "Exporters get six more months of duty remission", "business-standard.com"))
    assert source_roles.corroborating_origin_count(rows) == 2


def test_duplicate_copies_do_not_satisfy_the_two_source_rule(conn):
    _signal_with_items(conn, [
        ("google_news_reuters_india_trade", "India to cap onion exports", REUTERS, "reuters.com", ""),
        ("google_news_india_tariffs", "India to cap onion exports", "https://theprint.in/a", "theprint.in", ""),
        ("google_news_india_tariffs", "India to cap onion exports", "https://zeebiz.com/a", "zeebiz.com", "(Reuters)"),
        ("expert_social_posts", "Onion export cap coming, sources say", "https://x.com/a/1", "x.com", ""),
    ])
    _, has_primary, corroborations = _signal_text_and_sources(conn, "S")
    assert has_primary is False and corroborations == 1  # G1 needs 2


# --- clustering and provenance across source types ---

def test_primary_and_secondary_reports_of_one_event_cluster_with_provenance_preserved(conn):
    _seed_sample(conn)
    signal_ids = {r[0] for r in conn.execute(
        "SELECT signal_id FROM raw_items WHERE source_id IN ('dgft_notifications', 'google_news_rodtep_roscl')"
    )}
    assert len(signal_ids) == 1
    prov = source_roles.signal_provenance(conn, signal_ids.pop())
    assert prov["first_seen"]["source_id"] == "dgft_notifications"
    assert {r["source_id"] for r in prov["primary"]} == {"dgft_notifications", "pib_english"}
    assert len(prov["corroboration"]) == 2
    assert prov["saturation"]["items"] == 2
    text = source_roles.format_provenance(prov)
    for heading in ("PRIMARY", "SECONDARY (independent corroboration: 2", "DISCOVERY LEADS", "CONTEXT", "SATURATION"):
        assert heading in text


# --- the same event observed across runs ---

def _sample(source_id):
    from radar.collectors.sample_data import load_sample_raw_items
    return next(i for i in load_sample_raw_items() if i.source_id == source_id)


def _run(conn, *source_ids):
    for sid in source_ids:
        registry.insert_raw_item(conn, _sample(sid))
    return dedupe_and_cluster_new_items(conn, NOW)


def test_news_arriving_a_day_after_the_notification_joins_the_same_signal(conn):
    [signal_id] = _run(conn, "dgft_notifications")
    conn.execute("UPDATE signals SET status = 'SCORED' WHERE id = ?", (signal_id,))
    assert _run(conn, "google_news_rodtep_roscl") == []  # no second story
    assert conn.execute("SELECT COUNT(*) FROM raw_items WHERE signal_id = ?", (signal_id,)).fetchone()[0] == 2
    assert conn.execute("SELECT status FROM signals WHERE id = ?", (signal_id,)).fetchone()[0] == "TRIAGED"  # rescored


def test_primary_document_found_after_the_news_becomes_the_signals_primary(conn):
    [signal_id] = _run(conn, "google_news_rodtep_roscl")
    assert conn.execute("SELECT lifecycle_stage FROM signals WHERE id = ?", (signal_id,)).fetchone()[0] == "reported"
    assert _run(conn, "dgft_notifications") == []
    row = conn.execute("SELECT lifecycle_stage, title FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["lifecycle_stage"] == "notified" and row["title"].startswith("DGFT Notification 74/2025-26")
    verify.build_claims_table(conn, signal_id)
    assert conn.execute(
        "SELECT COUNT(*) FROM claims WHERE signal_id = ? AND source_type = 'primary'", (signal_id,)
    ).fetchone()[0] > 0


def test_late_coverage_does_not_move_a_signal_a_human_already_advanced(conn):
    [signal_id] = _run(conn, "dgft_notifications")
    conn.execute("UPDATE signals SET status = 'READY_FOR_REVIEW' WHERE id = ?", (signal_id,))
    _run(conn, "google_news_rodtep_roscl")
    assert conn.execute("SELECT status FROM signals WHERE id = ?", (signal_id,)).fetchone()[0] == "READY_FOR_REVIEW"
