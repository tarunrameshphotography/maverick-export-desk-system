"""Maverick Angle Engine (Section 9, content_architecture.md "eight questions").

Angle generation is genuine analytical work — picking the differentiated
insight a headline missed — which the hybrid architecture routes to a
Claude Code session rather than a keyword heuristic (see decision_thresholds
and model_routing.mode in scoring_weights.yaml). This module's job is
mechanical: assemble everything the analyst/model needs into one brief, and
persist whatever angles come back.
"""
from __future__ import annotations

import sqlite3

ANGLE_PATTERNS = [
    "what_the_headline_misses", "fine_print", "second_order_effect",
    "competitive_comparison", "product_level_consequence", "country_level_consequence",
    "cluster_level_consequence", "margin_impact", "compliance_impact",
    "timing_transition_risk", "hidden_opportunity", "hidden_threat",
    "who_benefits", "who_loses", "change_vs_last_month", "what_to_do_now",
    "what_not_to_assume",
]

FRANCHISES = [
    "The Fine Print", "Second Order", "Countdown", "Headline vs Reality",
    "Draft Watch", "Rejection Watch", "Freight Pulse", "The Number",
    "Market Door", "News-pegged explainer", "Ask the Desk", "The Calls Ledger",
    "Case Notes", "Offers",
]

ANGLE_ENGINE_QUESTIONS = [
    "Who pays and who gains? Follow the money through the value chain.",
    "Which HS lines? Name them. If you can't, the story isn't ready.",
    "Which Indian exposure? The value of India's exports of those lines to that market. Source it.",
    "Which competitor country moves? Relative change matters more than absolute change.",
    "Which cluster feels it first?",
    "What's the deadline or window? When does it take effect, and when does it bite?",
    "What does the headline get wrong or leave out?",
    "What should an exporter do this week? If there's no action, it's a watchlist item, not a post.",
]

BANNED_HEADLINE_PATTERNS = [
    "Nobody is talking about this!!!", "BIG UPDATE!!!", "This will CHANGE everything",
    "Game changer", "Secret export opportunity", "Guaranteed export opportunity",
]


def render_angle_brief(conn: sqlite3.Connection, signal_id: str) -> str:
    signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    entities = conn.execute("SELECT * FROM signal_entities WHERE signal_id = ?", (signal_id,)).fetchall()
    verified_claims = conn.execute(
        "SELECT * FROM claims WHERE signal_id = ? AND status = 'verified'", (signal_id,)
    ).fetchall()

    lines = [
        f"# Angle brief — {signal_id}",
        "",
        f"**Title:** {signal['title']}",
        f"**Category:** {signal['topic_category']}",
        f"**Score:** {signal['score_final']} ({signal['decision']})",
        "",
        "## Entities",
    ]
    for e in entities:
        lines.append(f"- {e['entity_type']}: {e['normalized_value']}")

    lines += ["", "## Verified claims"]
    if verified_claims:
        for c in verified_claims:
            lines.append(f"- [{c['claim_type'].upper()}] {c['claim_text']} — {c['source_quote']} ({c['source_url']})")
    else:
        lines.append("_No claims verified yet — run the verification worksheet first._")

    lines += ["", "## The eight Angle Engine questions"]
    for i, q in enumerate(ANGLE_ENGINE_QUESTIONS, 1):
        lines.append(f"{i}. {q}")

    lines += [
        "",
        "## Angle pattern menu",
        ", ".join(ANGLE_PATTERNS),
        "",
        "## Franchise menu",
        ", ".join(FRANCHISES),
        "",
        "## Guardrails (Section 10)",
        "Find the hidden implication, not pretend the underlying news is secret.",
        "Do not use: " + "; ".join(BANNED_HEADLINE_PATTERNS),
        "",
        "Produce 2-3 angle options, each as: franchise, angle_pattern, angle_label, angle_text.",
        "Pick the one with the greatest useful differentiation — avoid empty contrarianism.",
    ]
    return "\n".join(lines)


def save_angle(
    conn: sqlite3.Connection,
    signal_id: str,
    franchise: str,
    angle_pattern: str,
    angle_label: str,
    angle_text: str,
    created_by: str,
    now_iso: str,
) -> int:
    if franchise not in FRANCHISES:
        raise ValueError(f"Unknown franchise: {franchise}")
    if angle_pattern not in ANGLE_PATTERNS:
        raise ValueError(f"Unknown angle pattern: {angle_pattern}")
    if created_by not in ("human", "claude_code"):
        raise ValueError("created_by must be 'human' or 'claude_code'")

    cur = conn.execute(
        """
        INSERT INTO analyses (signal_id, franchise, angle_pattern, angle_label, angle_text, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (signal_id, franchise, angle_pattern, angle_label, angle_text, created_by, now_iso),
    )
    conn.commit()
    return cur.lastrowid


def select_angle(conn: sqlite3.Connection, analysis_id: int) -> None:
    row = conn.execute("SELECT signal_id FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such analysis: {analysis_id}")
    conn.execute("UPDATE analyses SET selected = 0 WHERE signal_id = ?", (row["signal_id"],))
    conn.execute("UPDATE analyses SET selected = 1 WHERE id = ?", (analysis_id,))
    conn.execute("UPDATE signals SET status = 'READY_FOR_REVIEW' WHERE id = ?", (row["signal_id"],))
    conn.commit()


def list_angles(conn: sqlite3.Connection, signal_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM analyses WHERE signal_id = ? ORDER BY id", (signal_id,)).fetchall()
    return [dict(r) for r in rows]
