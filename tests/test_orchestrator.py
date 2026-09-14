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


def test_run_pipeline_classifies_incentive_signal_correctly(conn):
    summary = _run_full_sample_pipeline(conn)
    categories = {r["category"] for r in summary["results"]}
    assert "incentives" in categories
