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


def test_gate3_fails_on_exact_repeat_within_window(conn):
    _insert_signal(conn, "SIG-TEST-0004", topic_category="incentives", created_at="2026-09-01T00:00:00")
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES ('SIG-TEST-0004', 'scheme', 'rodtep', 'RoDTEP', 1.0)"
    )
    _insert_signal(conn, "SIG-TEST-0005", topic_category="incentives", created_at="2026-09-10T00:00:00")
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
        "VALUES ('SIG-TEST-0005', 'scheme', 'rodtep', 'RoDTEP', 1.0)"
    )
    conn.commit()
    exposure = IndianExposure(direct_effect="true")
    result = evaluate_gates(conn, "SIG-TEST-0005", exposure, has_primary_source=True, secondary_source_count=0, text="")
    assert "G3_repeat" in result.failed_gates


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
    strong_scores = score_criteria(strong, True, 0, 5, True, True, 5)
    weak_scores = score_criteria(weak, False, 0, 1, False, False, 5)
    assert strong_scores["exporter_impact"] > weak_scores["exporter_impact"]
    assert strong_scores["actionability"] > weak_scores["actionability"]
    assert strong_scores["indian_exposure"] > weak_scores["indian_exposure"]


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
