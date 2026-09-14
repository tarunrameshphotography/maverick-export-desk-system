"""Indian Export Exposure analysis (Section 8). Answers the twelve questions
from evidence already extracted by entities.py — never invents a connection.
Every boolean field can also be "uncertain"; that is treated as a valid,
expected answer rather than a failure to determine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date

INDIA_DIRECT_AUTHORITIES = {"DGFT", "CBIC", "RBI", "DGTR", "ICEGATE"}
INDIA_SPECIFIC_SCHEMES = {"RoDTEP", "RoSCTL", "Duty Drawback"}

LANDED_COST_KEYWORDS = re.compile(
    r"\b(tariff|duty|customs duty|countervailing|anti-?dumping|safeguard duty|cess|surcharge)\b", re.I
)
MARKET_ACCESS_KEYWORDS = re.compile(
    r"\b(import ban|import restriction|quota|licensing requirement|market access|"
    r"trade agreement|free trade agreement|\bfta\b|ceta)\b", re.I
)
COMPLIANCE_COST_KEYWORDS = re.compile(
    r"\b(standard|regulation|sps|tbt|cbam|eudr|labelling|labeling|residue limit|certification|compliance)\b", re.I
)
OPPORTUNITY_KEYWORDS = re.compile(
    r"\b(duty-?free|preferential (?:access|tariff|treatment)|enters into force|market access gained|"
    r"tariff (?:cut|reduction|elimination))\b", re.I
)
THREAT_KEYWORDS = re.compile(
    r"\b(ban|restrict|refus(?:al|ed)|reject(?:ion|ed)?|countervailing|anti-?dumping|penalt(?:y|ies)|"
    r"tariff (?:hike|increase))\b", re.I
)
COMPETITOR_POLICY_KEYWORDS = re.compile(
    r"\b(competitor|rival exporter|preferential tariff|graduation|gsp)\b", re.I
)
# Export incentive rates change the exporter's net realised price — the
# spec's "changes price" exporter-impact test — even though no duty moves.
INCENTIVE_PRICE_KEYWORDS = re.compile(
    r"\b(rodtep|rosctl|duty drawback|export incentive|remission of duties)\b", re.I
)
CASH_CYCLE_KEYWORDS = re.compile(
    r"\b(realisation period|realization period|export proceeds|payment terms|credit period|"
    r"edpms|edf|set-off|repatriation)\b", re.I
)
# Instruments that apply to (nearly) every Indian exporter regardless of product.
ECONOMY_WIDE_INSTRUMENTS = {"RoDTEP", "FEMA", "Duty Drawback"}
ACTIONABLE_WINDOW_DAYS = 30


@dataclass
class IndianExposure:
    direct_effect: str = "uncertain"          # "true" | "false" | "uncertain"
    affected_products: list[str] = field(default_factory=list)
    hs_codes: list[str] = field(default_factory=list)
    destination_markets: list[str] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    breadth: str = "unknown"                 # "economy_wide" | "specific" | "unknown"
    landed_cost_change: str = "uncertain"
    cash_cycle_change: str = "uncertain"
    market_access_change: str = "uncertain"
    compliance_cost_change: str = "uncertain"
    competitive_positioning_change: str = "uncertain"
    opportunity: str = "uncertain"
    threat: str = "uncertain"
    actionable_this_week: str = "uncertain"
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(payload: str) -> "IndianExposure":
        return IndianExposure(**json.loads(payload))


def _tribool(matched: bool, has_any_evidence: bool) -> str:
    if matched:
        return "true"
    return "false" if has_any_evidence else "uncertain"


def analyze_indian_exposure(
    text: str,
    entities: list[dict],
    reference_date: date | None = None,
) -> IndianExposure:
    reference_date = reference_date or date.today()
    notes: list[str] = []

    countries = {e["normalized_value"] for e in entities if e["entity_type"] == "country"}
    authorities = {e["normalized_value"] for e in entities if e["entity_type"] == "authority"}
    schemes = {e["normalized_value"] for e in entities if e["entity_type"] == "scheme"}
    hs_codes = sorted({e["normalized_value"] for e in entities if e["entity_type"] == "hs_code"})
    clusters = sorted({e["normalized_value"] for e in entities if e["entity_type"] == "cluster"})
    destination_markets = sorted(countries - {"India"})

    india_authority_hit = bool(authorities & INDIA_DIRECT_AUTHORITIES)
    india_scheme_hit = bool(schemes & INDIA_SPECIFIC_SCHEMES)
    india_mentioned = "India" in countries

    if india_authority_hit or india_scheme_hit:
        direct_effect = "true"
        notes.append("Indian authority or India-specific scheme named directly.")
    elif india_mentioned:
        direct_effect = "true"
        notes.append("India named as a country in the signal text.")
    elif countries or hs_codes:
        direct_effect = "uncertain"
        notes.append("Foreign development with no explicit India mention — exposure unconfirmed.")
    else:
        direct_effect = "uncertain"
        notes.append("No country or product entity extracted; cannot assess exposure without more evidence.")

    has_duty = bool(LANDED_COST_KEYWORDS.search(text))
    has_incentive_price = bool(INCENTIVE_PRICE_KEYWORDS.search(text))
    has_landed = has_duty or has_incentive_price
    if has_incentive_price and not has_duty:
        notes.append("Export incentive rates change the exporter's net realised price (treated as a price effect).")
    has_cash_cycle = bool(CASH_CYCLE_KEYWORDS.search(text))
    has_market_access = bool(MARKET_ACCESS_KEYWORDS.search(text))
    has_compliance = bool(COMPLIANCE_COST_KEYWORDS.search(text))
    has_opportunity = bool(OPPORTUNITY_KEYWORDS.search(text))
    has_threat = bool(THREAT_KEYWORDS.search(text))
    has_competitor_signal = bool(COMPETITOR_POLICY_KEYWORDS.search(text)) and len(countries) >= 2

    any_regulatory_evidence = has_landed or has_market_access or has_compliance or has_cash_cycle

    instruments = schemes | {e["normalized_value"] for e in entities if e["entity_type"] == "regulation"}
    if direct_effect == "true" and instruments & ECONOMY_WIDE_INSTRUMENTS:
        breadth = "economy_wide"
        notes.append(f"{', '.join(sorted(instruments & ECONOMY_WIDE_INSTRUMENTS))} applies to exporters across products.")
    elif hs_codes or clusters:
        breadth = "specific"
    else:
        breadth = "unknown"

    landed_cost_change = _tribool(has_landed, any_regulatory_evidence)
    cash_cycle_change = _tribool(has_cash_cycle, any_regulatory_evidence)
    market_access_change = _tribool(has_market_access, any_regulatory_evidence)
    compliance_cost_change = _tribool(has_compliance, any_regulatory_evidence)
    competitive_positioning_change = _tribool(has_competitor_signal, len(countries) >= 2)
    opportunity = _tribool(has_opportunity, any_regulatory_evidence or has_threat)
    threat = _tribool(has_threat, any_regulatory_evidence or has_opportunity)

    deadline_entities = [e for e in entities if e["entity_type"] in ("deadline", "effective_date")]
    actionable = "uncertain"
    if deadline_entities:
        for e in deadline_entities:
            try:
                target = date.fromisoformat(e["normalized_value"])
            except ValueError:
                continue
            days_out = (target - reference_date).days
            if 0 <= days_out <= ACTIONABLE_WINDOW_DAYS:
                actionable = "true"
                notes.append(f"{e['entity_type']} {e['normalized_value']} falls within {ACTIONABLE_WINDOW_DAYS} days.")
                break
        else:
            actionable = "false"
    else:
        notes.append("No effective/deadline date extracted — cannot judge this-week actionability.")

    evidence_signals = sum(
        [india_authority_hit, india_scheme_hit, india_mentioned, has_landed or has_cash_cycle, has_market_access,
         has_compliance, bool(hs_codes), bool(clusters), bool(deadline_entities)]
    )
    confidence = min(1.0, evidence_signals / 6)

    return IndianExposure(
        direct_effect=direct_effect,
        affected_products=clusters,  # cluster labels double as a product proxy in v1
        hs_codes=hs_codes,
        destination_markets=destination_markets,
        clusters=clusters,
        breadth=breadth,
        landed_cost_change=landed_cost_change,
        cash_cycle_change=cash_cycle_change,
        market_access_change=market_access_change,
        compliance_cost_change=compliance_cost_change,
        competitive_positioning_change=competitive_positioning_change,
        opportunity=opportunity,
        threat=threat,
        actionable_this_week=actionable,
        confidence=round(confidence, 2),
        notes=notes,
    )
