"""Command-line interface: `python -m radar <command>`. Thin wrappers only —
all logic lives in the modules so it stays testable."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

from radar import runner, settings
from radar.content import drafts, generation
from radar.db.connection import get_connection, init_db, seed_sources
from radar.desk import approvals, calls_ledger, performance
from radar.desk.desk_sheet import write_desk_sheet
from radar.pipeline import angles, evidence, verify
from radar.pipeline.exposure import IndianExposure
from radar.pipeline.orchestrator import rescore_signal
from radar.runlog import now_iso


def _conn():
    conn = get_connection()
    init_db(conn)
    return conn


def _read_text_arg(value: str) -> str:
    """Accepts literal text or @path/to/file."""
    return Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value


# ---------------------------------------------------------------- runs
def cmd_init(a):
    conn = get_connection()
    applied = init_db(conn)
    n = seed_sources(conn)
    print(f"Database ready at {settings.DB_PATH}. Sources registered: {n}. Migrations applied now: {applied or 'none'}")


def cmd_run(a):
    row = runner.run(_conn(), a.mode, use_sample=a.sample,
                     reference_date=date.fromisoformat(a.date) if a.date else None)
    errors = json.loads(row["errors_json"] or "[]")
    print(f"Run #{row['id']} ({row['run_type']}) {row['status'].upper()}")
    for key in ("sources_checked", "items_collected", "duplicates_found", "items_discarded",
                "high_score_signals", "verification_failures", "model_calls"):
        print(f"  {key}: {row[key]}")
    print(f"  failed sources / errors: {len(errors)}")
    for e in errors:
        print(f"    - {e.strip().splitlines()[-1][:160]}")
    if row["desk_sheet_id"]:
        print(f"  Desk Sheet: {row['desk_sheet_id']}")
    return 0 if row["status"] == "completed" else 1


def cmd_desk(a):
    _, path = write_desk_sheet(_conn(), date.fromisoformat(a.date) if a.date else None)
    print(Path(path).read_text(encoding="utf-8"))
    print(f"\n(written to {path})")


def cmd_runs(a):
    for r in _conn().execute("SELECT * FROM system_runs ORDER BY id DESC LIMIT ?", (a.limit,)).fetchall():
        errs = len(json.loads(r["errors_json"] or "[]"))
        print(f"#{r['id']} {r['run_type']:<18} {r['status']:<9} {r['started_at']}  sources={r['sources_checked']} "
              f"new={r['items_collected']} high={r['high_score_signals']} errors={errs}")


# ---------------------------------------------------------------- inspect
def cmd_signals(a):
    q = "SELECT * FROM signals"
    params: list = []
    if a.decision:
        q += " WHERE decision = ?"
        params.append(a.decision)
    q += " ORDER BY score_final DESC LIMIT ?"
    params.append(a.limit)
    for s in _conn().execute(q, params).fetchall():
        print(f"{s['id']}  {s['score_final'] or 0:>6.1f}  {s['decision'] or '-':<9} {s['status']:<16} {s['title'][:90]}")


def cmd_why(a):
    """Section 29: "Why did the system choose this story?" must have an answer."""
    conn = _conn()
    s = conn.execute("SELECT * FROM signals WHERE id = ?", (a.signal_id,)).fetchone()
    if s is None:
        sys.exit(f"No such signal: {a.signal_id}")
    b = json.loads(s["score_breakdown_json"] or "{}")
    weights = {c["id"]: c["weight"] for c in settings.scoring_weights()["criteria"]}
    print(f"{s['id']} — {s['title']}\nstatus {s['status']} · decision {s['decision']} · category {s['topic_category']}\n")
    print("GATES:", "all passed" if b.get("gates", {}).get("passed") else f"FAILED {b.get('gates', {}).get('failed')}")
    print("\nCRITERIA (score 0-5 x weight -> points)")
    for cid, score in b.get("criteria", {}).items():
        print(f"  {cid:<18} {score:>3.0f}/5 x {weights.get(cid, 0):>2} -> {weights.get(cid, 0) * score / 5:5.1f}")
    print(f"\nbase {b.get('base_score')} x confidence {b.get('confidence_mult')} "
          f"- penalties {b.get('penalties_total')} {b.get('penalties_applied')} = FINAL {b.get('final_score')}")
    print(f"coverage: {s['saturation_label']} ({s['saturation_count_72h']} matching items / 72h)")
    if s["exposure_json"]:
        e = IndianExposure.from_json(s["exposure_json"])
        print("\nINDIAN EXPOSURE")
        for k, v in e.__dict__.items():
            if k != "notes":
                print(f"  {k}: {v}")
        for n in e.notes:
            print(f"  note: {n}")
    print("\nENTITIES")
    for r in conn.execute("SELECT entity_type, normalized_value FROM signal_entities WHERE signal_id = ?", (s["id"],)):
        print(f"  {r['entity_type']}: {r['normalized_value']}")
    print("\nRAW ITEMS IN THIS SIGNAL")
    for r in conn.execute(
        "SELECT ri.title, ri.url, s.name, s.kind FROM raw_items ri JOIN sources s ON s.id = ri.source_id WHERE ri.signal_id = ?",
        (s["id"],),
    ):
        print(f"  [{r['kind']}] {r['name']}: {r['title'][:90]}\n      {r['url']}")
    print("\nAPPROVAL HISTORY")
    for h in approvals.approval_history(conn, s["id"]):
        print(f"  {h['decided_at']} {h['from_status']} -> {h['to_status']} by {h['reviewer']} {h['reason_code'] or ''}")


# ---------------------------------------------------------------- research gates
def _rescore_and_report(conn, signal_id):
    before = conn.execute("SELECT score_final, decision FROM signals WHERE id = ?", (signal_id,)).fetchone()
    after = rescore_signal(conn, signal_id, now_iso())["score"]
    print(f"Rescored {signal_id}: {before['score_final']} ({before['decision']}) -> "
          f"{after['final_score']} ({after['decision']}) · confidence x{after['confidence_mult']}")


def cmd_rescore(a):
    _rescore_and_report(_conn(), a.signal_id)


def cmd_fetch(a):
    """Section 26: fetch and preserve the primary document itself."""
    conn = _conn()
    for doc in evidence.fetch_signal_evidence(conn, a.signal_id, now_iso()):
        if doc["status"] == "stored":
            print(f"STORED  {doc['chars']:,} chars -> {doc['path']}\n        {doc['url']}")
            text = evidence.stored_text(conn, a.signal_id, doc["url"]) or ""
            for line in evidence.excerpts(text, verify.CLAIM_WORTHY_PATTERN, limit=a.excerpts):
                print(f"    · {line}")
        else:
            print(f"{doc['status'].upper():<8}{doc['url']}\n        {doc['note']}")


def cmd_verify(a):
    conn = _conn()
    verify.build_claims_table(conn, a.signal_id)
    print(verify.render_verification_worksheet(conn, a.signal_id))


def cmd_claim_mark(a):
    conn = _conn()
    verify.mark_claim(conn, a.claim_id, a.status, now_iso(), claim_type=a.type,
                      source_quote=_read_text_arg(a.quote) if a.quote else None, verified_by=a.by, notes=a.notes)
    print(f"Claim #{a.claim_id} -> {a.status}")
    _rescore_and_report(conn, conn.execute("SELECT signal_id FROM claims WHERE id = ?", (a.claim_id,)).fetchone()[0])


def cmd_claim_add(a):
    conn = _conn()
    cid = verify.add_claim(conn, a.signal_id, a.text, a.type, a.url, a.source_type, now_iso(),
                           source_title=a.title, source_date=a.source_date,
                           source_quote=_read_text_arg(a.quote) if a.quote else None, verified_by=a.by)
    print(f"Claim #{cid} added")
    _rescore_and_report(conn, a.signal_id)


def cmd_angle_brief(a):
    print(angles.render_angle_brief(_conn(), a.signal_id))


def cmd_angle_add(a):
    aid = angles.save_angle(_conn(), a.signal_id, a.franchise, a.pattern, a.label, _read_text_arg(a.text), a.by, now_iso())
    print(f"Angle #{aid} saved (not selected — a human selects with `angle-select`)")


def cmd_angle_select(a):
    conn = _conn()
    angles.select_angle(conn, a.analysis_id)
    print(f"Angle #{a.analysis_id} selected")
    _rescore_and_report(conn, conn.execute("SELECT signal_id FROM analyses WHERE id = ?", (a.analysis_id,)).fetchone()[0])


def cmd_status(a):
    approvals.transition_signal_status(_conn(), a.signal_id, a.to_status, a.by, now_iso(), reason_code=a.reason)
    print(f"{a.signal_id} -> {a.to_status}")


# ---------------------------------------------------------------- drafting gates
def cmd_draft(a):
    conn = _conn()
    did = drafts.create_draft_scaffold(conn, a.signal_id, a.channel, now_iso())
    print(drafts.render_draft_brief(conn, did))


def cmd_content_bundle(a):
    conn = _conn()
    draft_ids = drafts.create_content_bundle(conn, a.signal_id, now_iso())
    print(drafts.render_bundle_brief(conn, draft_ids))


def cmd_content_generate(a):
    conn = _conn()
    draft_ids = drafts.create_content_bundle(conn, a.signal_id, now_iso())
    results = generation.generate_bundle_prose(conn, a.signal_id, draft_ids, now_iso())
    dirty = 0
    for slot in drafts.ASSET_SLOTS:
        r = results[slot]
        status = "clean" if not r["lint_issues"] else f"{len(r['lint_issues'])} lint issue(s)"
        print(f"- {slot}: draft #{r['draft_id']} — {status}")
        for issue in r["lint_issues"]:
            print(f"    ! {issue}")
        dirty += bool(r["lint_issues"])
    print(f"\n{len(drafts.ASSET_SLOTS) - dirty}/{len(drafts.ASSET_SLOTS)} assets passed lint clean; "
          "review before approving any of them.")
    return 1 if dirty else 0


def cmd_draft_edit(a):
    changes = {k: _read_text_arg(v) for k, v in
               (("headline", a.headline), ("body", a.body), ("exposure_line", a.exposure_line),
                ("visual_brief", a.visual_brief), ("suggested_first_comment", a.first_comment)) if v}
    new_id = drafts.save_draft_version(_conn(), a.draft_id, now_iso(), a.by, a.reason or [], **changes)
    print(f"Saved as draft #{new_id}")


def cmd_draft_lint(a):
    issues = drafts.lint_draft(_conn(), a.draft_id)
    print("Checklist clean." if not issues else "\n".join(f"- {i}" for i in issues))
    return 1 if issues else 0


def cmd_draft_status(a):
    drafts.set_draft_status(_conn(), a.draft_id, a.status, a.by, now_iso(), reason_code=a.reason,
                            external_check_by=a.external_check)
    print(f"Draft #{a.draft_id} -> {a.status}")


def cmd_published(a):
    pid = drafts.record_publication(_conn(), a.draft_id, a.url, _read_text_arg(a.text), a.at or now_iso(), a.commercial)
    print(f"Recorded publication #{pid} (logged only — this system cannot post)")


# ---------------------------------------------------------------- ledger & learning
def cmd_call_add(a):
    cid = calls_ledger.add_call(_conn(), a.text, a.reasoning, a.source, a.made_on or date.today().isoformat(),
                                a.review_on, signal_id=a.signal_id, confidence_word=a.confidence,
                                expected_outcome=a.expected)
    print(f"Call #{cid} logged; review on {a.review_on}")


def cmd_call_grade(a):
    calls_ledger.grade_call(_conn(), a.call_id, a.grade, a.outcome, a.by, date.today().isoformat(), a.notes)
    print(f"Call #{a.call_id} graded {a.grade}")


def cmd_calls(a):
    conn = _conn()
    rows = calls_ledger.list_due_for_review(conn, date.today().isoformat()) if a.due else calls_ledger.list_open_calls(conn)
    for r in rows:
        print(f"#{r['id']} review {r['review_on']} [{r['confidence_word'] or '-'}] {r['call_text']}")
    print(f"hit rate: {calls_ledger.hit_rate(conn)}")


def cmd_metrics_import(a):
    print(performance.import_metrics_csv(_conn(), a.csv))


def cmd_learn(a):
    conn = _conn()
    lid = performance.propose_weight_changes(conn, a.period)
    if lid is None:
        print(f"Not enough evidence for a proposal (need {performance.MIN_POSTS_FOR_PROPOSAL}+ published posts with metrics "
              "and a clear difference between criteria).")
    else:
        r = conn.execute("SELECT * FROM learnings WHERE id = ?", (lid,)).fetchone()
        print(f"Proposal #{lid}: {r['finding']}\n  {r['action']}\n  Accept or reject with: learn-decide {lid} --accept/--reject")
    print(f"Rejection reasons so far: {approvals.rejection_reason_breakdown(conn)}")


def cmd_learn_decide(a):
    performance.decide_learning(_conn(), a.learning_id, a.accept)
    print("Recorded. If accepted, edit radar/config/scoring_weights.yaml to apply it.")


# ---------------------------------------------------------------- scheduling
def cmd_schedule(a):
    cfg = yaml.safe_load((settings.CONFIG_DIR / "schedule.yaml").read_text(encoding="utf-8"))
    python = sys.executable
    commands = []
    for r in cfg["runs"]:
        action = f'cmd /c cd /d "{settings.PROJECT_ROOT}" && "{python}" -m radar run {r["mode"]} >> data\\runs\\scheduler.log 2>&1'
        commands.append(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", r["time"], "/TN", r["name"], "/TR", action])
    for c in commands:
        print(subprocess.list2cmdline(c))
    if a.install:
        for c in commands:
            subprocess.run(c, check=True)
        print(f"Registered {len(commands)} Windows scheduled tasks.")
    else:
        print("\nDry run. Re-run with --install to register these tasks.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m radar", description="Maverick Minds Export Signal Radar")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "create/migrate the database and register sources")
    sp = add("run", cmd_run, "run a scheduled mode now")
    sp.add_argument("mode", choices=sorted(runner.RUN_TYPES))
    sp.add_argument("--sample", action="store_true", help="use the offline sample fixture instead of live sources")
    sp.add_argument("--date", help="reference date for deadlines (YYYY-MM-DD)")
    sp = add("desk", cmd_desk, "re-render today's Desk Sheet from the database")
    sp.add_argument("--date")
    sp = add("runs", cmd_runs, "recent pipeline runs")
    sp.add_argument("--limit", type=int, default=10)

    sp = add("signals", cmd_signals, "list scored signals")
    sp.add_argument("--decision", choices=["lead", "secondary", "watchlist", "discard"])
    sp.add_argument("--limit", type=int, default=25)
    sp = add("why", cmd_why, "explain how a signal was scored")
    sp.add_argument("signal_id")

    sp = add("rescore", cmd_rescore, "re-score a signal after new evidence or angle work")
    sp.add_argument("signal_id")
    sp = add("fetch", cmd_fetch, "fetch and store the signal's primary documents")
    sp.add_argument("signal_id")
    sp.add_argument("--excerpts", type=int, default=10, help="claim-worthy sentences to print per document")
    sp = add("verify", cmd_verify, "print the verification worksheet")
    sp.add_argument("signal_id")
    sp = add("claim-mark", cmd_claim_mark, "verify/fail a claim (verified requires --quote)")
    sp.add_argument("claim_id", type=int)
    sp.add_argument("status", choices=sorted(verify.VALID_STATUSES))
    sp.add_argument("--type", choices=sorted(verify.VALID_CLAIM_TYPES))
    sp.add_argument("--quote", help="exact source passage, or @file")
    sp.add_argument("--by", default="analyst")
    sp.add_argument("--notes")
    sp = add("claim-add", cmd_claim_add, "add a claim found during research")
    sp.add_argument("signal_id")
    sp.add_argument("--text", required=True)
    sp.add_argument("--type", required=True, choices=sorted(verify.VALID_CLAIM_TYPES))
    sp.add_argument("--url", required=True)
    sp.add_argument("--source-type", required=True, choices=["primary", "secondary"])
    sp.add_argument("--title")
    sp.add_argument("--source-date")
    sp.add_argument("--quote")
    sp.add_argument("--by", default="analyst")

    sp = add("angle-brief", cmd_angle_brief, "print the Angle Engine brief")
    sp.add_argument("signal_id")
    sp = add("angle-add", cmd_angle_add, "save one angle option")
    sp.add_argument("signal_id")
    sp.add_argument("--franchise", required=True, choices=angles.FRANCHISES)
    sp.add_argument("--pattern", required=True, choices=angles.ANGLE_PATTERNS)
    sp.add_argument("--label", required=True)
    sp.add_argument("--text", required=True, help="angle text, or @file")
    sp.add_argument("--by", default="claude_code", choices=["human", "claude_code"])
    sp = add("angle-select", cmd_angle_select, "HUMAN GATE: pick the angle")
    sp.add_argument("analysis_id", type=int)
    sp = add("status", cmd_status, "move a signal through the approval state machine")
    sp.add_argument("signal_id")
    sp.add_argument("to_status", choices=sorted(approvals.VALID_STATUSES))
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", choices=sorted(approvals.REASON_CODES))

    sp = add("draft", cmd_draft, "create a LinkedIn/Instagram draft scaffold + writing brief")
    sp.add_argument("signal_id")
    sp.add_argument("--channel", choices=["linkedin", "instagram"], default="linkedin")
    sp = add("content-bundle", cmd_content_bundle,
             "create the full Phase 2 content bundle scaffold (3 LinkedIn + 2 Reels + caption + carousel + visual direction)")
    sp.add_argument("signal_id")
    sp = add("content-generate", cmd_content_generate,
             "create AND write the full Phase 2 content bundle (calls the Anthropic API; needs ANTHROPIC_API_KEY)")
    sp.add_argument("signal_id")
    sp = add("draft-edit", cmd_draft_edit, "save a new draft version (text or @file)")
    sp.add_argument("draft_id", type=int)
    for f in ("--headline", "--body", "--exposure-line", "--visual-brief", "--first-comment"):
        sp.add_argument(f)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", action="append", help="edit reason code (repeatable)")
    sp = add("draft-lint", cmd_draft_lint, "run the pre-publish checklist")
    sp.add_argument("draft_id", type=int)
    sp = add("draft-status", cmd_draft_status, "HUMAN GATE: approve or reject a draft")
    sp.add_argument("draft_id", type=int)
    sp.add_argument("status", choices=["approved", "rejected", "edited"])
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", choices=sorted(approvals.REASON_CODES))
    sp.add_argument("--external-check", help="partner CA / customs broker who checked (red tier)")
    sp = add("published", cmd_published, "log a post a human already published")
    sp.add_argument("draft_id", type=int)
    sp.add_argument("--url", required=True)
    sp.add_argument("--text", required=True, help="final text, or @file")
    sp.add_argument("--at")
    sp.add_argument("--commercial", default="none", choices=["none", "leap", "advisory", "incentive"])

    sp = add("call-add", cmd_call_add, "log a forward-looking call in the Calls Ledger")
    sp.add_argument("--text", required=True)
    sp.add_argument("--reasoning", required=True)
    sp.add_argument("--source", required=True)
    sp.add_argument("--review-on", required=True)
    sp.add_argument("--made-on")
    sp.add_argument("--signal-id")
    sp.add_argument("--confidence", choices=sorted(calls_ledger.VALID_CONFIDENCE_WORDS))
    sp.add_argument("--expected")
    sp = add("call-grade", cmd_call_grade, "grade a call (never deletes it)")
    sp.add_argument("call_id", type=int)
    sp.add_argument("grade", choices=sorted(calls_ledger.VALID_GRADES))
    sp.add_argument("--outcome", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--notes")
    sp = add("calls", cmd_calls, "open calls (or --due for review)")
    sp.add_argument("--due", action="store_true")

    sp = add("metrics-import", cmd_metrics_import, "import a weekly metrics CSV")
    sp.add_argument("csv")
    sp = add("learn", cmd_learn, "propose scoring-weight changes from outcomes")
    sp.add_argument("--period", default=date.today().strftime("%Y-%m"))
    sp = add("learn-decide", cmd_learn_decide, "accept/reject a learning proposal")
    sp.add_argument("learning_id", type=int)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", dest="accept", action="store_true")
    g.add_argument("--reject", dest="accept", action="store_false")

    sp = add("schedule", cmd_schedule, "print (or --install) Windows Task Scheduler entries")
    sp.add_argument("--install", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except ValueError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
