from radar.pipeline.saturation import compute_saturation, count_matching_secondary_items_72h, saturation_band_score


def test_saturation_band_score_under_covered():
    score, label = saturation_band_score(0)
    assert score == 5
    assert label == "under-covered"


def test_saturation_band_score_lightly_covered():
    score, label = saturation_band_score(5)
    assert score == 3
    assert label == "lightly covered"


def test_saturation_band_score_heavily_saturated():
    score, label = saturation_band_score(20)
    assert score == 1
    assert label == "heavily saturated"


def _setup_signal_with_entities(conn, signal_id, created_at, entities):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, created_at, updated_at)
        VALUES (?, 'Test', ?, 'incentives', 'TRIAGED', ?, ?)
        """,
        (signal_id, created_at, created_at, created_at),
    )
    for entity_type, value in entities:
        conn.execute(
            "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value, confidence) "
            "VALUES (?, ?, ?, ?, 1.0)",
            (signal_id, entity_type, value, value),
        )
    conn.commit()


def _add_raw_item(conn, source_id, signal_id, collected_at):
    conn.execute(
        """
        INSERT INTO raw_items (source_id, url, canonical_url, title, collected_at, content_hash, signal_id, status)
        VALUES (?, 'https://example.com/x', 'https://example.com/x', 'x', ?, ?, ?, 'CLUSTERED')
        """,
        (source_id, collected_at, f"hash-{source_id}-{signal_id}-{collected_at}", signal_id),
    )
    conn.commit()


def test_count_matching_secondary_items_is_zero_with_no_shared_entities(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    _setup_signal_with_entities(conn, "SIG-A", "2026-09-14T06:00:00", [("scheme", "RoDTEP")])
    assert count_matching_secondary_items_72h(conn, "SIG-A") == 0


def test_count_matching_secondary_items_counts_shared_scheme_within_window(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    _setup_signal_with_entities(conn, "SIG-A", "2026-09-14T06:00:00", [("scheme", "RoDTEP")])
    _setup_signal_with_entities(conn, "SIG-B", "2026-09-14T05:00:00", [("scheme", "RoDTEP")])
    _add_raw_item(conn, "business_standard_economy", "SIG-B", "2026-09-14T05:00:00")

    assert count_matching_secondary_items_72h(conn, "SIG-A") == 1


def test_count_matching_secondary_items_ignores_items_outside_72h_window(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    _setup_signal_with_entities(conn, "SIG-A", "2026-09-14T06:00:00", [("scheme", "RoDTEP")])
    _setup_signal_with_entities(conn, "SIG-B", "2026-09-01T05:00:00", [("scheme", "RoDTEP")])
    _add_raw_item(conn, "business_standard_economy", "SIG-B", "2026-09-01T05:00:00")

    assert count_matching_secondary_items_72h(conn, "SIG-A") == 0


def test_count_matching_secondary_items_ignores_primary_sources(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    _setup_signal_with_entities(conn, "SIG-A", "2026-09-14T06:00:00", [("scheme", "RoDTEP")])
    _setup_signal_with_entities(conn, "SIG-B", "2026-09-14T05:30:00", [("scheme", "RoDTEP")])
    _add_raw_item(conn, "dgft_notifications", "SIG-B", "2026-09-14T05:30:00")  # primary source

    assert count_matching_secondary_items_72h(conn, "SIG-A") == 0


def test_compute_saturation_returns_full_dict(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    _setup_signal_with_entities(conn, "SIG-A", "2026-09-14T06:00:00", [("scheme", "RoDTEP")])
    result = compute_saturation(conn, "SIG-A")
    assert result == {"count": 0, "score": 5, "label": "under-covered"}
