"""Indian export trade-value lookup — Section 8's "the exposure line":
"India exported [value] of HS [code] to [market] in [period]
(source: TradeStat / UN Comtrade)."

API-key-optional by design (priority 2 of the signal-quality pass): UN
Comtrade's data API needs a free registered subscription key. Without one —
or if the automated lookup fails or has no data for this HS code/period —
this always falls back to a direct manual-lookup link naming the exact HS
code and market to search. The Desk Sheet must never show a fabricated
number and must never go silent about exposure either.

The automated path only covers a handful of markets with a settled numeric
UN M49 reporter/partner code (Comtrade's API keys countries by code, not
name); any destination outside COUNTRY_M49 always takes the manual path.
"""
from __future__ import annotations

import requests

from radar import settings

COMTRADE_DATA_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
COMTRADE_SEARCH_URL = "https://comtradeplus.un.org/"
TRADESTAT_URL = "https://tradestat.commerce.gov.in/eidb/commodity_wise_export"
TIMEOUT_SECONDS = 15
INDIA_REPORTER_CODE = "699"  # UN M49 code for India, Comtrade's reporterCode

# Comtrade partner codes for the destinations this Desk Sheet sees most often.
# Deliberately small and hand-verified rather than a full ISO/M49 table —
# anything not listed here just takes the manual-lookup path below. Values are
# the UN Statistics Division's standard M49 numeric country codes (public
# reference data — https://unstats.un.org/unsd/methodology/m49/), matched
# against radar/pipeline/entities.py's COUNTRY_ALIASES canonical names so a
# destination extracted from a story resolves here whenever it's covered.
COUNTRY_M49 = {
    "United States": "842",
    "European Union": "97",
    "United Kingdom": "826",
    "China": "156",
    "United Arab Emirates": "784",
    "Japan": "392",
    "Australia": "36",
    "Canada": "124",
    "Saudi Arabia": "682",
    "Singapore": "702",
    "Germany": "276",
    "France": "251",
    "Netherlands": "528",
    "Italy": "380",
    "South Korea": "410",
    "Vietnam": "704",
    "Bangladesh": "50",
    "Indonesia": "360",
    "Turkey": "792",
    "Brazil": "76",
    "South Africa": "710",
    "Thailand": "764",
    "Malaysia": "458",
    "Sri Lanka": "144",
    "Pakistan": "586",
    "Oman": "512",
    "Qatar": "634",
    "Kuwait": "414",
    "Israel": "376",
    "Nigeria": "566",
    "Kenya": "404",
    "Philippines": "608",
    "New Zealand": "554",
    "Switzerland": "756",
    "Egypt": "818",
    "Russia": "643",
    "Mexico": "484",
    "Argentina": "32",
    "Chile": "152",
    "Peru": "604",
    "Norway": "579",
}


def _comtrade_lookup(hs_code: str, destination: str) -> dict | None:
    partner_code = COUNTRY_M49.get(destination)
    api_key = settings.COMTRADE_API_KEY
    if not api_key or not partner_code:
        return None
    try:
        resp = requests.get(
            COMTRADE_DATA_URL,
            params={
                "reporterCode": INDIA_REPORTER_CODE,
                "partnerCode": partner_code,
                "cmdCode": hs_code,
                "flowCode": "X",  # exports
            },
            headers={"Ocp-Apim-Subscription-Key": api_key},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rows = resp.json().get("data") or []
        if not rows:
            return None
        row = rows[0]
        value = row.get("primaryValue")
        period = row.get("period")
        if value is None or period is None:
            return None
        return {"value_usd": float(value), "period": str(period)}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def exposure_line(hs_codes: list[str], destination_markets: list[str]) -> str:
    """One Desk-Sheet-ready line: a real figure when auto-retrieval works,
    otherwise a direct manual-lookup link naming the exact HS code/market."""
    if not hs_codes:
        return "No HS code extracted yet — cannot look up trade exposure until research adds one."

    hs_code = hs_codes[0]
    destination = destination_markets[0] if destination_markets else None

    # Try every extracted destination, not just the first — a story naming
    # both an M49-covered market and an uncovered one shouldn't fall back to
    # the manual link when a later market would have resolved automatically.
    for candidate in destination_markets:
        result = _comtrade_lookup(hs_code, candidate)
        if result:
            return (
                f"India exported ${result['value_usd']:,.0f} of HS {hs_code} to {candidate} in "
                f"{result['period']} (source: UN Comtrade, auto-retrieved)."
            )

    market_label = destination or "the relevant market(s)"
    return (
        f"Trade value not auto-retrieved — look up HS {hs_code} exports to {market_label} on "
        f"UN Comtrade ({COMTRADE_SEARCH_URL}) or India TradeStat ({TRADESTAT_URL}) before quoting "
        f"an exposure figure."
    )
