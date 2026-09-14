"""Approval status state machine (Section 22). Enforces valid transitions and
captures a reason code on every rejection so it becomes learning data later
(performance_learning_loop.md)."""
from __future__ import annotations

import sqlite3

VALID_STATUSES = {
    "NEW", "TRIAGED", "SCORED", "NEEDS_RESEARCH", "VERIFIED", "READY_FOR_ANGLE",
    "READY_FOR_REVIEW", "APPROVED", "PUBLISHED", "REJECTED", "WATCHLIST",
    "NEEDS_CORRECTION", "ARCHIVED",
}

REASON_CODES = {
    "Too generic", "Not enough Indian relevance", "Weak source", "Cannot verify",
    "Too promotional", "Already covered", "Bad angle", "Too risky",
    "No actionability", "Better story available",
}

TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"TRIAGED"},
    "TRIAGED": {"SCORED", "REJECTED", "WATCHLIST"},
    "SCORED": {"NEEDS_RESEARCH", "VERIFIED", "WATCHLIST", "REJECTED"},
    "NEEDS_RESEARCH": {"VERIFIED", "REJECTED"},
    "VERIFIED": {"READY_FOR_ANGLE", "REJECTED"},
    "READY_FOR_ANGLE": {"READY_FOR_REVIEW", "REJECTED"},
    "READY_FOR_REVIEW": {"APPROVED", "REJECTED", "NEEDS_CORRECTION"},
    "NEEDS_CORRECTION": {"READY_FOR_REVIEW"},
    "APPROVED": {"PUBLISHED", "REJECTED"},
    "PUBLISHED": {"ARCHIVED", "NEEDS_CORRECTION"},
    "WATCHLIST": {"TRIAGED", "SCORED", "REJECTED", "ARCHIVED"},
    "REJECTED": {"ARCHIVED", "WATCHLIST"},
    "ARCHIVED": set(),
}


class InvalidTransitionError(Exception):
    pass


def _minutes_between(started_at_iso: str | None, decided_at_iso: str) -> float | None:
    if not started_at_iso:
        return None
    from dateutil import parser as dateparser

    started = dateparser.parse(started_at_iso)
    decided = dateparser.parse(decided_at_iso)
    return round((decided - started).total_seconds() / 60, 1)


def transition_signal_status(
    conn: sqlite3.Connection,
    signal_id: str,
    to_status: str,
    reviewer: str,
    decided_at_iso: str,
    reason_code: str | None = None,
    started_at_iso: str | None = None,
) -> None:
    if to_status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {to_status}")

    row = conn.execute("SELECT status FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such signal: {signal_id}")
    from_status = row["status"]

    allowed = TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvalidTransitionError(f"Cannot move signal {signal_id} from {from_status} to {to_status}")

    if to_status == "REJECTED":
        if reason_code not in REASON_CODES:
            raise ValueError(f"REJECTED requires a valid reason_code, got: {reason_code!r}")

    conn.execute("UPDATE signals SET status = ?, updated_at = ? WHERE id = ?", (to_status, decided_at_iso, signal_id))
    conn.execute(
        """
        INSERT INTO approvals (entity_type, entity_id, from_status, to_status, reviewer,
                                decision, reason_code, decided_at, time_to_decide_min)
        VALUES ('signal', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id, from_status, to_status, reviewer, to_status, reason_code,
            decided_at_iso, _minutes_between(started_at_iso, decided_at_iso),
        ),
    )
    conn.commit()


def approval_history(conn: sqlite3.Connection, signal_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM approvals WHERE entity_type = 'signal' AND entity_id = ? ORDER BY id",
        (signal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def rejection_reason_breakdown(conn: sqlite3.Connection) -> dict[str, int]:
    """Section 22: 'this should eventually become useful learning data.'"""
    rows = conn.execute(
        "SELECT reason_code, COUNT(*) AS n FROM approvals WHERE to_status = 'REJECTED' GROUP BY reason_code"
    ).fetchall()
    return {row["reason_code"]: row["n"] for row in rows}
