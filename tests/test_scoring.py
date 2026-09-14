from radar.pipeline.exposure import IndianExposure
from radar.pipeline.scoring import (
    compute_base_score,
    compute_penalties,
    decide,
    evaluate_gates,
    score_criteria,
    score_signal,
)


def _insert_signal(conn, signal_id, topic_category="incentives", created_at="2026-09-14T06:00:00"):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, created_at, updated_at)
        VALUES (?, 'Test signal', ?, ?, 'TRIAGED', ?, ?)
        """,
        (signal_id, created_at, topic_category, created_at, created_at),
    )
    conn.commit()


def test_gates_pass_with_primary_source_and_confirmed_exposure(conn):
    _insert_signal(conn, "SIG-TEST-0001")
    exposure = IndianExposure(direct_effect="true")
    result = evaluate_gates(conn, "SIG-TEST-0001", exposure, has_primary_source=True, secondary_source_count=0, text="")
    assert result.passed
    assert result.failed_gates == []


def test_gate1_fails_without_evidence(conn):
    _insert_signal(conn, "SIG-TEST-0002")
    exposure = IndianExposure(direct_effect="true")
    result = evaluate_gates(conn, "SIG-TEST-0002", exposure, has_primary_source=False, secondary_source_count=1, text="")
    assert "G1_evidence" in result.failed_gates


def test_gate2_fails_without_indian_implication(conn):
    _insert_signal(conn, "SIG-TEST-0003")
    exposure = IndianExposure(direct_effect="uncertain")
    result = evaluate_gates(conn, "SIG-TEST-0003", exposure, has_primary_source=True, secondary_source_count=0, text="")
    assert "G2_indian_implication" in result.failed_gates


def _rodtep_entity(conn, signal_id):
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES (?, 'scheme', 'rodtep', 'RoDTEP', 1.0)",
        (signal_id,),
    )


def _publish(conn, signal_id, published_at):
    draft = conn.execute(
        "INSERT INTO content_drafts (signal_id, channel, status, created_at) VALUES (?, 'linkedin', 'approved', ?)",
        (signal_id, published_at),
    ).lastrowid
    conn.execute(
        "INSERT INTO published_content (draft_id, channel, published_at, url, status) VALUES (?, 'linkedin', ?, 'u', 'published')",
        (draft, published_at),
    )
    conn.commit()


def test_gate3_fails_when_we_published_the_same_story_within_14_days(conn):
    _insert_signal(conn, "SIG-TEST-0004", topic_category="incentives", created_at="2026-09-01T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0004")
    _publish(conn, "SIG-TEST-0004", "2026-09-02T11:00:00")
    _insert_signal(conn, "SIG-TEST-0005", topic_category="incentives", created_at="2026-09-10T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0005")
    conn.commit()
    exposure = IndianExposure(direct_effect="true")
    result = evaluate_gates(conn, "SIG-TEST-0005", exposure, has_primary_source=True, secondary_source_count=0, text="")
    assert "G3_repeat" in result.failed_gates


def test_gate3_ignores_similar_signals_we_never_published(conn):
    # "Covered" means published by Maverick Minds, not merely seen by the collector.
    _insert_signal(conn, "SIG-TEST-0012", topic_category="incentives", created_at="2026-09-09T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0012")
    _insert_signal(conn, "SIG-TEST-0013", topic_category="incentives", created_at="2026-09-10T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0013")
    conn.commit()
    result = evaluate_gates(conn, "SIG-TEST-0013", IndianExposure(direct_effect="true"), True, 0, "")
    assert "G3_repeat" not in result.failed_gates


def test_gate3_allows_repeat_after_the_14_day_window(conn):
    _insert_signal(conn, "SIG-TEST-0014", topic_category="incentives", created_at="2026-08-01T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0014")
    _publish(conn, "SIG-TEST-0014", "2026-08-01T11:00:00")
    _insert_signal(conn, "SIG-TEST-0015", topic_category="incentives", created_at="2026-09-10T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0015")
    conn.commit()
    result = evaluate_gates(conn, "SIG-TEST-0015", IndianExposure(direct_effect="true"), True, 0, "")
    assert "G3_repeat" not in result.failed_gates


def test_near_repeat_penalty_applies_within_90_days_of_a_related_post(conn):
    _insert_signal(conn, "SIG-TEST-0016", topic_category="incentives", created_at="2026-07-01T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0016")
    _publish(conn, "SIG-TEST-0016", "2026-07-01T11:00:00")
    _insert_signal(conn, "SIG-TEST-0017", topic_category="countervailing_duty", created_at="2026-09-10T00:00:00")
    _rodtep_entity(conn, "SIG-TEST-0017")
    conn.commit()
    _, applied = compute_penalties(conn, "SIG-TEST-0017", "DGFT notified rates", False)
    assert "near_repeat" in applied


def test_formula_reproduces_the_specs_worked_scores():
    """content_scoring_model.md 'Worked scoring' table: same criterion scores in,
    same base score out. Guards the formula and the configured weights."""
    order = ["exporter_impact", "actionability", "angle_strength", "indian_exposure", "under_coverage",
             "time_sensitivity", "explainability", "audience_fit", "evidence_quality"]
    worked = {
        "RoDTEP/RoSCTL extension": ([5, 4, 4, 5, 3, 5, 4, 5, 5], 88.0),
        "FEMA 2026": ([4, 5, 4, 5, 4, 5, 4, 4, 4], 87.4),
        "US CVD oleoresin paprika": ([3, 3, 5, 2, 5, 3, 3, 3, 5], 70.4),
        "Hormuz freight": ([4, 3, 3, 4, 2, 4, 3, 3, 3], 66.0),
        "Hormuz + Incoterm angle": ([4, 5, 5, 4, 2, 4, 3, 3, 3], 78.0),
        "August trade data": ([2, 1, 2, 4, 1, 4, 4, 3, 5], 49.8),
    }
    for name, (scores, expected) in worked.items():
        assert compute_base_score(dict(zip(order, map(float, scores)))) == expected, name
    # Hormuz final: base x 0.85 (secondary) - 5 (sensitivity) = 51 in the spec
    assert round(66.0 * 0.85 - 5) == 51
    assert decide(51) == "watchlist" and decide(61) == "secondary" and decide(88) == "lead"


def test_gate4_fails_on_unverifiable_numeric_claim(conn):
    _insert_signal(conn, "SIG-TEST-0006")
    exposure = IndianExposure(direct_effect="true")
    result = evaluate_gates(
        conn, "SIG-TEST-0006", exposure, has_primary_source=False, secondary_source_count=0,
        text="Exporters could save up to 25% under this scheme",
    )
    assert "G4_unverifiable_numbers" in result.failed_gates


def test_score_criteria_rewards_direct_effect_and_actionability():
    strong = IndianExposure(
        direct_effect="true", landed_cost_change="true", market_access_change="true",
        threat="true", actionable_this_week="true", clusters=["tirupur"], hs_codes=["6109"],
        destination_markets=["United States"],
    )
    weak = IndianExposure(direct_effect="uncertain", actionable_this_week="uncertain")
    strong_scores = score_criteria(strong, True, 0, 5, True, True, 5, claim_count=2)
    weak_scores = score_criteria(weak, False, 0, 1, False, False, 5, claim_count=0)
    assert strong_scores["exporter_impact"] > weak_scores["exporter_impact"]
    assert strong_scores["actionability"] > weak_scores["actionability"]
    assert strong_scores["indian_exposure"] > weak_scores["indian_exposure"]


def test_evidence_quality_capped_when_no_claims_extracted():
    # A primary-sourced headline with zero extracted claims (e.g. "no claims
    # extracted" press releases) must not score full evidence quality just
    # because the source itself is primary.
    exposure = IndianExposure(direct_effect="true")
    with_claims = score_criteria(exposure, True, 0, 3, False, False, 3, claim_count=2)
    no_claims = score_criteria(exposure, True, 0, 3, False, False, 3, claim_count=0)
    assert no_claims["evidence_quality"] < with_claims["evidence_quality"]
    assert no_claims["evidence_quality"] <= 1.0


def test_evidence_quality_unaffected_when_claims_present():
    exposure = IndianExposure(direct_effect="true")
    scores = score_criteria(exposure, True, 0, 3, False, False, 3, claim_count=3)
    assert scores["evidence_quality"] == 5.0


def test_routine_administrative_review_is_penalized(conn):
    _insert_signal(conn, "SIG-TEST-0018")
    total, applied = compute_penalties(
        conn, "SIG-TEST-0018",
        "Glycine From India: Preliminary Results of Antidumping Duty Administrative Review",
        False,
    )
    assert "routine_procedural_review" in applied
    assert total > 0


def test_initiation_notice_is_not_penalized_as_routine(conn):
    _insert_signal(conn, "SIG-TEST-0019")
    total, applied = compute_penalties(
        conn, "SIG-TEST-0019",
        "Certain Widgets From India: Initiation of Countervailing Duty Investigation",
        False,
    )
    assert "routine_procedural_review" not in applied


def test_compute_base_score_matches_weighted_sum_when_all_criteria_maxed():
    all_max = {c: 5.0 for c in [
        "exporter_impact", "actionability", "angle_strength", "indian_exposure",
        "under_coverage", "time_sensitivity", "explainability", "audience_fit", "evidence_quality",
    ]}
    assert compute_base_score(all_max) == 100.0


def test_compute_base_score_zero_when_all_criteria_zero():
    all_zero = {c: 0.0 for c in [
        "exporter_impact", "actionability", "angle_strength", "indian_exposure",
        "under_coverage", "time_sensitivity", "explainability", "audience_fit", "evidence_quality",
    ]}
    assert compute_base_score(all_zero) == 0.0


def test_penalties_apply_for_geopolitical_sensitivity(conn):
    _insert_signal(conn, "SIG-TEST-0007")
    total, applied = compute_penalties(conn, "SIG-TEST-0007", "The Strait of Hormuz blockade raised war-risk premiums", False)
    assert "geopolitical_sensitivity" in applied
    assert total >= 5


def test_penalties_apply_for_unlabelled_speculation(conn):
    _insert_signal(conn, "SIG-TEST-0008")
    total, applied = compute_penalties(conn, "SIG-TEST-0008", "This could lead to a major shift in trade flows", False)
    assert "speculation" in applied


def test_penalties_zero_for_clean_factual_text(conn):
    _insert_signal(conn, "SIG-TEST-0009")
    total, applied = compute_penalties(conn, "SIG-TEST-0009", "DGFT notified an extension of RoDTEP rates", False)
    assert total == 0.0
    assert applied == []


def test_decide_thresholds():
    assert decide(90) == "lead"
    assert decide(75) == "lead"
    assert decide(70) == "secondary"
    assert decide(50) == "watchlist"
    assert decide(10) == "discard"


def test_score_signal_end_to_end_lead_candidate(conn):
    _insert_signal(conn, "SIG-TEST-0010", topic_category="incentives")
    exposure = IndianExposure(
        direct_effect="true", landed_cost_change="true", market_access_change="true",
        compliance_cost_change="true", threat="true", opportunity="true",
        actionable_this_week="true", clusters=["tirupur", "karur"], hs_codes=["6109", "6302"],
        destination_markets=["United States"], confidence=0.9,
    )
    breakdown = score_signal(
        conn, "SIG-TEST-0010",
        text="DGFT notified an extension of RoDTEP rates to 30 September 2026 for exporters",
        exposure=exposure, has_primary_source=True, secondary_source_count=0,
        saturation_score_0to5=5, entity_count=6, now_iso="2026-09-14T06:30:00",
    )
    assert breakdown["gates"]["passed"] is True
    assert breakdown["decision"] in ("lead", "secondary")
    row = conn.execute("SELECT status, score_final FROM signals WHERE id = 'SIG-TEST-0010'").fetchone()
    assert row["score_final"] == breakdown["final_score"]
    assert row["status"] in ("SCORED",)


def test_score_signal_end_to_end_discard_when_gates_fail(conn):
    _insert_signal(conn, "SIG-TEST-0011")
    exposure = IndianExposure(direct_effect="uncertain")
    breakdown = score_signal(
        conn, "SIG-TEST-0011", text="Some unrelated development",
        exposure=exposure, has_primary_source=False, secondary_source_count=0,
        saturation_score_0to5=1, entity_count=0, now_iso="2026-09-14T06:30:00",
    )
    assert breakdown["gates"]["passed"] is False
    assert breakdown["decision"] == "discard"
    row = conn.execute("SELECT status FROM signals WHERE id = 'SIG-TEST-0011'").fetchone()
    assert row["status"] == "REJECTED"
