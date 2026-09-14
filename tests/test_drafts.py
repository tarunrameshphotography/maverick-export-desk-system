import pytest

from radar import settings
from radar.content.drafts import (
    ASSET_SLOTS,
    create_content_bundle,
    create_draft_scaffold,
    disclosure_required,
    lint_draft,
    record_publication,
    render_bundle_brief,
    render_draft_brief,
    risk_tier,
    save_draft_version,
    set_draft_status,
)
from radar.pipeline.angles import save_angle, select_angle
from radar.pipeline.verify import add_claim

NOW = "2026-09-14T09:00:00"
DISCLOSURE = settings.content_rules()["disclosure_line"]


def _seed(conn, category="incentives", schemes=("RoDTEP",), select=True):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, primary_source_url,
                              created_at, updated_at)
        VALUES ('SIG-D-1', 'RoDTEP extension', ?, ?, 'SCORED', 'https://dgft.gov.in/n74', ?, ?)
        """,
        (NOW, category, NOW, NOW),
    )
    for scheme in schemes:
        conn.execute(
            "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value) VALUES ('SIG-D-1','scheme',?,?)",
            (scheme.lower(), scheme),
        )
    conn.commit()
    add_claim(
        conn, "SIG-D-1", "RoDTEP rates extended to 30 September 2026", "fact",
        "https://dgft.gov.in/n74", "primary", NOW,
        source_quote="existing RoDTEP rates ... are extended till 30.09.2026", verified_by="analyst",
    )
    aid = save_angle(conn, "SIG-D-1", "Countdown", "timing_transition_risk", "Price October quotes as if RoDTEP is zero",
                     "The extension ends 30 September; nothing yet covers October shipments.", "claude_code", NOW)
    if select:
        select_angle(conn, aid)
    return "SIG-D-1"


def _clean_linkedin_body(disclosure=True):
    words = ("The notification extends rates to 30 September 2026 and says nothing about October. " * 12).strip()
    return words + (f"\n\n{DISCLOSURE}" if disclosure else "")


def test_scaffold_requires_a_selected_angle(conn):
    sid = _seed(conn, select=False)
    with pytest.raises(ValueError, match="no selected angle"):
        create_draft_scaffold(conn, sid, "linkedin", NOW)


def test_rodtep_signal_is_red_tier_with_disclosure(conn):
    sid = _seed(conn)
    assert disclosure_required(conn, sid)
    assert risk_tier(conn, sid) == "red"


def test_non_incentive_amber_signal_has_no_disclosure(conn):
    sid = _seed(conn, category="fx_payments", schemes=("FEMA",))
    assert not disclosure_required(conn, sid)
    assert risk_tier(conn, sid) == "amber"


def test_scaffold_includes_verified_fact_disclosure_and_first_comment(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert "[FACT] RoDTEP rates extended to 30 September 2026" in draft["body"]
    assert DISCLOSURE in draft["body"]
    assert draft["suggested_first_comment"].startswith("Source:")
    assert draft["format"] == "Text list (recurring template)"  # Countdown franchise


def test_instagram_scaffold_is_not_a_linkedin_copy(conn):
    sid = _seed(conn)
    li = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (create_draft_scaffold(conn, sid, "linkedin", NOW),)).fetchone()
    ig = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (create_draft_scaffold(conn, sid, "instagram", NOW),)).fetchone()
    assert "REEL CONCEPT" in ig["body"] and "CAROUSEL CONCEPT" in ig["body"]
    assert li["body"] != ig["body"]


def test_fresh_scaffold_fails_lint_until_placeholders_filled(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    assert any("placeholder" in i for i in lint_draft(conn, draft_id))


def test_lint_flags_banned_phrase_and_exclamation(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "analyst", body=_clean_linkedin_body() + " This is a game changer!")
    issues = lint_draft(conn, v2)
    assert any("game changer" in i for i in issues)
    assert any("Exclamation" in i for i in issues)


def test_lint_flags_untraceable_number(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "analyst",
                            body=_clean_linkedin_body() + " Exporters lose 45% of margin.",
                            exposure_line="Exposure figure pending.")
    assert any("45%" in i for i in lint_draft(conn, v2))


def test_lint_flags_missing_disclosure(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "analyst", body=_clean_linkedin_body(disclosure=False),
                            exposure_line="Exposure figure pending.")
    assert any("Disclosure" in i for i in lint_draft(conn, v2))


def test_clean_draft_passes_lint(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "analyst", body=_clean_linkedin_body(),
                            visual_brief="Calendar graphic", exposure_line="Exposure figure pending.")
    assert lint_draft(conn, v2) == []


def test_edit_creates_new_version_and_keeps_old(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "founder", ["Bad angle"], headline="New headline")
    rows = conn.execute("SELECT id, version, headline FROM content_drafts WHERE signal_id = ? ORDER BY version", (sid,)).fetchall()
    assert [r["version"] for r in rows] == [1, 2]
    assert rows[1]["headline"] == "New headline"
    assert conn.execute("SELECT edit_reason_codes FROM content_drafts WHERE id = ?", (v2,)).fetchone()[0] == '["Bad angle"]'


def test_red_tier_approval_needs_founder_and_external_check(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    v2 = save_draft_version(conn, draft_id, NOW, "analyst", body=_clean_linkedin_body(), exposure_line="Exposure figure pending.", visual_brief="Calendar graphic")
    with pytest.raises(ValueError, match="founder"):
        set_draft_status(conn, v2, "approved", "analyst", NOW)
    with pytest.raises(ValueError, match="external"):
        set_draft_status(conn, v2, "approved", "founder", NOW)
    set_draft_status(conn, v2, "approved", "founder", NOW, external_check_by="Partner CA")
    assert conn.execute("SELECT status FROM content_drafts WHERE id = ?", (v2,)).fetchone()["status"] == "approved"


def test_cannot_approve_a_draft_that_fails_lint(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    with pytest.raises(ValueError, match="checklist"):
        set_draft_status(conn, draft_id, "approved", "founder", NOW, external_check_by="CA")


def test_draft_rejection_requires_reason(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    with pytest.raises(ValueError):
        set_draft_status(conn, draft_id, "rejected", "founder", NOW)
    set_draft_status(conn, draft_id, "rejected", "founder", NOW, reason_code="Too promotional")


def test_record_publication_requires_approval_and_disclosure(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    with pytest.raises(ValueError, match="approved"):
        record_publication(conn, draft_id, "https://linkedin.com/x", "text", NOW)

    v2 = save_draft_version(conn, draft_id, NOW, "analyst", body=_clean_linkedin_body(), exposure_line="Exposure figure pending.", visual_brief="Calendar graphic")
    set_draft_status(conn, v2, "approved", "founder", NOW, external_check_by="CA")
    with pytest.raises(ValueError, match="disclosure"):
        record_publication(conn, v2, "https://linkedin.com/x", "text without it", NOW)
    pid = record_publication(conn, v2, "https://linkedin.com/x", _clean_linkedin_body(), NOW)
    assert pid > 0


def test_auto_publish_flag_cannot_be_enabled(monkeypatch):
    real = settings._load_yaml

    def fake(name):
        data = dict(real(name))
        if name == "scoring_weights.yaml":
            data["feature_flags"] = {**data["feature_flags"], "auto_publish": True}
        return data

    monkeypatch.setattr(settings, "_load_yaml", fake)
    with pytest.raises(RuntimeError, match="auto_publish"):
        settings.feature_flags()


def test_draft_brief_contains_rules(conn):
    sid = _seed(conn)
    brief = render_draft_brief(conn, create_draft_scaffold(conn, sid, "linkedin", NOW))
    assert "Pre-mortem" in brief
    assert DISCLOSURE in brief


def test_content_bundle_creates_all_eight_assets_each_sourced(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    assert set(draft_ids) == set(ASSET_SLOTS)
    assert len(set(draft_ids.values())) == 8  # eight distinct draft rows

    for slot, draft_id in draft_ids.items():
        row = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
        assert row["signal_id"] == sid
        assert row["asset_slot"] == slot
        assert row["channel"] in ("linkedin", "instagram")
        assert row["source_reference"] == "https://dgft.gov.in/n74"  # references the source signal
        assert row["status"] == "draft"


def test_content_bundle_linkedin_variants_carry_distinct_emphasis(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    bodies = [
        conn.execute("SELECT body FROM content_drafts WHERE id = ?", (draft_ids[slot],)).fetchone()["body"]
        for slot in ("linkedin_post_1", "linkedin_post_2", "linkedin_post_3")
    ]
    assert len(set(bodies)) == 3  # three genuinely different scaffolds, not copies
    assert "Consequence first" in bodies[0]
    assert "Fine print" in bodies[1]
    assert "Action window" in bodies[2]


def test_content_bundle_requires_a_selected_angle(conn):
    sid = _seed(conn, select=False)
    with pytest.raises(ValueError, match="no selected angle"):
        create_content_bundle(conn, sid, NOW)


def test_content_bundle_applies_disclosure_and_risk_tier_per_asset(conn):
    sid = _seed(conn)  # incentives category + RoDTEP -> disclosure required, red tier
    draft_ids = create_content_bundle(conn, sid, NOW)
    for slot, draft_id in draft_ids.items():
        row = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
        assert row["disclosure_required"] == 1
        assert row["risk_tier"] == "red"
        if slot != "visual_direction":  # internal brief, not public-facing copy
            assert DISCLOSURE in row["body"]


def test_content_bundle_versions_are_tracked_per_asset_slot(conn):
    sid = _seed(conn)
    first = create_content_bundle(conn, sid, NOW)
    second = create_content_bundle(conn, sid, NOW)  # e.g. re-run after a new angle is selected
    for slot in ASSET_SLOTS:
        v1 = conn.execute("SELECT version FROM content_drafts WHERE id = ?", (first[slot],)).fetchone()["version"]
        v2 = conn.execute("SELECT version FROM content_drafts WHERE id = ?", (second[slot],)).fetchone()["version"]
        assert (v1, v2) == (1, 2)


def test_bundle_brief_lists_every_slot(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    brief = render_bundle_brief(conn, draft_ids)
    for slot in ASSET_SLOTS:
        assert slot in brief


def test_exposure_line_never_joins_a_stated_hs_code_with_an_unrelated_inferred_one(conn):
    # A signal whose entity-extraction text merges several clustered raw
    # items (orchestrator.py) can carry a real, stated HS code alongside a
    # low-confidence chapter inferred from an unrelated product keyword.
    # Joining both into one "HS X, Y" string would read as one fabricated-
    # looking list — the stated code must win outright.
    sid = _seed(conn)
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES (?, 'hs_code', 'HS 6109', '6109', 0.9)", (sid,),
    )
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES (?, 'hs_code', \"inferred from product keyword 'engineering goods' (cluster map)\", '73', 0.4)", (sid,),
    )
    conn.commit()
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    exposure_line = conn.execute("SELECT exposure_line FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()["exposure_line"]
    assert "6109" in exposure_line
    assert "73" not in exposure_line


def test_exposure_line_flags_an_inferred_only_hs_code_as_unverified(conn):
    sid = _seed(conn)
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES (?, 'hs_code', \"inferred from product keyword 'turmeric' (cluster map)\", '0910', 0.4)", (sid,),
    )
    conn.commit()
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    exposure_line = conn.execute("SELECT exposure_line FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()["exposure_line"]
    assert "0910" in exposure_line
    assert "inferred" in exposure_line
    assert "not stated" in exposure_line


def test_legacy_single_draft_scaffold_still_works_after_asset_slot_migration(conn):
    sid = _seed(conn)
    draft_id = create_draft_scaffold(conn, sid, "linkedin", NOW)
    row = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert row["asset_slot"] == "linkedin"
    # A second single-channel draft still versions independently of the channel-wide bundle slots.
    draft_id_2 = create_draft_scaffold(conn, sid, "linkedin", NOW)
    row2 = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id_2,)).fetchone()
    assert row2["version"] == 2
