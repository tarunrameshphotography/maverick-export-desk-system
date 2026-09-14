"""Step 2 of the daily workflow (Section 17): classify a signal into the
canonical taxonomy (Section 5 / config/categories.yaml) from its extracted
entities. Falls back to 'uncategorised' rather than guessing."""
from __future__ import annotations

CATEGORY_BY_ENTITY_VALUE: dict[str, str] = {
    "RoDTEP": "incentives",
    "RoSCTL": "incentives",
    "Duty Drawback": "incentives",
    "Countervailing Duty": "countervailing_duty",
    "Anti-Dumping Duty": "anti_dumping",
    "Safeguard Duty": "safeguards",
    "CBAM": "carbon_environment",
    "EUDR": "carbon_environment",
    "RASFF": "sps_food_alert",
    "US FDA": "sps_food_alert",
    "FEMA": "fx_payments",
    "UK-India CETA": "trade_agreements",
    "US Section 301": "tariffs",
    "US Section 232": "tariffs",
    "DGFT": "customs_dgft",
    "CBIC": "customs_cbic",
    "ICEGATE": "customs_cbic",
    "DGTR": "trade_remedies",
}

_VALID_CATEGORY_IDS = {
    "tariffs", "trade_agreements", "rules_of_origin", "customs_dgft", "customs_cbic",
    "customs_other", "trade_remedies", "anti_dumping", "countervailing_duty", "safeguards",
    "tbt_sps", "product_standards", "import_restrictions", "sps_food_alert", "carbon_environment",
    "sanctions", "geopolitics", "shipping_logistics", "fx_payments", "export_finance",
    "incentives", "government_scheme", "msme_policy", "market_demand", "commodity_movements",
    "buyer_signals", "competitor_policy", "trade_statistics", "sector_cluster", "legal_ruling",
    "export_promotion", "field_intelligence", "news_confirmation", "uncategorised",
}


# Most specific wins. A US CVD case that *mentions* RoDTEP is a trade-remedy
# story, not an incentives story; a DGFT notice about RoDTEP is an incentives
# story, not a generic DGFT story.
CATEGORY_PRIORITY = [
    "countervailing_duty", "anti_dumping", "safeguards", "carbon_environment", "sps_food_alert",
    "tariffs", "trade_agreements", "fx_payments", "incentives", "trade_remedies",
    "customs_cbic", "customs_dgft",
]


def classify_topic_category(entities: list[dict]) -> str:
    found = {CATEGORY_BY_ENTITY_VALUE[e["normalized_value"]]
             for e in entities if e.get("normalized_value") in CATEGORY_BY_ENTITY_VALUE}
    for category in CATEGORY_PRIORITY:
        if category in found:
            assert category in _VALID_CATEGORY_IDS
            return category
    return "uncategorised"
