"""SQLite connection + schema management. Kept deliberately thin so a later
move to Postgres only needs to swap this module (Section 21)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from radar import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    if db_path == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path) if db_path else settings.DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def init_db(conn: sqlite3.Connection) -> list[str]:
    """Applies the baseline schema (idempotent) then any migrations/NNN_*.sql
    not yet recorded in schema_migrations. Returns the migrations applied now."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied_names = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied_names:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (name, applied_at) VALUES (?, datetime('now'))", (path.name,)
        )
        newly_applied.append(path.name)
    conn.commit()
    return newly_applied


def next_sequence(conn: sqlite3.Connection, name: str) -> int:
    """Atomically increment and return a named counter (e.g. 'signal_2026')."""
    conn.execute(
        "INSERT INTO id_sequences(name, value) VALUES (?, 1) "
        "ON CONFLICT(name) DO UPDATE SET value = value + 1",
        (name,),
    )
    row = conn.execute("SELECT value FROM id_sequences WHERE name = ?", (name,)).fetchone()
    conn.commit()
    return row["value"]


def seed_sources(conn: sqlite3.Connection) -> int:
    """Upsert the source registry from config/sources.yaml. Returns row count written."""
    count = 0
    for src in settings.sources():
        conn.execute(
            """
            INSERT INTO sources (id, name, url, kind, signal_type, cadence, method,
                                  country, category, reliability_1to5, active, query, notes)
            VALUES (:id, :name, :url, :kind, :signal_type, :cadence, :method,
                    :country, :category, :reliability_1to5, :active, :query, :notes)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, url=excluded.url, kind=excluded.kind,
                signal_type=excluded.signal_type, cadence=excluded.cadence,
                method=excluded.method, country=excluded.country,
                category=excluded.category, reliability_1to5=excluded.reliability_1to5,
                active=excluded.active, query=excluded.query, notes=excluded.notes
            """,
            {
                "id": src["id"],
                "name": src["name"],
                "url": src.get("url"),
                "kind": src["kind"],
                "signal_type": src["signal_type"],
                "cadence": src.get("cadence"),
                "method": src["method"],
                "country": src.get("country"),
                "category": src.get("category"),
                "reliability_1to5": src["reliability_1to5"],
                "active": 1 if src.get("active", True) else 0,
                "query": src.get("query"),
                "notes": src.get("notes"),
            },
        )
        count += 1
    conn.commit()
    return count
