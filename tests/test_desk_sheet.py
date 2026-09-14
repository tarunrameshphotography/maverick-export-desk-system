from datetime import date

from radar.collectors import registry
from radar.db.connection import seed_sources
from radar.desk.desk_sheet import (
    assemble_desk_sheet,
    get_lead_and_backups,
    get_upcoming_deadlines,
    get_watchlist,
    render_desk_sheet_markdown,
    write_desk_sheet,
)
from radar.pipeline.orchestrator import run_pipeline


def _seeded_conn(conn):
    seed_sources(conn)
    run_row = conn.execute(
        "INSERT INTO system_runs (run_type, started_at) VALUES ('manual', '2026-09-14T05:30:00')"
    )
    conn.commit()
    registry.run_collection(conn, run_row.lastrowid, use_sample=True)
    run_pipeline(conn, "2026-09-14T06:30:00")
    return conn


def test_get_lead_and_backups_orders_by_score(conn):
    _seeded_conn(conn)
    lead, backups = get_lead_and_backups(conn)
    assert lead is not None
    all_scores = [lead["score_final"]] + [b["score_final"] for b in backups]
    assert all_scores == sorted(all_scores, reverse=True)


def test_get_lead_and_backups_returns_none_when_nothing_qualifies(conn):
    lead, backups = get_lead_and_backups(conn)
    assert lead is None
    assert backups == []


def test_get_watchlist_only_returns_watchlist_decisions(conn):
    _seeded_conn(conn)
    watchlist = get_watchlist(conn)
    assert all(s["decision"] == "watchlist" for s in watchlist)


def test_get_upcoming_deadlines_within_window(conn):
    _seeded_conn(conn)
    deadlines = get_upcoming_deadlines(conn, reference_date=date(2026, 9, 14), window_days=30)
    dated = {d["date"] for d in deadlines}
    assert "2026-09-30" in dated  # RoDTEP/RoSCTL extension deadline from the sample fixture


def test_deadlines_from_discarded_signals_are_not_listed(conn):
    # Live run: an RBI bank-specific direction's date appeared as an "exporter deadline".
    conn.execute(
        "INSERT INTO signals (id, title, first_seen_at, status, decision, created_at, updated_at) "
        "VALUES ('SIG-X', 'Bank direction', 'x', 'REJECTED', 'discard', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value) "
        "VALUES ('SIG-X', 'deadline', '16 Sep 2026', '2026-09-16')"
    )
    conn.commit()
    assert get_upcoming_deadlines(conn, reference_date=date(2026, 9, 14)) == []


def test_get_upcoming_deadlines_excludes_out_of_window_dates(conn):
    _seeded_conn(conn)
    deadlines = get_upcoming_deadlines(conn, reference_date=date(2026, 1, 1), window_days=7)
    assert deadlines == []


def test_assemble_desk_sheet_has_all_seven_sections(conn):
    _seeded_conn(conn)
    data = assemble_desk_sheet(conn, reference_date=date(2026, 9, 14))
    for key in ("lead", "backups", "watchlist", "verification_blockers", "upcoming_deadlines", "content_memory_warnings"):
        assert key in data
    assert data["lead"] is not None


def test_render_desk_sheet_markdown_has_all_section_headers(conn):
    _seeded_conn(conn)
    data = assemble_desk_sheet(conn, reference_date=date(2026, 9, 14))
    markdown = render_desk_sheet_markdown(data)
    for header in ["## A. Top signal", "## B. Backup signals", "## C. Watchlist",
                   "## D. Verification blockers", "## E. Upcoming deadlines",
                   "## F. Content memory warnings", "## G. Suggested action"]:
        assert header in markdown


def test_lead_card_carries_every_section_16a_field(conn):
    _seeded_conn(conn)
    card = assemble_desk_sheet(conn, reference_date=date(2026, 9, 14))["lead_card"]
    for field in ("title", "why_it_matters", "countries", "products", "exposure", "sources",
                  "score", "angle", "franchise", "format", "coverage"):
        assert field in card
    assert "India" in card["countries"]
    assert card["sources"]  # at least the cluster's own sources are named
    markdown = render_desk_sheet_markdown(assemble_desk_sheet(conn, reference_date=date(2026, 9, 14)))
    assert "Why it matters:" in markdown and "Franchise / format:" in markdown


def test_rodtep_lead_is_flagged_for_disclosure_on_the_sheet(conn):
    _seeded_conn(conn)
    data = assemble_desk_sheet(conn, reference_date=date(2026, 9, 14))
    rodtep_cards = [c for c in [data["lead_card"], *data["backup_cards"]] if "RoDTEP" in c["instruments"]]
    assert rodtep_cards and all(c["disclosure"] for c in rodtep_cards)


def test_render_desk_sheet_handles_no_lead_gracefully(conn):
    data = assemble_desk_sheet(conn, reference_date=date(2026, 9, 14))
    markdown = render_desk_sheet_markdown(data)
    assert "No signal cleared the gates today" in markdown


def test_write_desk_sheet_creates_file(conn, tmp_path, monkeypatch):
    from radar import settings as radar_settings

    monkeypatch.setattr(radar_settings, "DESK_SHEETS_DIR", tmp_path)
    _seeded_conn(conn)
    data, path = write_desk_sheet(conn, reference_date=date(2026, 9, 14))
    from pathlib import Path

    assert Path(path).exists()
    assert Path(path).read_text(encoding="utf-8").startswith("# MAVERICK MORNING DESK")
