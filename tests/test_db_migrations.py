from radar.db import connection as db


def test_init_db_creates_all_core_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "sources", "raw_items", "signals", "signal_related", "signal_entities",
        "claims", "analyses", "content_drafts", "published_content",
        "competitor_observations", "calls_ledger", "approvals", "performance",
        "watchlist", "learnings", "system_runs", "source_failures", "id_sequences",
    }
    assert expected.issubset(tables)


def test_init_db_is_idempotent(conn):
    # Running init_db twice against the same connection must not raise.
    db.init_db(conn)
    db.init_db(conn)


def test_migrations_apply_once_and_are_recorded():
    c = db.get_connection(":memory:")
    first = db.init_db(c)
    second = db.init_db(c)
    assert "001_performance_leads.sql" in first
    assert second == []
    cols = {r["name"] for r in c.execute("PRAGMA table_info(performance)").fetchall()}
    assert "qualified_leads" in cols
    c.close()


def test_migration_upgrades_a_database_created_before_it_existed(tmp_path):
    path = tmp_path / "old.db"
    c = db.get_connection(path)
    c.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))  # baseline only, as an older install would have
    c.commit()
    assert "qualified_leads" not in {r["name"] for r in c.execute("PRAGMA table_info(performance)").fetchall()}
    c.close()

    c = db.get_connection(path)
    db.init_db(c)
    assert "qualified_leads" in {r["name"] for r in c.execute("PRAGMA table_info(performance)").fetchall()}
    c.close()


def test_seed_sources_loads_config(conn):
    count = db.seed_sources(conn)
    assert count > 0
    row = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()
    assert row["n"] == count


def test_seed_sources_is_upsert_not_duplicate(conn):
    db.seed_sources(conn)
    first = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    db.seed_sources(conn)
    second = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    assert first == second


def test_next_sequence_increments_and_is_stable():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    first = db.next_sequence(conn, "signal_2026")
    second = db.next_sequence(conn, "signal_2026")
    other = db.next_sequence(conn, "signal_2027")
    assert first == 1
    assert second == 2
    assert other == 1
    conn.close()
