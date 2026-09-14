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


def test_round_trip_json_serialization():
    exp = _analyze("DGFT notified RoDTEP rate extension for India")
    restored = IndianExposure.from_json(exp.to_json())
    assert restored == exp
