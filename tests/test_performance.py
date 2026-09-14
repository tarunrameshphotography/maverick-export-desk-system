import json

from radar.desk.performance import decide_learning, import_metrics_csv, propose_weight_changes, scored_outcomes

BASE_CRITERIA = {
    "exporter_impact": 3.0, "actionability": 3.0, "angle_strength": 3.0, "indian_exposure": 3.0,
    "under_coverage": 3.0, "time_sensitivity": 3.0, "explainability": 3.0, "audience_fit": 3.0,
    "evidence_quality": 3.0,
}


def _published_post(conn, n, criteria):
    sid = f"SIG-P-{n}"
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, score_breakdown_json, created_at, updated_at)
        VALUES (?, 't', '2026-09-01', 'tariffs', 'PUBLISHED', ?, '2026-09-01', '2026-09-01')
        """,
        (sid, json.dumps({"criteria": criteria})),
    )
    draft = conn.execute(
        "INSERT INTO content_drafts (signal_id, channel, status, created_at) VALUES (?, 'linkedin', 'approved', '2026-09-01')",
        (sid,),
    ).lastrowid
    conn.execute(
        "INSERT INTO published_content (draft_id, channel, published_at, url, status) VALUES (?, 'linkedin', '2026-09-02', ?, 'published')",
        (draft, f"https://linkedin.com/p/{n}"),
    )
    conn.commit()


def test_import_metrics_csv_matches_by_url_and_reports_unmatched(conn, tmp_path):
    _published_post(conn, 1, BASE_CRITERIA)
    path = tmp_path / "week.csv"
    path.write_text(
        "url,captured_at,impressions,qualified_comments,qualified_leads\n"
        "https://linkedin.com/p/1,2026-09-09,1200,4,1\n"
        "https://linkedin.com/p/missing,2026-09-09,50,0,0\n",
        encoding="utf-8",
    )
    result = import_metrics_csv(conn, path)
    assert result == {"imported": 1, "unmatched_urls": ["https://linkedin.com/p/missing"]}
    row = conn.execute("SELECT impressions, qualified_leads FROM performance").fetchone()
    assert (row["impressions"], row["qualified_leads"]) == (1200, 1)


def _perf(conn, n, leads, comments=0):
    pid = conn.execute("SELECT id FROM published_content WHERE url = ?", (f"https://linkedin.com/p/{n}",)).fetchone()["id"]
    conn.execute(
        "INSERT INTO performance (published_content_id, captured_at, qualified_leads, qualified_comments) VALUES (?, '2026-09-09', ?, ?)",
        (pid, leads, comments),
    )
    conn.commit()


def test_no_proposal_without_enough_data(conn):
    for n in range(3):
        _published_post(conn, n, BASE_CRITERIA)
        _perf(conn, n, leads=n)
    assert propose_weight_changes(conn, "2026-09") is None


def test_proposal_shifts_weight_toward_predictive_criterion(conn):
    # under_coverage tracks leads perfectly; time_sensitivity runs opposite.
    for n in range(10):
        criteria = {**BASE_CRITERIA, "under_coverage": float(n % 5), "time_sensitivity": float(4 - n % 5)}
        _published_post(conn, n, criteria)
        _perf(conn, n, leads=n % 5)

    learning_id = propose_weight_changes(conn, "2026-09")
    assert learning_id is not None
    row = conn.execute("SELECT * FROM learnings WHERE id = ?", (learning_id,)).fetchone()
    change = json.loads(row["weight_change_json"])
    assert change["under_coverage"] == 14  # 12 + 2
    assert change["time_sensitivity"] == 8  # 10 - 2
    assert row["accepted"] == 0  # proposals are never self-applied


def test_decide_learning_records_human_decision(conn):
    for n in range(10):
        _published_post(conn, n, {**BASE_CRITERIA, "under_coverage": float(n % 5), "time_sensitivity": float(4 - n % 5)})
        _perf(conn, n, leads=n % 5)
    lid = propose_weight_changes(conn, "2026-09")
    decide_learning(conn, lid, accept=False)
    assert conn.execute("SELECT accepted FROM learnings WHERE id = ?", (lid,)).fetchone()["accepted"] == -1


def test_scored_outcomes_uses_latest_capture(conn):
    _published_post(conn, 1, BASE_CRITERIA)
    _perf(conn, 1, leads=0, comments=1)
    _perf(conn, 1, leads=2, comments=5)
    assert scored_outcomes(conn)[0]["outcome"] == 25
