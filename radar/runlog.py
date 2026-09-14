"""system_runs logging (Section 29). Every scheduled or manual run leaves a
row in the DB and a JSON file in data/runs/ so a failed morning can be
diagnosed without re-running anything."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from radar import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, run_type: str) -> int:
    cur = conn.execute("INSERT INTO system_runs (run_type, started_at) VALUES (?, ?)", (run_type, now_iso()))
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    errors: list[str],
    notes: dict | None = None,
    **counts: int | str | None,
) -> dict:
    allowed = {
        "sources_checked", "items_collected", "items_discarded", "duplicates_found",
        "high_score_signals", "verification_failures", "model_calls", "desk_sheet_id",
    }
    counts = {k: v for k, v in counts.items() if k in allowed and v is not None}
    assignments = ", ".join(f"{k} = ?" for k in counts)
    conn.execute(
        f"UPDATE system_runs SET ended_at = ?, status = ?, errors_json = ?, notes = ?"
        f"{', ' + assignments if assignments else ''} WHERE id = ?",
        (now_iso(), status, json.dumps(errors), json.dumps(notes or {}), *counts.values(), run_id),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM system_runs WHERE id = ?", (run_id,)).fetchone())
    (settings.RUNS_DIR / f"run_{run_id:05d}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
    return row
