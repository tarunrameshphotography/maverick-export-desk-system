import pytest

from radar.desk.approvals import (
    InvalidTransitionError,
    approval_history,
    rejection_reason_breakdown,
    transition_signal_status,
)


def _seed(conn, signal_id="SIG-AP-0001", status="SCORED"):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, created_at, updated_at)
        VALUES (?, 't', '2026-09-14', 'incentives', ?, '2026-09-14', '2026-09-14')
        """,
        (signal_id, status),
    )
    conn.commit()
    return signal_id


def test_happy_path_through_to_published(conn):
    sid = _seed(conn)
    for step in ["NEEDS_RESEARCH", "VERIFIED", "READY_FOR_ANGLE", "READY_FOR_REVIEW", "APPROVED", "PUBLISHED"]:
        transition_signal_status(conn, sid, step, "founder", "2026-09-14T10:30:00")
    assert conn.execute("SELECT status FROM signals WHERE id = ?", (sid,)).fetchone()["status"] == "PUBLISHED"
    assert len(approval_history(conn, sid)) == 6


def test_cannot_skip_verification_straight_to_approved(conn):
    sid = _seed(conn)
    with pytest.raises(InvalidTransitionError):
        transition_signal_status(conn, sid, "APPROVED", "founder", "2026-09-14T10:30:00")


def test_cannot_publish_without_approval(conn):
    sid = _seed(conn, status="READY_FOR_REVIEW")
    with pytest.raises(InvalidTransitionError):
        transition_signal_status(conn, sid, "PUBLISHED", "founder", "2026-09-14T10:30:00")


def test_archived_is_terminal(conn):
    sid = _seed(conn, status="ARCHIVED")
    with pytest.raises(InvalidTransitionError):
        transition_signal_status(conn, sid, "TRIAGED", "founder", "2026-09-14T10:30:00")


def test_rejection_requires_valid_reason_code(conn):
    sid = _seed(conn)
    with pytest.raises(ValueError):
        transition_signal_status(conn, sid, "REJECTED", "founder", "2026-09-14T10:30:00")
    with pytest.raises(ValueError):
        transition_signal_status(conn, sid, "REJECTED", "founder", "2026-09-14T10:30:00", reason_code="meh")


def test_rejection_with_reason_is_logged(conn):
    sid = _seed(conn)
    transition_signal_status(conn, sid, "REJECTED", "founder", "2026-09-14T10:30:00", reason_code="Too generic")
    history = approval_history(conn, sid)
    assert history[-1]["reason_code"] == "Too generic"
    assert history[-1]["from_status"] == "SCORED"
    assert rejection_reason_breakdown(conn) == {"Too generic": 1}


def test_time_to_decide_is_recorded(conn):
    sid = _seed(conn)
    transition_signal_status(
        conn, sid, "NEEDS_RESEARCH", "founder", "2026-09-14T08:00:00", started_at_iso="2026-09-14T07:45:00"
    )
    assert approval_history(conn, sid)[0]["time_to_decide_min"] == 15.0


def test_correction_loop_after_publish(conn):
    sid = _seed(conn, status="PUBLISHED")
    transition_signal_status(conn, sid, "NEEDS_CORRECTION", "founder", "2026-09-15T09:00:00")
    transition_signal_status(conn, sid, "READY_FOR_REVIEW", "analyst", "2026-09-15T10:00:00")
    assert conn.execute("SELECT status FROM signals WHERE id = ?", (sid,)).fetchone()["status"] == "READY_FOR_REVIEW"


def test_unknown_signal_raises(conn):
    with pytest.raises(ValueError):
        transition_signal_status(conn, "SIG-NOPE", "TRIAGED", "founder", "2026-09-14T10:30:00")
