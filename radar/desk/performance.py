"""Performance import and the learning hook (Phase 15, performance_learning_loop.md).

Import: a weekly CSV (one row per post per capture), matched to published_content
by post URL. Learning: compares each scoring criterion against realised
qualified outcomes and *proposes* a weight shift. It never edits config —
a human accepts or rejects the proposal, and edits scoring_weights.yaml by hand.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from statistics import mean

from radar import settings

METRIC_COLUMNS = [
    "impressions", "reactions", "comments", "reposts", "saves", "sends", "profile_visits",
    "link_clicks", "follows", "qualified_comments", "qualified_engagers_sampled", "qualified_leads",
]
MIN_POSTS_FOR_PROPOSAL = 8
WEIGHT_STEP = 2


def import_metrics_csv(conn: sqlite3.Connection, csv_path: str | Path) -> dict:
    """Required columns: url, captured_at. Optional: every METRIC_COLUMNS name and
    qualified_share. Rows whose URL matches no published post are reported, not dropped silently."""
    imported, unmatched = 0, []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            post = conn.execute("SELECT id FROM published_content WHERE url = ?", (row["url"].strip(),)).fetchone()
            if post is None:
                unmatched.append(row["url"])
                continue
            values = [int(row.get(col) or 0) for col in METRIC_COLUMNS]
            share = float(row["qualified_share"]) if row.get("qualified_share") else None
            conn.execute(
                f"""
                INSERT INTO performance (published_content_id, captured_at, {", ".join(METRIC_COLUMNS)}, qualified_share)
                VALUES (?, ?, {", ".join("?" for _ in METRIC_COLUMNS)}, ?)
                """,
                (post["id"], row["captured_at"], *values, share),
            )
            imported += 1
    conn.commit()
    return {"imported": imported, "unmatched_urls": unmatched}


def _outcome(latest: sqlite3.Row) -> float:
    # Leads dominate; qualified comments are the diagnostic fallback. Raw reach never counts
    # ("Never let a reach-winning format displace a franchise that produces leads").
    return latest["qualified_leads"] * 10 + latest["qualified_comments"]


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


def scored_outcomes(conn: sqlite3.Connection) -> list[dict]:
    """One row per published post: its signal's criterion scores and latest outcome."""
    rows = conn.execute(
        """
        SELECT pc.id AS post_id, s.score_breakdown_json,
               p.qualified_leads, p.qualified_comments
        FROM published_content pc
        JOIN content_drafts cd ON cd.id = pc.draft_id
        JOIN signals s ON s.id = cd.signal_id
        JOIN performance p ON p.published_content_id = pc.id
        WHERE p.id = (SELECT MAX(id) FROM performance WHERE published_content_id = pc.id)
          AND s.score_breakdown_json IS NOT NULL
        """
    ).fetchall()
    return [
        {"post_id": r["post_id"], "criteria": json.loads(r["score_breakdown_json"])["criteria"], "outcome": _outcome(r)}
        for r in rows
    ]


def propose_weight_changes(conn: sqlite3.Connection, period: str) -> int | None:
    """Writes a learnings row proposing to move WEIGHT_STEP points from the
    criterion that least predicts outcomes to the one that best predicts them.
    Returns the learnings id, or None when there isn't enough data to say."""
    data = scored_outcomes(conn)
    if len(data) < MIN_POSTS_FOR_PROPOSAL:
        return None
    outcomes = [d["outcome"] for d in data]
    correlations = {
        cid: round(_pearson([d["criteria"].get(cid, 0.0) for d in data], outcomes), 3)
        for cid in (c["id"] for c in settings.scoring_weights()["criteria"])
    }
    best = max(correlations, key=correlations.get)
    worst = min(correlations, key=correlations.get)
    if best == worst or correlations[best] - correlations[worst] < 0.2:
        return None

    weights = {c["id"]: c["weight"] for c in settings.scoring_weights()["criteria"]}
    step = min(WEIGHT_STEP, weights[worst])
    change = {worst: weights[worst] - step, best: weights[best] + step}
    cur = conn.execute(
        """
        INSERT INTO learnings (period, finding, evidence_json, action, weight_change_json, accepted)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            period,
            f"'{best}' predicts qualified outcomes best (r={correlations[best]}); '{worst}' worst (r={correlations[worst]}).",
            json.dumps({"posts": len(data), "correlations": correlations}),
            f"Move {step} weight points from {worst} to {best} in config/scoring_weights.yaml",
            json.dumps(change),
        ),
    )
    conn.commit()
    return cur.lastrowid


def decide_learning(conn: sqlite3.Connection, learning_id: int, accept: bool) -> None:
    """learnings.accepted: 0 = pending, 1 = accepted, -1 = rejected."""
    conn.execute("UPDATE learnings SET accepted = ? WHERE id = ?", (1 if accept else -1, learning_id))
    conn.commit()
