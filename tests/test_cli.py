import sqlite3

import pytest

from radar import cli, settings


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "radar.db")
    monkeypatch.setattr(settings, "DESK_SHEETS_DIR", tmp_path)
    monkeypatch.setattr(settings, "RUNS_DIR", tmp_path)
    return tmp_path


def _db(env):
    c = sqlite3.connect(env / "radar.db")
    c.row_factory = sqlite3.Row
    return c


def test_full_human_gated_walkthrough(env, capsys):
    assert cli.main(["run", "morning", "--sample", "--date", "2026-09-14"]) == 0
    db = _db(env)
    lead = db.execute("SELECT id FROM signals WHERE decision = 'lead' ORDER BY score_final DESC").fetchone()["id"]

    assert cli.main(["why", lead]) == 0
    why = capsys.readouterr().out
    assert "GATES:" in why and "INDIAN EXPOSURE" in why and "RAW ITEMS IN THIS SIGNAL" in why

    assert cli.main(["verify", lead]) == 0
    claim_id = db.execute("SELECT id FROM claims WHERE signal_id = ? ORDER BY id", (lead,)).fetchone()["id"]

    # guardrail: verified without a quote is refused (exit 2), not silently accepted
    assert cli.main(["claim-mark", str(claim_id), "verified", "--type", "fact"]) == 2
    assert cli.main(["claim-mark", str(claim_id), "verified", "--type", "fact",
                     "--quote", "the existing RoDTEP rates ... are extended till 30.09.2026"]) == 0
    assert "Rescored" in capsys.readouterr().out

    assert cli.main(["angle-add", lead, "--franchise", "Countdown", "--pattern", "timing_transition_risk",
                     "--label", "Price October quotes as if RoDTEP is zero",
                     "--text", "The extension ends 30 September; nothing covers October shipments yet."]) == 0
    analysis_id = db.execute("SELECT id FROM analyses WHERE signal_id = ?", (lead,)).fetchone()["id"]
    assert cli.main(["angle-select", str(analysis_id)]) == 0

    assert cli.main(["draft", lead, "--channel", "linkedin"]) == 0
    assert "Draft brief" in capsys.readouterr().out
    draft_id = db.execute("SELECT id FROM content_drafts WHERE signal_id = ?", (lead,)).fetchone()["id"]
    assert cli.main(["draft-lint", str(draft_id)]) == 1  # scaffold placeholders must block approval
    assert cli.main(["draft-status", str(draft_id), "approved", "--by", "founder", "--external-check", "CA"]) == 2

    assert cli.main(["call-add", "--text", "October shipments will price without RoDTEP", "--reasoning", "no notification",
                     "--source", "DGFT 74/2025-26", "--review-on", "2026-10-15", "--signal-id", lead]) == 0
    assert db.execute("SELECT COUNT(*) FROM calls_ledger").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM published_content").fetchone()[0] == 0  # nothing published


def test_schedule_is_a_dry_run_by_default(env, capsys, monkeypatch):
    import subprocess

    def refuse(*a, **k):
        raise AssertionError("schedule must not touch Task Scheduler without --install")

    monkeypatch.setattr(subprocess, "run", refuse)
    assert cli.main(["schedule"]) == 0
    out = capsys.readouterr().out
    assert out.count("schtasks /Create") == 4
    assert "/ST 05:30" in out and "/ST 06:30" in out and "/ST 21:00" in out
    assert "Dry run" in out


def test_status_rejection_requires_reason_code(env, capsys):
    cli.main(["run", "morning", "--sample", "--date", "2026-09-14"])
    sid = _db(env).execute("SELECT id FROM signals WHERE status = 'NEEDS_RESEARCH' LIMIT 1").fetchone()["id"]
    assert cli.main(["status", sid, "REJECTED", "--by", "founder"]) == 2
    assert cli.main(["status", sid, "REJECTED", "--by", "founder", "--reason", "Too generic"]) == 0


def test_queue_dispatch_live_still_refused_regardless_of_credentials(env, capsys, monkeypatch):
    # No credentials configured: dry run must not error, and --live must
    # still be refused by the auto_publish gate (exit 2), unaffected by
    # Phase 4's publisher wiring.
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    assert cli.main(["queue-dispatch"]) == 0
    assert "Nothing is due" in capsys.readouterr().out
    assert cli.main(["queue-dispatch", "--live"]) == 2
    assert "auto_publish" in capsys.readouterr().err

    # Fake credentials configured: _publishers() must build a real
    # LinkedInPublisher without error, and --live must STILL be refused --
    # having credentials never bypasses the auto_publish gate.
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:1")
    assert cli.main(["queue-dispatch"]) == 0
    assert "Nothing is due" in capsys.readouterr().out
    assert cli.main(["queue-dispatch", "--live"]) == 2
    assert "auto_publish" in capsys.readouterr().err
