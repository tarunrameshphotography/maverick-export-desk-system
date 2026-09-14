"""Calls Ledger (Section 20). Accountability for forward-looking claims —
entries are graded, never deleted, even when the call was wrong."""
from __future__ import annotations

import sqlite3

VALID_GRADES = {"right", "partly_right", "wrong", "too_early_unresolved"}
VALID_CONFIDENCE_WORDS = {"likely", "possible"}


def add_call(
    conn: sqlite3.Connection,
    call_text: str,
    reasoning: str,
    source: str,
    made_on: str,
    review_on: str,
    signal_id: str | None = None,
    post_id: int | None = None,
    confidence_word: str | None = None,
    expected_outcome: str | None = None,
) -> int:
    if confidence_word is not None and confidence_word not in VALID_CONFIDENCE_WORDS:
        raise ValueError(f"Invalid confidence_word: {confidence_word}")
    cur = conn.execute(
        """
        INSERT INTO calls_ledger (signal_id, post_id, call_text, reasoning, source,
                                   confidence_word, made_on, review_on, expected_outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (signal_id, post_id, call_text, reasoning, source, confidence_word, made_on, review_on, expected_outcome),
    )
    conn.commit()
    return cur.lastrowid


def grade_call(
    conn: sqlite3.Connection,
    call_id: int,
    grade: str,
    actual_outcome: str,
    graded_by: str,
    graded_on: str,
    notes: str | None = None,
) -> None:
    if grade not in VALID_GRADES:
        raise ValueError(f"Invalid grade: {grade}")
    row = conn.execute("SELECT id FROM calls_ledger WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such call: {call_id}")
    conn.execute(
        """
        UPDATE calls_ledger
        SET grade = ?, actual_outcome = ?, graded_by = ?, graded_on = ?, notes = COALESCE(?, notes)
        WHERE id = ?
        """,
        (grade, actual_outcome, graded_by, graded_on, notes, call_id),
    )
    conn.commit()


def list_due_for_review(conn: sqlite3.Connection, as_of_iso_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM calls_ledger WHERE review_on <= ? AND grade IS NULL ORDER BY review_on",
        (as_of_iso_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_open_calls(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM calls_ledger WHERE grade IS NULL ORDER BY review_on").fetchall()
    return [dict(r) for r in rows]


def hit_rate(conn: sqlite3.Connection) -> dict:
    """Right / partly-right / wrong / unresolved counts among graded calls —
    the "Ledger hit rate" metric from performance_learning_loop.md."""
    rows = conn.execute("SELECT grade, COUNT(*) AS n FROM calls_ledger WHERE grade IS NOT NULL GROUP BY grade").fetchall()
    counts = {grade: 0 for grade in VALID_GRADES}
    for row in rows:
        counts[row["grade"]] = row["n"]
    total_graded = sum(counts.values())
    return {**counts, "total_graded": total_graded}
