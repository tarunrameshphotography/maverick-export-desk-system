# Phase 5 — Daily Content Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `content` scheduled run mode that automatically generates
and lints a full content bundle (Phase 2's `create_and_generate_bundle`) for
every signal a human has already moved to `READY_FOR_REVIEW` by selecting an
angle — so the analyst's next action is review/edit/approve instead of
starting from a blank scaffold — and register it in `schedule.yaml` /
`radar schedule --install` alongside the existing collection runs.

**Architecture:** `radar/runner.py` gets a new branch in `run()` for
`mode == "content"` that never touches collection or the scoring pipeline —
it only queries signals in `READY_FOR_REVIEW` with no `content_drafts` rows
yet, and calls `radar.content.generation.create_and_generate_bundle` once per
candidate, catching `ValueError` (expected precondition misses — no verified
claims, angle deselected between query and generation) per-signal and letting
anything else propagate to the existing top-level `except Exception` so the
whole run is marked `failed` and visible in Task Scheduler, exactly like
every other mode already does. No auto-approval, no auto-queueing: draft
status stays `'draft'`; `set_draft_status`/`publish_queue.enqueue` are never
called from this path.

**Tech Stack:** Python 3, sqlite3, existing `radar.content.drafts` /
`radar.content.generation` modules, pytest.

**Spec:** `INTELLIGENCE SYSTEM/roadmap.md` ("Phase 5 — Daily orchestrator"
and "Phase 5 done" under "What 'done' means for each remaining milestone");
`CLAUDE.md` (guardrails: no auto-approval, no auto-angle-selection, no
fabricated content).

## Global Constraints

- Never call `drafts.set_draft_status(..., "approved", ...)` or
  `publish_queue.enqueue` from the orchestrator — approval and queueing stay
  strictly human-initiated (CLAUDE.md: "founder-approved exception" covers
  *generation* only, not approval/queueing).
- Never auto-select an angle. Eligibility is gated purely on signals already
  in `READY_FOR_REVIEW` (the status `angles.select_angle` sets when a human
  selects an angle).
- A run must never raise out uncoded (`radar/runner.py`'s existing contract):
  expected per-signal misses are caught and recorded in `notes`; anything
  else still surfaces as a `failed` run, not swallowed.
- Run the full suite (`python -m pytest tests/ -q`) before and after — must
  stay at 270 passing plus whatever this plan adds, no regressions.
- Schema is unchanged (no migration needed) — `content_drafts.signal_id`
  and `signals.status` already exist.

---

## File Structure

- Modify `radar/runner.py`: add `"content": "content_generation"` to
  `RUN_TYPES`, add `_generate_content_for_ready_signals()` helper, add the
  `mode == "content"` branch in `run()`, thread an optional `generate_fn`
  parameter through `run()` for test injection (defaults to `None`, meaning
  "use the real Anthropic call" — same default-injection pattern already
  used by `generation.create_and_generate_bundle`).
- Modify `radar/config/schedule.yaml`: add one new scheduled run entry,
  `mode: content`, timed after the existing afternoon sweep so analysts have
  had a window to run `angle-select` on the day's candidates.
- Modify `tests/test_runner.py`: add tests for the new mode (candidates
  found and generated, signals without a selected angle or without verified
  claims are skipped and reported not crashed, bundles aren't regenerated on
  a second run, `radar schedule --install`'s dry-run print already covers
  the new entry generically so no test changes needed there — verified in
  Task 3).
- Modify `INTELLIGENCE SYSTEM/roadmap.md` and `INTELLIGENCE SYSTEM/milestones.md`:
  mark Phase 5 done, matching the existing Phase 3/4 write-up style.
- Modify `RADAR_README.md`: document the new `radar run content` command and
  update the schedule times line.

---

## Task 1: `_generate_content_for_ready_signals` helper + `content` run mode

**Files:**
- Modify: `radar/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `radar.content.drafts.create_content_bundle` (existing, raises
  `ValueError` if no selected angle), `radar.content.generation.create_and_generate_bundle(conn, signal_id, now_iso, generate_fn=None) -> dict[str, dict]`
  (existing; raises `ValueError` if no verified claims or no selected angle),
  `radar.pipeline.angles.select_angle` (test setup only).
- Produces: `runner.RUN_TYPES` gains key `"content"`; `runner.run(conn, "content", generate_fn=...)`
  returns the same `system_runs` row shape as every other mode, with
  `notes["candidates"]` (int), `notes["bundles_generated"]` (int),
  `notes["skipped"]` (list of `{"signal_id": str, "reason": str}`), and
  `counts["model_calls"]` set to the real number of generation calls made
  (`len(ASSET_SLOTS)` per bundle, i.e. 8).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_runner.py` (reuse the `_isolated_output_dirs` fixture
already in the file; add these imports at the top alongside the existing
ones: `from radar.content.drafts import ASSET_SLOTS, create_content_bundle`,
`from radar.content.generation import VISUAL_SPLIT`,
`from radar.pipeline.angles import save_angle, select_angle`,
`from radar.pipeline.verify import add_claim`):

```python
NOW_CONTENT = "2026-09-15T19:00:00"
CONTENT_DISCLOSURE_FREE_CATEGORY = "logistics"


def _seed_ready_signal(conn, signal_id="SIG-READY-1", with_claim=True, select=True):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, primary_source_url,
                              created_at, updated_at)
        VALUES (?, 'Container detention notice', ?, ?, 'SCORED', 'https://dgft.gov.in/n99', ?, ?)
        """,
        (signal_id, NOW_CONTENT, CONTENT_DISCLOSURE_FREE_CATEGORY, NOW_CONTENT, NOW_CONTENT),
    )
    conn.commit()
    if with_claim:
        add_claim(
            conn, signal_id, "Detention-free period extended to 10 days", "fact",
            "https://dgft.gov.in/n99", "primary", NOW_CONTENT,
            source_quote="the detention-free period is extended to 10 days", verified_by="analyst",
        )
    aid = save_angle(
        conn, signal_id, "Countdown", "timing_transition_risk",
        "Ten days sounds generous until you count loading delays",
        "The extension helps only if the container actually moves within the window.",
        "claude_code", NOW_CONTENT,
    )
    if select:
        select_angle(conn, aid)
    return signal_id


def _fake_generate(system_prompt: str, user_prompt: str) -> str:
    body = (
        "The detention-free period is extended to 10 days, per the DGFT notice. "
        "This helps only if loading finishes inside that window. " * 8
    ).strip()
    return f"{body}\n\n{VISUAL_SPLIT}\nA simple countdown graphic showing the 10-day window."


def test_content_run_generates_bundles_for_ready_signals(conn):
    sid = _seed_ready_signal(conn)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    assert notes["candidates"] == 1
    assert notes["bundles_generated"] == 1
    assert notes["skipped"] == []
    assert row["model_calls"] == len(ASSET_SLOTS)
    drafts_for_signal = conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE signal_id = ?", (sid,)
    ).fetchone()["n"]
    assert drafts_for_signal == len(ASSET_SLOTS)


def test_content_run_skips_signal_without_selected_angle(conn):
    _seed_ready_signal(conn, select=False)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    # not selected -> status never moved to READY_FOR_REVIEW -> not even a candidate
    assert notes["candidates"] == 0
    assert notes["bundles_generated"] == 0


def test_content_run_skips_signal_without_verified_claims_and_records_why(conn):
    sid = _seed_ready_signal(conn, with_claim=False)
    row = runner.run(conn, "content", generate_fn=_fake_generate)
    assert row["status"] == "completed"
    notes = json.loads(row["notes"])
    assert notes["candidates"] == 1
    assert notes["bundles_generated"] == 0
    assert len(notes["skipped"]) == 1
    assert notes["skipped"][0]["signal_id"] == sid
    assert "no verified claims" in notes["skipped"][0]["reason"]


def test_content_run_does_not_regenerate_an_existing_bundle(conn):
    sid = _seed_ready_signal(conn)
    first = runner.run(conn, "content", generate_fn=_fake_generate)
    assert json.loads(first["notes"])["bundles_generated"] == 1
    second = runner.run(conn, "content", generate_fn=_fake_generate)
    notes = json.loads(second["notes"])
    assert notes["candidates"] == 0
    assert notes["bundles_generated"] == 0
    drafts_for_signal = conn.execute(
        "SELECT COUNT(*) AS n FROM content_drafts WHERE signal_id = ?", (sid,)
    ).fetchone()["n"]
    assert drafts_for_signal == len(ASSET_SLOTS)  # still just the one bundle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runner.py -k content_run -v`
Expected: FAIL — `runner.run` doesn't accept `generate_fn`, `"content"` is
not in `RUN_TYPES`, so this raises `ValueError: Unknown mode 'content'`.

- [ ] **Step 3: Implement the helper and the new mode**

In `radar/runner.py`, add the import and update `RUN_TYPES`:

```python
from radar.content import generation as content_generation

RUN_TYPES = {
    "overnight": "overnight_collect",
    "morning": "morning_pipeline",
    "evening": "evening_sweep",
    "content": "content_generation",
}
```

Add the helper function (place it after `_prepare_candidates_for_research`):

```python
def _generate_content_for_ready_signals(
    conn: sqlite3.Connection, now_iso_str: str, generate_fn=None
) -> dict:
    """Step 8 (mechanical half): every signal a human has already moved to
    READY_FOR_REVIEW by selecting an angle (angles.select_angle sets this
    status) gets its content bundle generated and linted automatically, so
    the human's next action is review/edit/approve, not a blank scaffold.
    Never touches draft status or the publishing queue — both still require
    a human (CLAUDE.md's hybrid-model guardrails)."""
    candidate_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM signals WHERE status = 'READY_FOR_REVIEW' "
            "AND id NOT IN (SELECT DISTINCT signal_id FROM content_drafts)"
        ).fetchall()
    ]
    bundles_generated = 0
    model_calls = 0
    skipped: list[dict] = []
    for signal_id in candidate_ids:
        try:
            results = content_generation.create_and_generate_bundle(
                conn, signal_id, now_iso_str, generate_fn=generate_fn
            )
            model_calls += len(results)
            bundles_generated += 1
        except ValueError as exc:
            skipped.append({"signal_id": signal_id, "reason": str(exc)})
    return {
        "candidates": len(candidate_ids),
        "bundles_generated": bundles_generated,
        "model_calls": model_calls,
        "skipped": skipped,
    }
```

Update `run()`'s signature and add the branch (insert right after
`errors: list[str] = []` and before the existing `collection = run_collection(...)`
line):

```python
def run(
    conn: sqlite3.Connection,
    mode: str,
    use_sample: bool = False,
    reference_date: date | None = None,
    generate_fn=None,
) -> dict:
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
            result = _generate_content_for_ready_signals(conn, now_iso(), generate_fn=generate_fn)
            notes["candidates"] = result["candidates"]
            notes["bundles_generated"] = result["bundles_generated"]
            notes["skipped"] = result["skipped"]
            counts["model_calls"] = result["model_calls"]
            return finish_run(conn, run_id, "completed", errors, notes, **counts)

        collection = run_collection(conn, run_id, use_sample=use_sample)
        ...  # rest of the existing function body is unchanged
```

(Leave every line below the `collection = run_collection(...)` call exactly
as it is today — only the new `if mode == "content":` block above it is new.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runner.py -v`
Expected: PASS — all new `test_content_run_*` tests plus every pre-existing
`test_runner.py` test (overnight/morning/evening) still pass unchanged.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS, 270 + 4 = 274 tests (or more if other suites also grew),
zero regressions.

- [ ] **Step 6: Commit**

```bash
git add radar/runner.py tests/test_runner.py
git commit -m "Add content-generation run mode for Phase 5's daily orchestrator

Generates and lints content bundles for signals already moved to
READY_FOR_REVIEW by a human angle selection, without auto-approving or
auto-queueing anything (both still require an explicit human step)."
```

---

## Task 2: Register the new run in `schedule.yaml` and verify `radar schedule` output

**Files:**
- Modify: `radar/config/schedule.yaml`
- Test: manual CLI smoke test against a scratch DB (no new automated test —
  `cmd_schedule` already renders `cfg["runs"]` generically; there is no
  existing test file for `cmd_schedule` to extend, and adding one just to
  assert a YAML round-trip would test PyYAML, not our logic)

**Interfaces:**
- Consumes: nothing new — `radar.cli.cmd_schedule` already reads
  `schedule.yaml`'s `runs` list generically (`name`, `mode`, `time` per
  entry) and builds one Windows Scheduled Task per entry.
- Produces: one new entry in `schedule.yaml`'s `runs` list with
  `mode: content`.

- [ ] **Step 1: Edit `radar/config/schedule.yaml`**

Add a new entry after `MaverickRadar_Afternoon` and before
`MaverickRadar_Evening`, and update the file's header comment:

```yaml
# Run schedule (Section 27 / daily_research_system.md "The day at the Desk", IST).
# Times are local machine time. Change them here; re-run `python -m radar schedule --install`.
runs:
  - name: MaverickRadar_Overnight
    mode: overnight      # collect only: overnight US/EU publications, late DGFT/CBIC uploads
    time: "05:30"
  - name: MaverickRadar_Morning
    mode: morning        # collect + dedupe + score + claims scaffolding + Desk Sheet
    time: "06:30"
  - name: MaverickRadar_Afternoon
    mode: evening        # second sweep: afternoon DGFT/CBIC uploads, EU morning publications
    time: "16:00"
  - name: MaverickRadar_ContentGen
    mode: content        # Phase 5: generate + lint content bundles for signals a human
                          # has already moved to READY_FOR_REVIEW via angle-select earlier
                          # in the day. Never approves or queues anything.
    time: "19:00"
  - name: MaverickRadar_Evening
    mode: evening        # late DGFT/CBIC check; refreshes the Watchlist for tomorrow
    time: "21:00"
```

- [ ] **Step 2: Smoke-test the dry-run print**

Run: `python -m radar schedule` (from the project root; uses the real
`radar/config/schedule.yaml`, not a scratch DB — this command doesn't touch
the database at all, only prints/registers OS scheduled tasks)
Expected: prints 5 lines, one of them showing the `MaverickRadar_ContentGen`
task running `python -m radar run content` at 19:00, in the same format as
the other four entries.

- [ ] **Step 3: Commit**

```bash
git add radar/config/schedule.yaml
git commit -m "Schedule the content-generation run alongside collection (Phase 5)"
```

---

## Task 3: Live smoke test on a scratch DB, then update docs

**Files:**
- Read: none new
- Modify: `INTELLIGENCE SYSTEM/roadmap.md`, `INTELLIGENCE SYSTEM/milestones.md`, `RADAR_README.md`

**Interfaces:**
- Consumes: `RADAR_DB_PATH` env var (already supported by `radar/settings.py`
  per CLAUDE.md's documented workflow) to point the CLI at a throwaway
  SQLite file instead of the real desk database.
- Produces: no new interfaces — this task is verification + documentation.

- [ ] **Step 1: Walk the mechanical half end-to-end on a scratch DB**

Run each of these in order (PowerShell; adjust path separators if using
Bash), from the project root, all pointed at one scratch DB path so state
carries across commands:

```
$env:RADAR_DB_PATH = "C:\WORK FILES\Maverick Minds\MAVERICK EXPORT DESK SYSTEM\data\scratch_phase5.db"
python -m radar init
python -m radar run morning --sample --date 2026-09-15
```

Note one signal id from the printed Desk Sheet (or query
`python -c "..."` against the scratch DB for a row with
`status='NEEDS_RESEARCH'`), call it `<SID>`, then:

```
python -m radar claim-mark <SID>          # or whatever claim workflow the Desk Sheet calls for
python -m radar angle-add <SID> --franchise Countdown --pattern timing_transition_risk --label "..." "..."
python -m radar angle-select <analysis_id from angle-add's output>
python -m radar run content
python -m radar signals --decision lead   # confirm <SID> now has content_drafts rows via a direct query
```

Confirm: the `run content` output reports 1 candidate, 1 bundle generated,
0 skipped (or documents why if the seeded signal lacks a verified claim —
adjust the manual `claim-add`/`claim-mark` steps accordingly so it has one);
confirm no draft's status became `approved` and no `publish_queue` row was
created (`python -m radar queue` should show nothing new).

- [ ] **Step 2: Delete the scratch DB**

```
Remove-Item "$env:RADAR_DB_PATH" -ErrorAction SilentlyContinue
Remove-Item Env:\RADAR_DB_PATH
```

- [ ] **Step 3: Update `INTELLIGENCE SYSTEM/roadmap.md`**

Change the "Dependencies" paragraph's sentence "Phase 5 depends on Phase 2
... and benefits from Phase 3 existing first (it now does)." — leave it, it
is still accurate — and add a new subsection right after the existing
"Phase 4 done" bullet under "What 'done' means for each remaining
milestone", replacing the now-stale "Phase 5 done:" bullet with:

```markdown
- **Phase 5 done (2026-09-15):** `radar run content` and `radar schedule
  --install` (via a new `MaverickRadar_ContentGen` entry in
  `config/schedule.yaml`) automatically generate and lint a full content
  bundle for every signal a human has already moved to `READY_FOR_REVIEW`
  by selecting an angle (`angle-select`). It never auto-approves a draft or
  places anything in the publishing queue — both remain explicit human
  actions via `draft-status approved` and `queue-add`/`queue-schedule`, as
  required by CLAUDE.md's hybrid-model guardrails. Tested in
  `tests/test_runner.py` (candidate generation, skipping signals without a
  selected angle or without verified claims, and idempotency — a signal
  that already has a bundle is not regenerated on a later run) and walked
  end-to-end on a scratch DB.
```

- [ ] **Step 4: Update `INTELLIGENCE SYSTEM/milestones.md`**

Find the line `- Phase 5: orchestrator scheduling for content generation/queue placement`
and replace it with:

```markdown
- Phase 5: DONE (2026-09-15) — orchestrator scheduling for content
  generation only (not queue placement: queueing an approved draft still
  requires a human, since `publish_queue.enqueue` requires
  `status='approved'` and nothing in this codebase auto-approves a draft).
  See roadmap.md's "Phase 5 done" entry.
```

- [ ] **Step 5: Update `RADAR_README.md`**

Add a row to the command table near the existing morning-run row:

```markdown
| Content generation run (fills + lints bundles for signals already at READY_FOR_REVIEW) | `python -m radar run content` |
```

And update the schedule line to:

```markdown
| Schedule (05:30, 06:30, 16:00, 19:00, 21:00) | `python -m radar schedule` (dry run) → `--install` to register Windows tasks |
```

- [ ] **Step 6: Run the full suite one more time**

Run: `python -m pytest tests/ -q`
Expected: PASS, same count as Task 1's Step 5 (docs changes don't affect
tests).

- [ ] **Step 7: Commit**

```bash
git add "INTELLIGENCE SYSTEM/roadmap.md" "INTELLIGENCE SYSTEM/milestones.md" RADAR_README.md
git commit -m "Document Phase 5 (daily content orchestrator): built and tested, generation-only by design"
```

---

## Self-Review Notes

- **Spec coverage:** roadmap.md's "Phase 5 done" criterion ("radar schedule
  --install registers tasks that also generate and queue content, with the
  same dry-run-by-default safety as today's collection scheduling") is
  covered by Tasks 1-2, with the "queue" half deliberately scoped down to
  generation-only per the founder's explicit choice recorded in this
  session (queueing needs human approval first; automating past that would
  be exactly the kind of "further loosening" CLAUDE.md says needs its own
  sign-off, not an implementation detail decided mid-plan).
- **Placeholder scan:** no TBD/TODO; every step has literal code or exact
  commands.
- **Type consistency:** `_generate_content_for_ready_signals` return dict
  keys (`candidates`, `bundles_generated`, `model_calls`, `skipped`) are
  used identically in `run()`'s new branch and in every test assertion.
