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


def test_migration_007_rebuild_does_not_violate_source_failures_fk(tmp_path):
    # Regression: migration 007 recreates system_runs (to widen its run_type
    # CHECK constraint) via CREATE-new/INSERT/DROP-old/RENAME. DROP TABLE
    # performs an implicit delete of every row, and source_failures.run_id
    # REFERENCES system_runs(id) with foreign_keys=ON (connection.py sets
    # this on every connection) -- so on any real DB that already has a
    # source_failures row pointing at an existing system_runs row, the naive
    # DROP TABLE raised sqlite3.IntegrityError the moment migration 007 ran.
    #
    # Reproduce by building a DB with migrations 001-006 applied (but not
    # 007), seeding FK-linked rows, then letting init_db apply 007.
    path = tmp_path / "pre_007.db"
    c = db.get_connection(path)
    c.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for migration_path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if migration_path.name == "007_content_generation_runtype.sql":
            continue
        c.executescript(migration_path.read_text(encoding="utf-8"))
        c.execute(
            "INSERT INTO schema_migrations (name, applied_at) VALUES (?, datetime('now'))",
            (migration_path.name,),
        )
    c.execute(
        "INSERT INTO sources (id, name, kind, signal_type, method, reliability_1to5) "
        "VALUES ('src-1', 'Test Source', 'primary', 'early', 'rss', 3)"
    )
    c.execute(
        "INSERT INTO system_runs (id, run_type, started_at, status) "
        "VALUES (999, 'manual', '2026-09-15T00:00:00', 'completed')"
    )
    c.execute(
        "INSERT INTO source_failures (source_id, run_id, occurred_at, error_text) "
        "VALUES ('src-1', 999, '2026-09-15T00:00:00', 'boom')"
    )
    c.commit()
    c.close()

    # Re-opening and running init_db must apply migration 007 without raising,
    # and both FK-linked rows must survive the table rebuild.
    c = db.get_connection(path)
    applied = db.init_db(c)
    assert "007_content_generation_runtype.sql" in applied

    run_row = c.execute("SELECT * FROM system_runs WHERE id = 999").fetchone()
    failure_row = c.execute("SELECT * FROM source_failures WHERE run_id = 999").fetchone()
    assert run_row is not None
    assert failure_row is not None
    c.close()


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
