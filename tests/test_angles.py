import pytest

from radar.pipeline.angles import list_angles, render_angle_brief, save_angle, select_angle


def _seed_signal(conn, signal_id="SIG-A-0001"):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, score_final, decision, created_at, updated_at)
        VALUES (?, 'RoDTEP extension', '2026-08-15', 'incentives', 'SCORED', 82.0, 'lead', '2026-08-15', '2026-08-15')
        """,
        (signal_id,),
    )
    conn.commit()
    return signal_id


def test_save_angle_rejects_unknown_franchise(conn):
    signal_id = _seed_signal(conn)
    with pytest.raises(ValueError):
        save_angle(conn, signal_id, "Not A Real Franchise", "fine_print", "x", "y", "claude_code", "2026-09-14T09:00:00")


def test_save_angle_rejects_unknown_pattern(conn):
    signal_id = _seed_signal(conn)
    with pytest.raises(ValueError):
        save_angle(conn, signal_id, "Second Order", "not_a_real_pattern", "x", "y", "claude_code", "2026-09-14T09:00:00")


def test_save_angle_persists_and_lists(conn):
    signal_id = _seed_signal(conn)
    save_angle(
        conn, signal_id, "Second Order", "hidden_threat",
        "RoDTEP extension masks a shrinking FY27 budget",
        "The extension buys time, but the 45% FY27 budget cut means post-Sept rates could land lower.",
        "claude_code", "2026-09-14T09:00:00",
    )
    angles = list_angles(conn, signal_id)
    assert len(angles) == 1
    assert angles[0]["franchise"] == "Second Order"
    assert angles[0]["selected"] == 0


def test_select_angle_marks_one_selected_and_unselects_others(conn):
    signal_id = _seed_signal(conn)
    id1 = save_angle(conn, signal_id, "Second Order", "hidden_threat", "A", "a", "claude_code", "2026-09-14T09:00:00")
    id2 = save_angle(conn, signal_id, "The Fine Print", "fine_print", "B", "b", "claude_code", "2026-09-14T09:01:00")

    select_angle(conn, id2)

    angles = {a["id"]: a["selected"] for a in list_angles(conn, signal_id)}
    assert angles[id1] == 0
    assert angles[id2] == 1

    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal_id,)).fetchone()["status"]
    assert status == "READY_FOR_REVIEW"


def test_select_angle_raises_for_unknown_id(conn):
    with pytest.raises(ValueError):
        select_angle(conn, 9999)


def test_render_angle_brief_includes_questions_and_guardrails(conn):
    signal_id = _seed_signal(conn)
    brief = render_angle_brief(conn, signal_id)
    assert "Who pays and who gains?" in brief
    assert "BIG UPDATE!!!" in brief
    assert signal_id in brief
