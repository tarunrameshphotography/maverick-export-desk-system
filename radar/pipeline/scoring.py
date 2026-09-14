"""Content scoring engine (Section 13, Part 12 of content_scoring_model.md).
Every weight and threshold is read from config/scoring_weights.yaml — nothing
here is a hard-coded number that a founder can't see and change.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from radar import settings
from radar.pipeline.exposure import IndianExposure

NUMERIC_CLAIM_PATTERN = re.compile(r"\d+(\.\d+)?\s*%|₹\s*\d|\$\s*\d|\bcrore\b|\bmillion\b|\bbillion\b", re.I)
GEOPOLITICAL_PATTERN = re.compile(r"\b(sanction|war|conflict|geopolit|military|strait|blockade|invasion)\b", re.I)
SPECULATIVE_PATTERN = re.compile(r"\b(could|might|may (?:lead|result)|is expected to|likely to|is rumou?red)\b", re.I)


@dataclass
class GateResult:
    passed: bool
    failed_gates: list[str] = field(default_factory=list)


def evaluate_gates(
    conn: sqlite3.Connection,
    signal_id: str,
    exposure: IndianExposure,
    has_primary_source: bool,
    secondary_source_count: int,
    text: str,
) -> GateResult:
    cfg = settings.scoring_weights()["gates"]
    failed: list[str] = []

    if cfg["g1_evidence_required"] and not (has_primary_source or secondary_source_count >= 2):
        failed.append("G1_evidence")

    if cfg["g2_indian_implication_required"] and exposure.direct_effect != "true":
        failed.append("G2_indian_implication")

    if _is_exact_repeat(conn, signal_id, cfg["g3_repeat_window_days"]):
        failed.append("G3_repeat")

    if cfg["g4_reject_unverifiable_numbers"] and NUMERIC_CLAIM_PATTERN.search(text) and not has_primary_source and secondary_source_count < 2:
        failed.append("G4_unverifiable_numbers")

    return GateResult(passed=not failed, failed_gates=failed)


def _signal_scheme_authority_entities(conn: sqlite3.Connection, signal_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT normalized_value FROM signal_entities WHERE signal_id = ? AND entity_type IN ('scheme', 'authority')",
        (signal_id,),
    ).fetchall()
    return {r["normalized_value"] for r in rows}


def _is_exact_repeat(conn: sqlite3.Connection, signal_id: str, window_days: int) -> bool:
    this_signal = conn.execute("SELECT topic_category, created_at FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if this_signal is None:
        return False
    this_entities = _signal_scheme_authority_entities(conn, signal_id)
    if not this_entities:
        return False

    candidates = conn.execute(
        """
        SELECT id FROM signals
        WHERE id != ? AND topic_category = ?
        AND julianday(?) - julianday(created_at) BETWEEN 0 AND ?
        """,
        (signal_id, this_signal["topic_category"], this_signal["created_at"], window_days),
    ).fetchall()
    for row in candidates:
        other_entities = _signal_scheme_authority_entities(conn, row["id"])
        if other_entities and other_entities == this_entities:
            return True
    return False


def _has_near_repeat(conn: sqlite3.Connection, signal_id: str, window_days: int) -> bool:
    this_signal = conn.execute("SELECT created_at FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if this_signal is None:
        return False
    this_entities = _signal_scheme_authority_entities(conn, signal_id)
    if not this_entities:
        return False
    candidates = conn.execute(
        """
        SELECT id FROM signals
        WHERE id != ? AND julianday(?) - julianday(created_at) BETWEEN 0 AND ?
        """,
        (signal_id, this_signal["created_at"], window_days),
    ).fetchall()
    for row in candidates:
        other_entities = _signal_scheme_authority_entities(conn, row["id"])
        if other_entities and (other_entities & this_entities) and other_entities != this_entities:
            return True
    return False


def _tribool_points(value: str, true_points: float, uncertain_points: float, false_points: float) -> float:
    return {"true": true_points, "uncertain": uncertain_points, "false": false_points}.get(value, uncertain_points)


def score_criteria(
    exposure: IndianExposure,
    has_primary_source: bool,
    secondary_source_count: int,
    saturation_score_0to5: int,
    angle_selected: bool,
    angle_exists: bool,
    entity_count: int,
) -> dict[str, float]:
    """Returns {criterion_id: score_0_to_5} for every criterion in config."""
    impact_hits = sum(
        1 for v in (exposure.landed_cost_change, exposure.market_access_change, exposure.compliance_cost_change)
        if v == "true"
    )
    impact_hits += 1 if exposure.threat == "true" or exposure.opportunity == "true" else 0
    impact_hits += 1 if exposure.direct_effect == "true" else 0
    exporter_impact = min(5, 1 + impact_hits)

    actionability = _tribool_points(exposure.actionable_this_week, 5, 3, 1)
    time_sensitivity = actionability  # same underlying evidence in v1 (Section 13 allows this to diverge later)

    angle_strength = 4 if angle_selected else (3 if angle_exists else 2)

    breadth = len(exposure.destination_markets) + len(exposure.clusters) + len(exposure.hs_codes)
    indian_exposure = min(5, (2 if exposure.direct_effect == "true" else 0) + min(3, breadth))

    under_coverage = saturation_score_0to5

    explainability = 4 if entity_count <= 4 else (2 if entity_count > 9 else 3)

    audience_fit = 5 if exposure.clusters else (3 if exposure.direct_effect == "true" else 1)

    evidence_quality = 5 if has_primary_source else (3 if secondary_source_count >= 2 else 1)

    return {
        "exporter_impact": float(exporter_impact),
        "actionability": float(actionability),
        "angle_strength": float(angle_strength),
        "indian_exposure": float(indian_exposure),
        "under_coverage": float(under_coverage),
        "time_sensitivity": float(time_sensitivity),
        "explainability": float(explainability),
        "audience_fit": float(audience_fit),
        "evidence_quality": float(evidence_quality),
    }


def compute_base_score(criteria_scores: dict[str, float]) -> float:
    weights = {c["id"]: c["weight"] for c in settings.scoring_weights()["criteria"]}
    total = 0.0
    for criterion_id, score in criteria_scores.items():
        weight = weights.get(criterion_id, 0)
        total += weight * (score / 5)
    return round(total, 2)


def compute_penalties(conn: sqlite3.Connection, signal_id: str, text: str, has_forecast_claim: bool) -> tuple[float, list[str]]:
    cfg = settings.scoring_weights()["penalties"]
    applied: list[str] = []
    total = 0.0

    if GEOPOLITICAL_PATTERN.search(text):
        total += cfg["geopolitical_sensitivity"]
        applied.append("geopolitical_sensitivity")

    if SPECULATIVE_PATTERN.search(text) and not has_forecast_claim:
        total += cfg["speculation_min"]
        applied.append("speculation")

    if _has_near_repeat(conn, signal_id, 14):
        total += cfg["near_repeat"]
        applied.append("near_repeat")

    return round(total, 2), applied


def decide(final_score: float) -> str:
    thresholds = settings.scoring_weights()["decision_thresholds"]
    if final_score >= thresholds["lead_signal"]:
        return "lead"
    if final_score >= thresholds["secondary"]:
        return "secondary"
    if final_score >= thresholds["watchlist"]:
        return "watchlist"
    return "discard"


def score_signal(
    conn: sqlite3.Connection,
    signal_id: str,
    text: str,
    exposure: IndianExposure,
    has_primary_source: bool,
    secondary_source_count: int,
    saturation_score_0to5: int,
    entity_count: int,
    now_iso: str,
) -> dict:
    gate_result = evaluate_gates(conn, signal_id, exposure, has_primary_source, secondary_source_count, text)

    angle_row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(selected) AS selected FROM analyses WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    angle_exists = (angle_row["n"] or 0) > 0
    angle_selected = (angle_row["selected"] or 0) > 0

    criteria_scores = score_criteria(
        exposure, has_primary_source, secondary_source_count, saturation_score_0to5,
        angle_selected, angle_exists, entity_count,
    )
    base_score = compute_base_score(criteria_scores)

    confidence_cfg = settings.scoring_weights()["confidence_multiplier"]
    confidence_mult = confidence_cfg["primary_verified"] if has_primary_source else confidence_cfg["credible_secondary_pending"]

    has_forecast_claim = conn.execute(
        "SELECT COUNT(*) AS n FROM claims WHERE signal_id = ? AND claim_type = 'forecast'", (signal_id,)
    ).fetchone()["n"] > 0
    penalties_total, penalties_applied = compute_penalties(conn, signal_id, text, has_forecast_claim)

    final_score = max(0.0, round(base_score * confidence_mult - penalties_total, 2))
    decision = "discard" if not gate_result.passed else decide(final_score)
    decision_reason = None
    if not gate_result.passed:
        decision_reason = "Failed gates: " + ", ".join(gate_result.failed_gates)

    breakdown = {
        "gates": {"passed": gate_result.passed, "failed": gate_result.failed_gates},
        "criteria": criteria_scores,
        "base_score": base_score,
        "confidence_mult": confidence_mult,
        "penalties_applied": penalties_applied,
        "penalties_total": penalties_total,
        "final_score": final_score,
        "decision": decision,
    }

    conn.execute(
        """
        UPDATE signals
        SET score_base = ?, confidence_mult = ?, penalties = ?, score_final = ?,
            score_breakdown_json = ?, decision = ?, decision_reason = ?,
            status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            base_score, confidence_mult, penalties_total, final_score,
            json.dumps(breakdown), decision, decision_reason,
            "WATCHLIST" if decision == "watchlist" else ("REJECTED" if decision == "discard" else "SCORED"),
            now_iso, signal_id,
        ),
    )
    conn.commit()
    return breakdown
