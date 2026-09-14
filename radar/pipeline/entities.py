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


_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_LITERAL = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+\d{{4}}\b"      # 30 September 2026
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"      # September 30, 2026
    r"|\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b"                          # 30.09.2026 (day first, Indian usage)
    r"|\b\d{4}-\d{2}-\d{2}\b",                                     # 2026-09-30
    re.I,
)
# A typed date is the first date literal within DATE_TRIGGER_WINDOW characters
# *after* its trigger phrase — "RoDTEP rates are extended till 30.09.2026".
DATE_TRIGGER_WINDOW = 100
DATE_TRIGGERS = [
    (re.compile(r"\beffective(?:\s+from)?\b|\bw\.e\.f\b|\bwith effect from\b|\bcom(?:e|es|ing) into (?:effect|force)\b"
                r"|\bin force from\b|\btakes? effect\b|\benters? into force\b|\bapplicable from\b", re.I), "effective_date"),
    (re.compile(r"\bextend(?:ed|s|ing)?\b|\bextension\b|\btill\b|\buntil\b|\bdeadline\b|\bvalid (?:up ?to|till)\b"
                r"|\blast date\b|\bends? on\b|\bexpir(?:es|y)\b", re.I), "deadline"),
    (re.compile(r"\bnotified on\b|\bdated\b|\bpublished on\b|\bissued on\b", re.I), "announcement_date"),
]
DOC_REF_PATTERNS = [
    (re.compile(r"\bNotification\s+(?:No\.?\s*)?(\d{1,3}/\d{4}-\d{2})", re.I), "Notification {}"),
    (re.compile(r"\bFederal Register doc (\d{4}-\d{4,6})\b", re.I), "Federal Register {}"),
    (re.compile(r"\bTrade Notice\s+(?:No\.?\s*)?(\d{1,3}/\d{4}-\d{2})", re.I), "Trade Notice {}"),
    (re.compile(r"\bPublic Notice\s+(?:No\.?\s*)?(\d{1,3}/\d{4}-\d{2})", re.I), "Public Notice {}"),
]
# Instrument -> cluster sensitivity key from config/clusters.yaml. A cluster is
# only tagged when the strategy's own cluster map names that sensitivity.
INSTRUMENT_SENSITIVITY = {
    "RoSCTL": "rosctl", "CBAM": "cbam_steel_content", "EUDR": "eudr", "RASFF": "rasff",
    "US FDA": "fda", "UK-India CETA": "uk_ceta",
}
HS_CODE_PATTERN = re.compile(r"\bHS\s*(?:code\s*)?(\d{4,8})\b", re.I)
CHAPTER_PATTERN = re.compile(r"\bCh(?:apter|\.)?\s*(\d{2})\b", re.I)


def _try_parse_date(text: str) -> str | None:
    text = text.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
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
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered) and (entity_type, normalized) not in seen:
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
        if re.search(rf"\b{re.escape(keyword)}\b", lowered) and cluster_id not in seen_clusters:
            seen_clusters.add(cluster_id)
            results.append(
                {"entity_type": "cluster", "raw_value": keyword, "normalized_value": cluster_id, "confidence": 0.8}
            )
    return results


def clusters_from_instruments(instruments: list[str], already: set[str]) -> list[dict]:
    """Links an instrument (e.g. RoSCTL) to the clusters whose sensitivities in
    config/clusters.yaml name it. Lower confidence than a product/HS match."""
    results = []
    for instrument in instruments:
        key = INSTRUMENT_SENSITIVITY.get(instrument)
        if not key:
            continue
        for cluster in settings.clusters():
            if key in cluster.get("sensitivities", []) and cluster["id"] not in already:
                already.add(cluster["id"])
                results.append({
                    "entity_type": "cluster", "raw_value": f"{instrument} (cluster sensitivity map)",
                    "normalized_value": cluster["id"], "confidence": 0.6,
                })
    return results


def extract_dates(text: str) -> list[dict]:
    """Every date literal becomes a generic 'date' entity; a literal that follows
    an effective/deadline/announcement trigger within the window is also typed."""
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(entity_type: str, raw: str, confidence: float) -> None:
        parsed = _try_parse_date(raw)
        if parsed and (entity_type, parsed) not in seen:
            seen.add((entity_type, parsed))
            results.append({"entity_type": entity_type, "raw_value": raw, "normalized_value": parsed, "confidence": confidence})

    for trigger, entity_type in DATE_TRIGGERS:
        for match in trigger.finditer(text):
            window = text[match.end(): match.end() + DATE_TRIGGER_WINDOW]
            literal = DATE_LITERAL.search(window)
            if literal:
                add(entity_type, literal.group(0), 0.85)
    for literal in DATE_LITERAL.finditer(text):
        add("date", literal.group(0), 0.9)
    return results


def extract_doc_refs(text: str) -> list[dict]:
    """Named instruments (Section 6): notification numbers and Federal Register
    document numbers are the strongest dedup key there is."""
    results, seen = [], set()
    for pattern, template in DOC_REF_PATTERNS:
        for match in pattern.finditer(text):
            value = template.format(match.group(1))
            if value not in seen:
                seen.add(value)
                results.append({"entity_type": "regulation", "raw_value": match.group(0), "normalized_value": value, "confidence": 1.0})
    return results


def extract_entities(text: str) -> list[dict]:
    """Runs every extractor over the given text (typically title + body) and
    returns a flat list of entity dicts, deduped within each extractor."""
    if not text:
        return []
    instruments = extract_schemes_and_authorities(text)
    hs_and_clusters = extract_hs_and_clusters(text)
    tagged = {e["normalized_value"] for e in hs_and_clusters if e["entity_type"] == "cluster"}
    sensitivity_clusters = clusters_from_instruments([e["normalized_value"] for e in instruments], tagged)
    return (
        extract_countries(text)
        + instruments
        + extract_doc_refs(text)
        + hs_and_clusters
        + sensitivity_clusters
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
