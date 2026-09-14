from datetime import date

from radar.pipeline.entities import extract_entities
from radar.pipeline.exposure import IndianExposure, analyze_indian_exposure


def _analyze(text, reference_date=None):
    entities = extract_entities(text)
    return analyze_indian_exposure(text, entities, reference_date=reference_date)


def test_direct_effect_true_when_indian_authority_named():
    exp = _analyze("DGFT notified an extension of RoDTEP rates for exporters")
    assert exp.direct_effect == "true"


def test_direct_effect_uncertain_when_no_india_signal_at_all():
    exp = _analyze("The quick brown fox jumps over the lazy dog")
    assert exp.direct_effect == "uncertain"


def test_direct_effect_uncertain_for_foreign_only_story_without_india_mention():
    exp = _analyze("China GACC updated its food safety registration portal for imports")
    assert exp.direct_effect == "uncertain"


def test_landed_cost_change_true_when_duty_keyword_present():
    exp = _analyze("US finalises countervailing duty on Indian oleoresin paprika at 25.29%")
    assert exp.landed_cost_change == "true"
    assert exp.threat == "true"


def test_landed_cost_change_false_when_regulatory_context_but_no_duty_keyword():
    exp = _analyze("New labelling standard published for packaged food imports into the EU, India included")
    assert exp.landed_cost_change == "false"
    assert exp.compliance_cost_change == "true"


def test_actionable_this_week_true_within_window():
    exp = _analyze(
        "RoDTEP rates extended to 30 September 2026 for all exporters",
        reference_date=date(2026, 9, 14),
    )
    assert exp.actionable_this_week == "true"


def test_actionable_this_week_false_when_deadline_far_out():
    exp = _analyze(
        "EUDR deadline extended to 30 December 2027 for compliance",
        reference_date=date(2026, 9, 14),
    )
    assert exp.actionable_this_week == "false"


def test_hs_codes_and_clusters_populate_affected_products():
    exp = _analyze("New rule affects HS 6109 knitwear exports from India")
    assert "6109" in exp.hs_codes
    assert "tirupur" in exp.clusters


def test_never_fabricates_destination_market_from_thin_air():
    exp = _analyze("India notified a domestic scheme change with no foreign country mentioned")
    assert exp.destination_markets == []


def test_confidence_increases_with_more_corroborating_evidence():
    thin = _analyze("Some regulation changed somewhere")
    rich = _analyze(
        "DGFT notified RoDTEP rate extension effective from 1 October 2026 for HS 6109 knitwear exporters in India"
    )
    assert rich.confidence > thin.confidence


# Regression cases from the first live run (14 Sep 2026).

def test_rbi_monetary_operation_is_not_an_exporter_effect():
    for title in ["RBI announces OMO Sale of Government of India Securities",
                  "RBI imposes monetary penalty on Asset Care & Reconstruction Enterprise Limited",
                  "RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction under LAF"]:
        assert _analyze(title).direct_effect == "uncertain", title


def test_rbi_fema_export_rule_still_counts():
    assert _analyze("RBI notifies FEMA export realisation rules for exporters").direct_effect == "true"


def test_institute_event_announcement_is_not_an_exporter_effect():
    # "Indian Institute of Foreign Trade" trips the generic TRADE_RELEVANCE
    # phrase "foreign trade" purely as part of an institution's name; a campus
    # conclave announcement carries no actual trade-policy content.
    exp = _analyze(
        "Indian Institute of Foreign Trade GIFT City Campus Hosts NEXUS 2026: Connecting Talent, "
        "Technology & Global Business, a leadership Conclave"
    )
    assert exp.direct_effect == "uncertain"


def test_conclave_with_real_duty_content_still_counts():
    # The event-noise filter must not suppress a genuine trade-policy story
    # just because it also happens to mention a conclave/summit.
    exp = _analyze(
        "At the FIEO export conclave, DGFT announced a new anti-dumping duty on Indian steel exports"
    )
    assert exp.direct_effect == "true"


def test_dgft_export_policy_amendment_is_a_market_access_change():
    exp = _analyze("DGFT Notification 34/2026-27: Amendment in the Export Policy of Wheat Flour and related products - reg.")
    assert exp.direct_effect == "true"
    assert exp.market_access_change == "true"
    assert exp.affected_products == ["wheat flour"]
    assert exp.breadth == "specific"


def test_dgft_procedure_change_is_a_compliance_change():
    exp = _analyze("DGFT Trade Notice 27/2026-27: Inviting comments on Amendment in Para 2.93 of the Handbook of Procedures")
    assert exp.compliance_cost_change == "true"


def test_us_investigation_naming_india_extracts_product_and_all_respondents():
    exp = _analyze("Certain Linear Hydraulic Cylinders and Parts Thereof From the People's Republic of China, "
                   "India, and Mexico: Initiation of Countervailing Duty Investigations")
    assert exp.direct_effect == "true"
    assert exp.affected_products == ["linear hydraulic cylinders"]
    assert set(exp.destination_markets) == {"China", "Mexico"}
    assert exp.threat == "true"


def test_round_trip_json_serialization():
    exp = _analyze("DGFT notified RoDTEP rate extension for India")
    restored = IndianExposure.from_json(exp.to_json())
    assert restored == exp
