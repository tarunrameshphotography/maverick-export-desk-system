"""Scheduled run modes (Sections 17 and 27). Each mode is one system_runs row.

overnight: collect only.
morning:   collect -> dedupe/cluster -> classify -> entities -> exposure ->
           saturation -> score -> claims scaffolding for candidates -> Desk Sheet.
evening:   same as morning, but the sheet is written as <date>-evening.md
           (tomorrow's Watchlist) so it never overwrites the morning sheet.

A run never raises out: a crash is recorded as status='failed' with the
traceback in errors_json, and the CLI exits non-zero so Task Scheduler shows it.
"""
from __future__ import annotations

import sqlite3
import traceback
from datetime import date

from radar.collectors.registry import run_collection
from radar.content import generation as content_generation
from radar.db.connection import init_db, seed_sources
from radar.desk.approvals import transition_signal_status
from radar.desk.desk_sheet import write_desk_sheet
from radar.pipeline.orchestrator import run_pipeline
from radar.pipeline.verify import build_claims_table
from radar.runlog import finish_run, now_iso, start_run

RUN_TYPES = {
    "overnight": "overnight_collect",
    "morning": "morning_pipeline",
    "evening": "evening_sweep",
    "content": "content_generation",
}


def _prepare_candidates_for_research(conn: sqlite3.Connection, signal_ids: list[str]) -> int:
    """Step 7 (mechanical half): lead/secondary signals get a claims table and
    move to NEEDS_RESEARCH so they show up as verification work, not as
    publishable items."""
    prepared = 0
    for sid in signal_ids:
        row = conn.execute("SELECT status, decision FROM signals WHERE id = ?", (sid,)).fetchone()
        if row["decision"] not in ("lead", "secondary") or row["status"] != "SCORED":
            continue
        build_claims_table(conn, sid)
        transition_signal_status(conn, sid, "NEEDS_RESEARCH", "system", now_iso())
        prepared += 1
    return prepared


def _generate_content_for_ready_signals(
    conn: sqlite3.Connection, now_iso_str: str, notes: dict, generate_fn=None
) -> int:
    """Step 8 (mechanical half): every signal a human has already moved to
    READY_FOR_REVIEW by selecting an angle (angles.select_angle sets this
    status) gets its content bundle generated and linted automatically, so
    the human's next action is review/edit/approve, not a blank scaffold.
    Never touches draft status or the publishing queue — both still require
    a human (CLAUDE.md's hybrid-model guardrails).

    Mutates `notes` in place as candidates are processed, so partial progress
    is visible even if an unexpected exception aborts the run partway through
    (the caller's outer except still marks the run 'failed', but notes shows
    what was actually done). Returns the number of model calls made.

    A signal with zero verified claims is checked for *before* any scaffold
    is created (rather than letting create_and_generate_bundle's ValueError
    catch it after drafts.create_content_bundle has already inserted and
    committed scaffold rows) so it remains a valid candidate — via the
    'NOT IN content_drafts' query above — on a later run once a claim is
    verified. Without this pre-check the signal would be permanently
    excluded the moment its scaffold rows exist, defeating the retry design.

    Note: a crash partway through a single signal's generation (after some
    but not all asset slots are written) leaves that signal's partial
    content_drafts rows in place, and it will not be retried automatically
    on a later run — recovering from a partial bundle is a known limitation,
    not handled here."""
    candidate_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM signals WHERE status = 'READY_FOR_REVIEW' "
            "AND id NOT IN (SELECT DISTINCT signal_id FROM content_drafts)"
        ).fetchall()
    ]
    notes["candidates"] = len(candidate_ids)
    notes["bundles_generated"] = 0
    notes["skipped"] = []
    model_calls = 0
    for signal_id in candidate_ids:
        verified_count = conn.execute(
            "SELECT COUNT(*) AS n FROM claims WHERE signal_id = ? AND status = 'verified'", (signal_id,)
        ).fetchone()["n"]
        if not verified_count:
            notes["skipped"].append({
                "signal_id": signal_id,
                "reason": f"{signal_id} has no verified claims yet — refusing to generate content with nothing to ground it in.",
            })
            continue
        try:
            results = content_generation.create_and_generate_bundle(
                conn, signal_id, now_iso_str, generate_fn=generate_fn
            )
            model_calls += len(results)
            notes["bundles_generated"] += 1
            notes["model_calls"] = model_calls
        except ValueError as exc:
            notes["skipped"].append({"signal_id": signal_id, "reason": str(exc)})
    return model_calls


def run(conn: sqlite3.Connection, mode: str, use_sample: bool = False, reference_date: date | None = None, generate_fn=None) -> dict:
    if mode not in RUN_TYPES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {sorted(RUN_TYPES)}")
    init_db(conn)
    seed_sources(conn)
    run_id = start_run(conn, RUN_TYPES[mode])
    counts: dict = {}
    notes: dict = {"mode": mode, "sample": use_sample}
    errors: list[str] = []
    try:
        if mode == "content":
            counts["model_calls"] = _generate_content_for_ready_signals(
                conn, now_iso(), notes, generate_fn=generate_fn
            )
            return finish_run(conn, run_id, "completed", errors, notes, **counts)

        collection = run_collection(conn, run_id, use_sample=use_sample)
        errors += collection["errors"]
        counts.update(
            sources_checked=collection["sources_checked"],
            items_collected=collection["items_collected"],
            duplicates_found=collection["duplicates_found"],
        )

        if mode in ("morning", "evening"):
            pipeline = run_pipeline(conn, now_iso(), reference_date)
            processed_ids = [r["signal_id"] for r in pipeline["results"]]
            decisions = [r["score"]["decision"] for r in pipeline["results"]]
            counts.update(
                high_score_signals=pipeline["high_score_signals"],
                items_discarded=decisions.count("discard"),
            )
            notes["signals_created"] = len(pipeline["new_signals"])
            notes["decisions"] = {d: decisions.count(d) for d in set(decisions)}
            notes["prepared_for_research"] = _prepare_candidates_for_research(conn, processed_ids)
            counts["verification_failures"] = conn.execute(
                "SELECT COUNT(*) AS n FROM claims WHERE status = 'failed'"
            ).fetchone()["n"]

            _, path = write_desk_sheet(
                conn, reference_date, suffix="-evening" if mode == "evening" else "",
                run_health={**collection, "errors": collection["errors"]},
            )
            counts["desk_sheet_id"] = path
            notes["desk_sheet"] = path

        counts["model_calls"] = 0
        return finish_run(conn, run_id, "completed", errors, notes, **counts)
    except Exception:
        errors.append(traceback.format_exc())
        return finish_run(conn, run_id, "failed", errors, notes, **counts)
