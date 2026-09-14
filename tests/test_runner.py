import json
from datetime import date

import pytest

from radar import runner, settings
from radar.collectors import registry


@pytest.fixture(autouse=True)
def _isolated_output_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DESK_SHEETS_DIR", tmp_path / "desk")
    monkeypatch.setattr(settings, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "desk").mkdir()
    (tmp_path / "runs").mkdir()


def test_morning_run_logs_everything_section_29_asks_for(conn):
    row = runner.run(conn, "morning", use_sample=True, reference_date=date(2026, 9, 14))
    assert row["status"] == "completed"
    assert row["ended_at"] is not None
    assert row["sources_checked"] > 0
    assert row["items_collected"] == 10
    assert row["high_score_signals"] >= 1
    assert row["model_calls"] == 0
    assert row["desk_sheet_id"].endswith("2026-09-14.md")
    assert json.loads(row["errors_json"]) == []
    assert (settings.RUNS_DIR / f"run_{row['id']:05d}.json").exists()


def test_morning_run_moves_candidates_to_research_with_claims(conn):
    runner.run(conn, "morning", use_sample=True, reference_date=date(2026, 9, 14))
    researching = conn.execute("SELECT id FROM signals WHERE status = 'NEEDS_RESEARCH'").fetchall()
    assert researching
    for r in researching:
        assert conn.execute("SELECT COUNT(*) AS n FROM claims WHERE signal_id = ?", (r["id"],)).fetchone()["n"] > 0
    # every automated move is in the audit trail
    assert conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE reviewer = 'system'").fetchone()["n"] == len(researching)


def test_overnight_run_collects_without_scoring(conn):
    row = runner.run(conn, "overnight", use_sample=True)
    assert row["items_collected"] == 10
    assert row["desk_sheet_id"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0


def test_evening_sheet_does_not_overwrite_morning_sheet(conn):
    morning = runner.run(conn, "morning", use_sample=True, reference_date=date(2026, 9, 14))
    evening = runner.run(conn, "evening", use_sample=True, reference_date=date(2026, 9, 14))
    assert morning["desk_sheet_id"] != evening["desk_sheet_id"]
    assert evening["desk_sheet_id"].endswith("-evening.md")


def test_run_survives_every_source_failing(conn, monkeypatch):
    from radar.collectors.base import Collector, CollectorError

    class Dead(Collector):
        def collect(self, source):
            raise CollectorError(f"{source['id']}: down")

    for method in ("rss", "api", "pagewatch"):
        monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, method, Dead())

    row = runner.run(conn, "morning", use_sample=False, reference_date=date(2026, 9, 14))
    assert row["status"] == "completed"  # degraded, not crashed
    assert len(json.loads(row["errors_json"])) == row["sources_checked"]
    sheet = open(row["desk_sheet_id"], encoding="utf-8").read()
    assert "No signal cleared the gates today" in sheet
    assert "failed sources:" in sheet


def test_unexpected_crash_is_recorded_not_raised(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(runner, "run_pipeline", boom)
    row = runner.run(conn, "morning", use_sample=True)
    assert row["status"] == "failed"
    assert "disk full" in row["errors_json"]


def test_unknown_mode_rejected(conn):
    with pytest.raises(ValueError):
        runner.run(conn, "lunchtime")
