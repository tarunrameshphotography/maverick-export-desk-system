import pytest

from radar import settings
from radar.content import drafts
from radar.content.generation import (
    VISUAL_SPLIT,
    create_and_generate_bundle,
    generate_bundle_prose,
)
from radar.content.drafts import ASSET_SLOTS, create_content_bundle
from radar.pipeline.angles import save_angle, select_angle
from radar.pipeline.verify import add_claim

NOW = "2026-09-14T09:00:00"
DISCLOSURE = settings.content_rules()["disclosure_line"]


def _seed(conn, category="incentives", schemes=("RoDTEP",), select=True, with_claim=True):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, primary_source_url,
                              created_at, updated_at)
        VALUES ('SIG-G-1', 'RoDTEP extension', ?, ?, 'SCORED', 'https://dgft.gov.in/n74', ?, ?)
        """,
        (NOW, category, NOW, NOW),
    )
    for scheme in schemes:
        conn.execute(
            "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value) VALUES ('SIG-G-1','scheme',?,?)",
            (scheme.lower(), scheme),
        )
    conn.commit()
    if with_claim:
        add_claim(
            conn, "SIG-G-1", "RoDTEP rates extended to 30 September 2026", "fact",
            "https://dgft.gov.in/n74", "primary", NOW,
            source_quote="existing RoDTEP rates ... are extended till 30.09.2026", verified_by="analyst",
        )
    aid = save_angle(conn, "SIG-G-1", "Countdown", "timing_transition_risk", "Price October quotes as if RoDTEP is zero",
                     "The extension ends 30 September; nothing yet covers October shipments.", "claude_code", NOW)
    if select:
        select_angle(conn, aid)
    return "SIG-G-1"


def _clean_fake_generate(system_prompt: str, user_prompt: str) -> str:
    """A fake model that only ever repeats grounded facts/angle text back —
    stands in for a well-behaved LLM so tests don't hit the network. Long
    enough to satisfy the LinkedIn word-count band; uses no numbers except
    30 and 2026, both present in the seeded verified claim."""
    body = (
        "RoDTEP rates were extended to 30 September 2026, per the DGFT notification. "
        "The extension covers shipments made before that date and says nothing about October. " * 8
    ).strip()
    return f"{body}\n{DISCLOSURE}\n\n{VISUAL_SPLIT}\nA simple calendar graphic marking 30 September 2026."


def _fabricating_generate(system_prompt: str, user_prompt: str) -> str:
    """A misbehaving fake model that ignores the grounding instruction and
    invents a number never present in any verified claim — proves lint_draft
    actually catches a model that fabricates, not just that the wiring runs."""
    body = (
        "Exporters could lose up to 45% of their margin once RoDTEP lapses. " * 6
    ).strip()
    return f"{body}\n\n{VISUAL_SPLIT}\nA chart showing the 45% margin drop."


def test_generation_requires_verified_claims(conn):
    sid = _seed(conn, with_claim=False)
    draft_ids = create_content_bundle(conn, sid, NOW)
    with pytest.raises(ValueError, match="no verified claims"):
        generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_clean_fake_generate)


def test_generation_fills_every_asset_and_drops_placeholders(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_clean_fake_generate)
    assert set(results) == set(ASSET_SLOTS)
    for slot in ASSET_SLOTS:
        draft = conn.execute(
            "SELECT * FROM content_drafts WHERE id = ?", (results[slot]["draft_id"],)
        ).fetchone()
        assert "[WRITE" not in draft["body"]
        assert "[VERIFY" not in draft["body"]
        assert "[WRITE" not in (draft["visual_brief"] or "")
        assert draft["edited_by"] == "claude_content_engine"
        assert draft["version"] == 2  # scaffold was v1, generated prose is v2


def test_generation_saves_visual_brief_separately_from_body(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_clean_fake_generate)
    draft = conn.execute(
        "SELECT * FROM content_drafts WHERE id = ?", (results["linkedin_post_1"]["draft_id"],)
    ).fetchone()
    assert VISUAL_SPLIT not in draft["body"]
    assert "calendar graphic" in draft["visual_brief"]


def test_grounded_generation_passes_lint_clean(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_clean_fake_generate)
    # LinkedIn posts (the only slots lint word-counts) should be clean.
    for slot in ("linkedin_post_1", "linkedin_post_2", "linkedin_post_3"):
        assert results[slot]["lint_issues"] == [], results[slot]["lint_issues"]


def test_lint_catches_a_fabricated_number_the_model_invented(conn):
    # Plants the exact defect the grounding rule exists to prevent: a model
    # that states a number no verified claim supports. If this ever stops
    # failing, the guard has gone vacuous.
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_fabricating_generate)
    issues = results["linkedin_post_1"]["lint_issues"]
    assert any("45%" in i for i in issues)


def test_fabricated_content_cannot_be_approved(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=_fabricating_generate)
    with pytest.raises(ValueError, match="checklist"):
        drafts.set_draft_status(conn, results["linkedin_post_1"]["draft_id"], "approved", "founder", NOW,
                                external_check_by="CA")


def test_linkedin_variants_get_distinct_prompts(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    seen_prompts = []

    def recording_generate(system_prompt, user_prompt):
        seen_prompts.append(user_prompt)
        return _clean_fake_generate(system_prompt, user_prompt)

    generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=recording_generate)
    linkedin_prompts = [p for p in seen_prompts if "LinkedIn post variant" in p]
    assert len(linkedin_prompts) == 3
    assert len(set(linkedin_prompts)) == 3  # each variant's prompt is genuinely different
    assert "Consequence first" in linkedin_prompts[0]
    assert "Fine print" in linkedin_prompts[1]
    assert "Action window" in linkedin_prompts[2]


def test_generation_grounds_prompt_in_verified_claim_text_and_source_quote(conn):
    sid = _seed(conn)
    draft_ids = create_content_bundle(conn, sid, NOW)
    seen = {}

    def recording_generate(system_prompt, user_prompt):
        seen["system"] = system_prompt
        seen.setdefault("users", []).append(user_prompt)
        return _clean_fake_generate(system_prompt, user_prompt)

    generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=recording_generate)
    fact_prompts = [p for p in seen["users"] if "FACTS" in p]  # excludes the visual-direction prompt
    assert len(fact_prompts) == 7
    assert all("RoDTEP rates extended to 30 September 2026" in p for p in fact_prompts)
    assert all("are extended till 30.09.2026" in p for p in fact_prompts)  # the machine-checked source quote
    assert "not literally present" in seen["system"]


def test_disclosure_line_instruction_present_for_red_tier_signal(conn):
    sid = _seed(conn)  # RoDTEP -> disclosure required
    draft_ids = create_content_bundle(conn, sid, NOW)
    seen_prompts = []

    def recording_generate(system_prompt, user_prompt):
        seen_prompts.append(user_prompt)
        return _clean_fake_generate(system_prompt, user_prompt)

    results = generate_bundle_prose(conn, sid, draft_ids, NOW, generate_fn=recording_generate)
    assert any(DISCLOSURE in p for p in seen_prompts)  # the instruction was actually given, not just followed
    for slot in ("linkedin_post_1", "instagram_caption"):
        draft = conn.execute(
            "SELECT body FROM content_drafts WHERE id = ?", (results[slot]["draft_id"],)
        ).fetchone()
        assert DISCLOSURE in draft["body"]


def test_create_and_generate_bundle_is_the_single_entry_point(conn):
    sid = _seed(conn)
    results = create_and_generate_bundle(conn, sid, NOW, generate_fn=_clean_fake_generate)
    assert set(results) == set(ASSET_SLOTS)
    for slot in ASSET_SLOTS:
        assert conn.execute(
            "SELECT status FROM content_drafts WHERE id = ?", (results[slot]["draft_id"],)
        ).fetchone()["status"] == "edited"


def test_generation_requires_a_selected_angle(conn):
    sid = _seed(conn, select=False)
    with pytest.raises(ValueError, match="no such signal|no selected angle"):
        create_and_generate_bundle(conn, sid, NOW, generate_fn=_clean_fake_generate)
