"""Rule-based entity extraction and normalization (Section 7). Deliberately
dictionary/regex-driven rather than model-driven for v1: it's free, fast,
deterministic, and testable — exactly the "cheap/fast" tier the spec reserves
for triage-grade extraction (Section 25). Every entity is normalized against
a controlled vocabulary so "U.S." / "US" / "United States" collapse to one value.
"""
from __future__ import annotations

import re

from dateutil import parser as dateparser

from radar import settings

COUNTRY_ALIASES = {
    "india": "India", "indian": "India",
    "us": "United States", "usa": "United States",
    "united states": "United States", "america": "United States",
    "eu": "European Union", "european union": "European Union",
    "uk": "United Kingdom", "united kingdom": "United Kingdom", "britain": "United Kingdom",
    "uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
    "saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia",
    "australia": "Australia", "canada": "Canada", "japan": "Japan",
    "singapore": "Singapore", "germany": "Germany", "france": "France",
    "netherlands": "Netherlands", "italy": "Italy",
    "south korea": "South Korea", "korea": "South Korea",
    "vietnam": "Vietnam", "bangladesh": "Bangladesh", "china": "China",
}

# scheme/authority keyword -> (entity_type, normalized_value)
SCHEME_AUTHORITY_KEYWORDS: dict[str, tuple[str, str]] = {
    "rodtep": ("scheme", "RoDTEP"),
    "rosctl": ("scheme", "RoSCTL"),
    "duty drawback": ("scheme", "Duty Drawback"),
    "fema": ("regulation", "FEMA"),
    "cbam": ("regulation", "CBAM"),
    "eudr": ("regulation", "EUDR"),
    "rasff": ("regulation", "RASFF"),
    "dgft": ("authority", "DGFT"),
    "cbic": ("authority", "CBIC"),
    "rbi": ("authority", "RBI"),
    "dgtr": ("authority", "DGTR"),
    "icegate": ("authority", "ICEGATE"),
    "wto": ("authority", "WTO"),
    "eping": ("authority", "WTO ePing"),
    "ustr": ("authority", "USTR"),
    "federal register": ("authority", "US Federal Register"),
    "fda": ("authority", "US FDA"),
    "cbp": ("authority", "US CBP"),
    "gacc": ("authority", "China GACC"),
    "countervailing duty": ("regulation", "Countervailing Duty"),
    "cvd": ("regulation", "Countervailing Duty"),
    "anti-dumping": ("regulation", "Anti-Dumping Duty"),
    "antidumping": ("regulation", "Anti-Dumping Duty"),
    "safeguard duty": ("regulation", "Safeguard Duty"),
    "ceta": ("trade_agreement", "UK-India CETA"),
    "section 301": ("regulation", "US Section 301"),
    "section 232": ("regulation", "US Section 232"),
}

# keyword -> cluster id, sourced from config/clusters.yaml `products`
_CLUSTER_KEYWORD_MAP: dict[str, str] | None = None


def _cluster_keyword_map() -> dict[str, str]:
    global _CLUSTER_KEYWORD_MAP
    if _CLUSTER_KEYWORD_MAP is None:
        mapping: dict[str, str] = {}
        for cluster in settings.clusters():
            for product in cluster.get("products", []):
                mapping[product.lower()] = cluster["id"]
        _CLUSTER_KEYWORD_MAP = mapping
    return _CLUSTER_KEYWORD_MAP


def _cluster_hs_chapter_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cluster in settings.clusters():
        for chapter in cluster.get("hs_chapters", []):
            mapping[chapter] = cluster["id"]
    return mapping


DATE_CONTEXT_PATTERNS = [
    (re.compile(r"(?:effective from|w\.e\.f\.?|with effect from)\s+([^.,;]+)", re.I), "effective_date"),
    (re.compile(r"(?:extended to|extended till|till|until|deadline of|deadline:)\s+([^.,;]+)", re.I), "deadline"),
    (re.compile(r"(?:notified on|dated|notification dated)\s+([^.,;]+)", re.I), "announcement_date"),
]
HS_CODE_PATTERN = re.compile(r"\bHS\s*(?:code\s*)?(\d{4,8})\b", re.I)
CHAPTER_PATTERN = re.compile(r"\bCh(?:apter|\.)?\s*(\d{2})\b", re.I)


