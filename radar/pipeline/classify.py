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


def classify_topic_category(entities: list[dict]) -> str:
    """First entity (in extraction order) whose normalized value maps to a
    category wins — entities.py already orders scheme/authority hits before
    generic ones, so a scheme-specific category (e.g. 'incentives') is
    preferred over a generic authority category (e.g. 'customs_dgft') when
    both are present in the same signal."""
    for entity in entities:
        category = CATEGORY_BY_ENTITY_VALUE.get(entity.get("normalized_value", ""))
        if category:
            assert category in _VALID_CATEGORY_IDS
            return category
    return "uncategorised"
