import json
from datetime import date

import pytest

from radar import runner, settings
from radar.collectors import registry
from radar.content.drafts import ASSET_SLOTS, create_content_bundle
from radar.content.generation import VISUAL_SPLIT
from radar.pipeline.angles import save_angle, select_angle
from radar.pipeline.verify import add_claim


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

    for method in ("rss", "api", "pagewatch", "cbic_api", "email_imap"):
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


NOW_CONTENT = "2026-09-15T19:00:00"
CONTENT_DISCLOSURE_FREE_CATEGORY = "logistics"


def _seed_ready_signal(conn, signal_id="SIG-READY-1", with_claim=True, select=True):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, primary_source_url,
                              created_at, updated_at)
        VALUES (?, 'Container detention notice', ?, ?, 'SCORED', 'https://dgft.gov.in/n99', ?, ?)
        """,
        (signal_id, NOW_CONTENT, CONTENT_DISCLOSURE_FREE_CATEGORY, NOW_CONTENT, NOW_CONTENT),
    )
    conn.commit()
    if with_claim:
        add_claim(
            conn, signal_id, "Detention-free period extended to 10 days", "fact",
            "https://dgft.gov.in/n99", "primary", NOW_CONTENT,
            source_quote="the detention-free period is extended to 10 days", verified_by="analyst",
        )
    aid = save_angle(
        conn, signal_id, "Countdown", "timing_transition_risk",
        "Ten days sounds generous until you count loading delays",
        "The extension helps only if the container actually moves within the window.",
        "claude_code", NOW_CONTENT,
    )
    if select:
        select_angle(conn, aid)
    return signal_id


def _fake_generate(system_prompt: str, user_prompt: str) -> str:
    body = (
        "The detention-free period is extended to 10 days, per the DGFT notice. "
        "This helps only if loading finishes inside that window. " * 8
    ).strip()
    return f"{body}\n\n{VISUAL_SPLIT}\nA simple countdown graphic showing the 10-day window."


def test_content_run_generates_bundles_for_ready_signals(conn):
    sid = _seed_ready_signal(conn)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    assert notes["candidates"] == 1
    assert notes["bundles_generated"] == 1
    assert notes["skipped"] == []
    assert row["model_calls"] == len(ASSET_SLOTS)
    drafts_for_signal = conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE signal_id = ?", (sid,)
    ).fetchone()["n"]
    # 8 scaffold rows (v1, status='draft') + 8 generated rows (v2, status='edited')
    # per save_draft_version's invariant: old versions are never deleted, they are the audit trail
    assert drafts_for_signal == len(ASSET_SLOTS) * 2
    # Core safety guarantee: nothing this orchestrator does ever auto-approves
    # or auto-queues content -- every generated asset still needs a human.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE status = 'approved'"
    ).fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM publish_queue").fetchone()["n"] == 0


def test_content_run_skips_signal_without_selected_angle(conn):
    _seed_ready_signal(conn, select=False)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    # not selected -> status never moved to READY_FOR_REVIEW -> not even a candidate
    assert notes["candidates"] == 0
    assert notes["bundles_generated"] == 0


def test_content_run_skips_signal_without_verified_claims_and_records_why(conn):
    sid = _seed_ready_signal(conn, with_claim=False)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    assert notes["candidates"] == 1
    assert notes["bundles_generated"] == 0
    assert len(notes["skipped"]) == 1
    assert notes["skipped"][0]["signal_id"] == sid
    assert "no verified claims" in notes["skipped"][0]["reason"]


def test_content_run_does_not_regenerate_an_existing_bundle(conn):
    sid = _seed_ready_signal(conn)
    first = runner.run(conn, "content", generate_fn=_fake_generate)
    assert json.loads(first["notes"])["bundles_generated"] == 1
    second = runner.run(conn, "content", generate_fn=_fake_generate)
    notes = json.loads(second["notes"])
    assert notes["candidates"] == 0
    assert notes["bundles_generated"] == 0
    drafts_for_signal = conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE signal_id = ?", (sid,)
    ).fetchone()["n"]
    # still 16 rows (8 scaffold v1 + 8 generated v2), no new rows from second run
    # because the idempotency guard (NOT IN content_drafts) prevents re-generation
    assert drafts_for_signal == len(ASSET_SLOTS) * 2


def test_content_run_retries_a_signal_once_claims_are_verified(conn):
    sid = _seed_ready_signal(conn, with_claim=False)

    first = runner.run(conn, "content", generate_fn=_fake_generate)
    first_notes = json.loads(first["notes"])
    assert first_notes["candidates"] == 1
    assert first_notes["bundles_generated"] == 0
    assert len(first_notes["skipped"]) == 1
    # No scaffold rows were ever created for this signal, so it remains a
    # valid candidate on a later run (the fix for the "permanently skipped"
    # bug: the old code called create_content_bundle, which commits its 8
    # scaffold rows, before discovering there were no verified claims).
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE signal_id = ?", (sid,)
    ).fetchone()["n"] == 0

    add_claim(
        conn, sid, "Detention-free period extended to 10 days", "fact",
        "https://dgft.gov.in/n99", "primary", NOW_CONTENT,
        source_quote="the detention-free period is extended to 10 days", verified_by="analyst",
    )

    second = runner.run(conn, "content", generate_fn=_fake_generate)
    second_notes = json.loads(second["notes"])
    assert second_notes["candidates"] == 1
    assert second_notes["bundles_generated"] == 1


def test_content_run_records_partial_progress_when_a_later_signal_crashes(conn):
    sid_1 = _seed_ready_signal(conn, signal_id="SIG-READY-1")
    sid_2 = _seed_ready_signal(conn, signal_id="SIG-READY-2")

    calls = {"n": 0}

    def flaky_generate(system_prompt: str, user_prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] > len(ASSET_SLOTS):
            raise RuntimeError("simulated API failure")
        return _fake_generate(system_prompt, user_prompt)

    row = runner.run(conn, "content", generate_fn=flaky_generate)
    assert row["status"] == "failed"
    assert "simulated API failure" in row["errors_json"]
    notes = json.loads(row["notes"])
    assert notes["bundles_generated"] == 1
    assert notes["candidates"] == 2
