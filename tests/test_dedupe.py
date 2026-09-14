from radar.pipeline.dedupe import (
    cluster_raw_items,
    dedupe_and_cluster_new_items,
    title_similarity,
)
from radar.collectors import registry
from radar.collectors.sample_data import load_sample_raw_items
from radar.pipeline.normalize import canonicalize_url, compute_content_hash


def test_title_similarity_identical_titles_is_one():
    assert title_similarity("RoDTEP rates extended", "RoDTEP rates extended") == 1.0


def test_title_similarity_unrelated_titles_is_low():
    assert title_similarity("RoDTEP rates extended to September", "Hormuz freight rates spike") < 0.2


def test_title_similarity_empty_title_is_zero():
    assert title_similarity("", "RoDTEP rates extended") == 0.0


def _insert(conn, source_id, title, url, body_text="", published_at=None):
    canonical = canonicalize_url(url)
    conn.execute(
        """
        INSERT INTO raw_items (source_id, url, canonical_url, title, body_text,
                                published_at, collected_at, content_hash, status)
        VALUES (?, ?, ?, ?, ?, ?, '2026-09-14T06:00:00', ?, 'NEW')
        """,
        (source_id, url, canonical, title, body_text, published_at, compute_content_hash(canonical, title)),
    )


def test_cluster_raw_items_groups_near_duplicate_titles():
    items = [
        {"title": "DGFT extends RoDTEP rates to 30 September 2026", "body_text": "", "published_at": "2026-08-15"},
        {"title": "Govt extends RoDTEP RoSCTL rates till September 30 2026", "body_text": "", "published_at": "2026-08-15"},
        {"title": "Strait of Hormuz shipping disruption raises freight costs", "body_text": "", "published_at": "2026-08-16"},
    ]
    clusters = cluster_raw_items(items)
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def test_cluster_raw_items_respects_date_window():
    items = [
        {"title": "RoDTEP rate extension announced", "body_text": "", "published_at": "2026-01-01"},
        {"title": "RoDTEP rate extension announced", "body_text": "", "published_at": "2026-09-01"},
    ]
    clusters = cluster_raw_items(items)
    assert len(clusters) == 2  # same title, but 8 months apart -> different events


def test_dedupe_and_cluster_new_items_creates_signals_and_links_raw_items(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    run_row = conn.execute(
        "INSERT INTO system_runs (run_type, started_at) VALUES ('manual', '2026-09-14T00:00:00')"
    )
    conn.commit()
    registry.run_collection(conn, run_row.lastrowid, use_sample=True)

    signal_ids = dedupe_and_cluster_new_items(conn, "2026-09-14T06:30:00")

    # 10 sample raw items about 5 underlying stories should cluster down.
    assert 1 <= len(signal_ids) < 10
    remaining_new = conn.execute("SELECT COUNT(*) AS n FROM raw_items WHERE status = 'NEW'").fetchone()["n"]
    assert remaining_new == 0
    clustered = conn.execute("SELECT COUNT(*) AS n FROM raw_items WHERE status = 'CLUSTERED'").fetchone()["n"]
    assert clustered == 10

    rodtep_signal = conn.execute(
        "SELECT signal_id, COUNT(*) AS n FROM raw_items "
        "WHERE title LIKE '%RoDTEP%' GROUP BY signal_id ORDER BY n DESC LIMIT 1"
    ).fetchone()
    assert rodtep_signal["n"] >= 2  # the four RoDTEP/RoSCTL articles collapse into one signal


def test_templated_federal_register_titles_for_different_cases_stay_apart():
    # Live run 14 Sep 2026: these merged into one "signal" before the product veto.
    items = [
        {"title": "Raw Honey From Brazil: Preliminary Results of Antidumping Duty Administrative Review; 2024-2025", "published_at": "2026-09-14"},
        {"title": "Certain Frozen Warmwater Shrimp from India: Final Results of Antidumping Duty Administrative Review; 2024-2025", "published_at": "2026-09-14"},
        {"title": "Polyethylene Terephthalate Resin From the Sultanate of Oman: Amended Final Results of Antidumping Duty Administrative Review", "published_at": "2026-09-14"},
        {"title": "Glycine From India: Preliminary Results and Rescission, in Part, of Countervailing Duty Administrative Review", "published_at": "2026-09-14"},
        {"title": "Glycine From Japan: Preliminary Results and Rescission, in Part, of Antidumping Duty Administrative Review", "published_at": "2026-09-14"},
    ]
    assert len(cluster_raw_items(items)) == 5


def test_one_trade_remedy_case_seen_by_commerce_and_the_itc_is_one_signal():
    items = [
        {"title": "Certain Linear Hydraulic Cylinders and Parts Thereof From the People's Republic of China, India, and Mexico: Initiation of Countervailing Duty Investigations", "published_at": "2026-09-14"},
        {"title": "Certain Linear Hydraulic Cylinders and Parts Thereof From Canada, the People's Republic of China, India, the Republic of Korea, and Mexico: Initiation of Less-Than-Fair-Value Investigations", "published_at": "2026-09-14"},
        {"title": "Linear Hydraulic Cylinders From Canada, China, India, Mexico, and South Korea; Revised Schedule for the Subject Investigations", "published_at": "2026-09-12"},
    ]
    assert len(cluster_raw_items(items)) == 1


def test_wheat_and_wheat_flour_policy_moves_same_day_stay_together():
    items = [
        {"title": "DGFT Notification 34/2026-27: Amendment in the Export Policy of Wheat Flour and related products - reg.", "published_at": "2026-08-24"},
        {"title": "DGFT Notification 35/2026-27: Amendment in the Export Policy of Wheat - reg.", "published_at": "2026-08-24"},
    ]
    assert len(cluster_raw_items(items)) == 1


def test_dedupe_and_cluster_is_a_noop_on_empty_raw_items(conn):
    assert dedupe_and_cluster_new_items(conn, "2026-09-14T06:30:00") == []
