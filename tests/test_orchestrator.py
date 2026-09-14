from radar.collectors import registry
from radar.db.connection import seed_sources
from radar.pipeline.orchestrator import run_pipeline


def _run_full_sample_pipeline(conn):
    seed_sources(conn)
    run_row = conn.execute(
        "INSERT INTO system_runs (run_type, started_at) VALUES ('manual', '2026-09-14T05:30:00')"
    )
    conn.commit()
    registry.run_collection(conn, run_row.lastrowid, use_sample=True)
    return run_pipeline(conn, "2026-09-14T06:30:00")


def test_run_pipeline_scores_every_new_signal(conn):
    summary = _run_full_sample_pipeline(conn)
    assert summary["processed"] == len(summary["new_signals"])
    assert summary["processed"] > 0

    unscored = conn.execute(
        "SELECT COUNT(*) AS n FROM signals WHERE score_final IS NULL"
    ).fetchone()["n"]
    assert unscored == 0


def test_run_pipeline_produces_at_least_one_lead_or_secondary_signal(conn):
    summary = _run_full_sample_pipeline(conn)
    # RoDTEP/RoSCTL extension (DGFT primary source, India-specific scheme,
    # near-term deadline) should score as a strong candidate.
    assert summary["high_score_signals"] >= 1


def test_run_pipeline_gives_primary_sourced_signal_higher_evidence_quality(conn):
    summary = _run_full_sample_pipeline(conn)
    rodtep_result = next(
        r for r in summary["results"]
        if "RoDTEP" in [e["normalized_value"] for e in
                        conn.execute("SELECT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type='scheme'",
                                     (r["signal_id"],)).fetchall()]
    )
    assert rodtep_result["score"]["criteria"]["evidence_quality"] == 5.0


def test_run_pipeline_is_idempotent_when_rerun_with_no_new_raw_items(conn):
    first = _run_full_sample_pipeline(conn)
    second = run_pipeline(conn, "2026-09-14T07:00:00")
    assert second["new_signals"] == []
    assert second["processed"] == 0  # nothing left in TRIAGED after the first run scored everything


def test_specs_fema_example_secondary_score_rises_once_primary_text_is_verified(conn):
    """daily_research_system.md worked example: 'Score: 74 while resting on
    secondary sources; 87 once the RBI text is verified.' Law-firm articles in,
    x0.85; analyst verifies against the RBI notification, x1.0."""
    from radar.collectors.registry import insert_raw_item
    from radar.collectors.base import RawItem
    from radar.pipeline.orchestrator import rescore_signal
    from radar.pipeline.verify import add_claim

    seed_sources(conn)
    body = ("Law firms say the FEMA (Export and Import of Goods and Services) Regulations, 2026 take effect "
            "from 1 October 2026, with an export realisation period of 15 months.")
    insert_raw_item(conn, RawItem("business_standard_economy", "FEMA 2026 export rules from 1 October 2026",
                                  "https://lawfirm-a.example/fema-2026", body, "2026-09-12"))
    insert_raw_item(conn, RawItem("et_foreign_trade", "New FEMA export regime starts 1 October 2026",
                                  "https://lawfirm-b.example/fema", body, "2026-09-12"))
    conn.commit()
    from datetime import date as _date

    summary = run_pipeline(conn, "2026-09-14T06:30:00", reference_date=_date(2026, 9, 14))
    assert len(summary["results"]) == 1  # two articles, one signal
    before = summary["results"][0]["score"]
    assert before["confidence_mult"] == 0.85
    sid = summary["results"][0]["signal_id"]

    add_claim(conn, sid, "FEMA 2026 regulations effective 1 October 2026", "fact",
              "https://www.rbi.org.in/fema-2026-notification", "primary", "2026-09-14T08:30:00",
              source_quote="These Regulations shall come into force on October 1, 2026", verified_by="analyst")
    after = rescore_signal(conn, sid, "2026-09-14T08:31:00", reference_date=_date(2026, 9, 14))["score"]

    assert after["confidence_mult"] == 1.0
    assert after["final_score"] > before["final_score"]


def test_rescore_does_not_move_a_signal_a_human_advanced(conn):
    from radar.pipeline.orchestrator import rescore_signal

    _run_full_sample_pipeline(conn)
    sid = conn.execute("SELECT id FROM signals WHERE decision = 'lead' LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE signals SET status = 'READY_FOR_REVIEW' WHERE id = ?", (sid,))
    conn.commit()
    rescore_signal(conn, sid, "2026-09-14T09:00:00")
    assert conn.execute("SELECT status FROM signals WHERE id = ?", (sid,)).fetchone()["status"] == "READY_FOR_REVIEW"


def test_two_items_from_one_publisher_are_not_two_independent_sources(conn):
    from radar.pipeline.orchestrator import _signal_text_and_sources

    seed_sources(conn)
    conn.execute(
        "INSERT INTO signals (id, title, first_seen_at, status, created_at, updated_at) VALUES ('S', 't', 'x', 'TRIAGED', 'x', 'x')"
    )
    for i, src in enumerate(["business_standard_economy", "google_news_india_tariffs"]):
        conn.execute(
            "INSERT INTO raw_items (source_id, url, canonical_url, title, collected_at, content_hash, signal_id, status) "
            "VALUES (?, ?, ?, 't', 'x', ?, 'S', 'CLUSTERED')",
            (src, f"https://business-standard.com/a{i}", f"https://business-standard.com/a{i}", f"h{i}"),
        )
    conn.commit()
    _, has_primary, independent = _signal_text_and_sources(conn, "S")
    assert has_primary is False
    assert independent == 1


def test_run_pipeline_classifies_incentive_signal_correctly(conn):
    summary = _run_full_sample_pipeline(conn)
    categories = {r["category"] for r in summary["results"]}
    assert "incentives" in categories