def _try_parse_date(text: str) -> str | None:
    text = text.strip()
    try:
        return dateparser.parse(text, fuzzy=True, dayfirst=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def extract_countries(text: str) -> list[dict]:
    # Periods are stripped so "U.S." / "U.K." match the same aliases as
    # "US" / "UK" without needing a fragile word-boundary regex around them.
    lowered = text.lower().replace(".", "")
    found: dict[str, str] = {}
    for alias, canonical in COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            found[canonical] = alias
    return [
        {"entity_type": "country", "raw_value": raw, "normalized_value": canonical, "confidence": 1.0}
        for canonical, raw in found.items()
    ]


def extract_schemes_and_authorities(text: str) -> list[dict]:
    lowered = text.lower()
    results = []
    seen: set[tuple[str, str]] = set()
    for keyword, (entity_type, normalized) in SCHEME_AUTHORITY_KEYWORDS.items():
        if keyword in lowered and (entity_type, normalized) not in seen:
            seen.add((entity_type, normalized))
            results.append(
                {"entity_type": entity_type, "raw_value": keyword, "normalized_value": normalized, "confidence": 1.0}
            )
    return results


def extract_hs_and_clusters(text: str) -> list[dict]:
    results = []
    chapter_map = _cluster_hs_chapter_map()
    seen_hs: set[str] = set()
    seen_clusters: set[str] = set()

    for match in HS_CODE_PATTERN.finditer(text):
        code = match.group(1)
        if code not in seen_hs:
            seen_hs.add(code)
            results.append({"entity_type": "hs_code", "raw_value": match.group(0), "normalized_value": code, "confidence": 0.9})
        chapter = code[:2]
        if chapter in chapter_map and chapter_map[chapter] not in seen_clusters:
            seen_clusters.add(chapter_map[chapter])
            results.append(
                {"entity_type": "cluster", "raw_value": match.group(0), "normalized_value": chapter_map[chapter], "confidence": 0.7}
            )

    for match in CHAPTER_PATTERN.finditer(text):
        chapter = match.group(1)
        if chapter in chapter_map and chapter_map[chapter] not in seen_clusters:
            seen_clusters.add(chapter_map[chapter])
            results.append(
                {"entity_type": "cluster", "raw_value": match.group(0), "normalized_value": chapter_map[chapter], "confidence": 0.7}
            )

    lowered = text.lower()
    for keyword, cluster_id in _cluster_keyword_map().items():
        if keyword in lowered and cluster_id not in seen_clusters:
            seen_clusters.add(cluster_id)
            results.append(
                {"entity_type": "cluster", "raw_value": keyword, "normalized_value": cluster_id, "confidence": 0.8}
            )
    return results


def extract_dates(text: str) -> list[dict]:
    results = []
    for pattern, entity_type in DATE_CONTEXT_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            parsed = _try_parse_date(raw)
            if parsed:
                results.append(
                    {"entity_type": entity_type, "raw_value": raw, "normalized_value": parsed, "confidence": 0.85}
                )
    return results


def extract_entities(text: str) -> list[dict]:
    """Runs every extractor over the given text (typically title + body) and
    returns a flat list of entity dicts, deduped within each extractor."""
    if not text:
        return []
    return (
        extract_countries(text)
        + extract_schemes_and_authorities(text)
        + extract_hs_and_clusters(text)
        + extract_dates(text)
    )


def persist_entities(conn, signal_id: str, entities: list[dict]) -> None:
    for entity in entities:
        conn.execute(
            """
            INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (signal_id, entity["entity_type"], entity["raw_value"], entity["normalized_value"], entity["confidence"]),
        )
