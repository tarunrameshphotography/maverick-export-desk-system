import pytest

from radar.desk.calls_ledger import add_call, grade_call, hit_rate, list_due_for_review, list_open_calls


def _add(conn, review_on="2026-10-15", **kw):
    return add_call(
        conn,
        call_text="Post-September RoDTEP rates will be notified lower than current rates",
        reasoning="FY27 allocation cut ~45% vs FY26",
        source="Business Standard, Budget 2026-27 coverage",
        made_on="2026-09-14",
        review_on=review_on,
        confidence_word="likely",
        expected_outcome="New RoDTEP schedule with reduced rates on most lines",
        **kw,
    )


def test_add_call_creates_open_entry(conn):
    call_id = _add(conn)
    row = conn.execute("SELECT * FROM calls_ledger WHERE id = ?", (call_id,)).fetchone()
    assert row["grade"] is None
    assert row["confidence_word"] == "likely"
    assert len(list_open_calls(conn)) == 1


def test_add_call_rejects_invalid_confidence_word(conn):
    with pytest.raises(ValueError):
        add_call(conn, "x", "y", "z", "2026-09-14", "2026-10-14", confidence_word="certain")


def test_grade_call_records_outcome_and_closes_it(conn):
    call_id = _add(conn)
    grade_call(conn, call_id, "wrong", "Rates held flat through March 2027", "founder", "2026-10-16")
    row = conn.execute("SELECT * FROM calls_ledger WHERE id = ?", (call_id,)).fetchone()
    assert row["grade"] == "wrong"
    assert row["actual_outcome"] == "Rates held flat through March 2027"
    assert list_open_calls(conn) == []


def test_wrong_calls_are_kept_not_deleted(conn):
    call_id = _add(conn)
    grade_call(conn, call_id, "wrong", "Did not happen", "founder", "2026-10-16")
    assert conn.execute("SELECT COUNT(*) AS n FROM calls_ledger").fetchone()["n"] == 1


def test_grade_call_rejects_invalid_grade(conn):
    call_id = _add(conn)
    with pytest.raises(ValueError):
        grade_call(conn, call_id, "sort_of", "x", "founder", "2026-10-16")


def test_grade_call_rejects_unknown_id(conn):
    with pytest.raises(ValueError):
        grade_call(conn, 999, "right", "x", "founder", "2026-10-16")


def test_list_due_for_review_filters_by_date(conn):
    _add(conn, review_on="2026-10-01")
    _add(conn, review_on="2026-12-01")
    due = list_due_for_review(conn, "2026-10-15")
    assert len(due) == 1
    assert due[0]["review_on"] == "2026-10-01"


def test_hit_rate_counts_graded_calls(conn):
    a, b, c = _add(conn), _add(conn), _add(conn)
    grade_call(conn, a, "right", "x", "founder", "2026-10-16")
    grade_call(conn, b, "partly_right", "x", "founder", "2026-10-16")
    rate = hit_rate(conn)
    assert rate["right"] == 1
    assert rate["partly_right"] == 1
    assert rate["total_graded"] == 2
