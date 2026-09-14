from radar.pipeline.entities import (
    extract_countries,
    extract_dates,
    extract_entities,
    extract_hs_and_clusters,
    extract_schemes_and_authorities,
)


def test_extract_countries_normalizes_aliases():
    for text in ["U.S. tariffs rise", "US tariffs rise", "United States tariffs rise"]:
        entities = extract_countries(text)
        values = {e["normalized_value"] for e in entities}
        assert "United States" in values


def test_extract_countries_finds_multiple():
    entities = extract_countries("India and the United Kingdom signed CETA")
    values = {e["normalized_value"] for e in entities}
    assert values == {"India", "United Kingdom"}


def test_extract_countries_no_false_positive_on_unrelated_text():
    assert extract_countries("The quick brown fox jumps") == []


def test_extract_schemes_and_authorities_recognises_rodtep_and_dgft():
    entities = extract_schemes_and_authorities("DGFT extends RoDTEP rates")
    values = {(e["entity_type"], e["normalized_value"]) for e in entities}
    assert ("authority", "DGFT") in values
    assert ("scheme", "RoDTEP") in values


def test_extract_schemes_deduplicates_repeated_mentions():
    entities = extract_schemes_and_authorities("RoDTEP rates, RoDTEP scrips, RoDTEP extension")
    rodtep_hits = [e for e in entities if e["normalized_value"] == "RoDTEP"]
    assert len(rodtep_hits) == 1


def test_extract_hs_and_clusters_maps_chapter_to_cluster():
    entities = extract_hs_and_clusters("New rule affects HS 6109 knitwear exports")
    types = {(e["entity_type"], e["normalized_value"]) for e in entities}
    assert ("hs_code", "6109") in types
    assert ("cluster", "tirupur") in types  # HS chapter 61 -> Tirupur


def test_extract_hs_and_clusters_matches_product_keyword_without_hs_code():
    entities = extract_hs_and_clusters("Turmeric exporters face new residue limits")
    values = {e["normalized_value"] for e in entities}
    assert "erode" in values


def test_extract_dates_effective_from():
    entities = extract_dates("The regulation is effective from 1 October 2026 nationwide")
    matches = [e for e in entities if e["entity_type"] == "effective_date"]
    assert matches
    assert matches[0]["normalized_value"] == "2026-10-01"


def test_extract_dates_deadline_extended_to():
    entities = extract_dates("RoDTEP rates extended to 30 September 2026 for exporters")
    matches = [e for e in entities if e["entity_type"] == "deadline"]
    assert matches
    assert matches[0]["normalized_value"] == "2026-09-30"


def test_extract_entities_combines_all_extractors():
    text = "DGFT notified RoDTEP rate extension for India effective from 1 October 2026, HS 6109"
    entities = extract_entities(text)
    types_present = {e["entity_type"] for e in entities}
    assert {"country", "authority", "scheme", "effective_date", "hs_code"}.issubset(types_present)


def test_extract_entities_empty_text_returns_empty_list():
    assert extract_entities("") == []
