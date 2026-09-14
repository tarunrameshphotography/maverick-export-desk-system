"""Phase 2 Content Engine: turns a scaffolded content bundle into genuinely
finished, publish-ready copy.

This is the founder-approved exception to the hybrid-mode rule documented in
CLAUDE.md ("do not have code generate final prose unattended"). Every other
guardrail from that rule stays exactly as strict:

- Generation is grounded *only* in `claims` rows already marked
  status='verified' (verify.py's machine-checked-quote gate) and the
  signal's selected angle text. The prompt never hands the model anything
  else to draw "facts" from, and instructs it not to state any number,
  date, HS code or trade value that isn't in that material.
- Every generated asset is saved through the existing `save_draft_version`
  (full version history preserved) and immediately run through the
  existing `lint_draft` — including its untraceable-number check, which is
  exactly the mechanism that would catch a model that ignored the grounding
  instruction and invented a figure anyway. Generation does not get to
  mark its own homework: `lint_draft`'s result is returned alongside every
  draft id so a caller can see, unedited, whether a "finished" asset is
  actually clean.
- `auto_publish`, `record_publication`, and the risk-tier/founder approval
  gates in `drafts.py::set_draft_status` are untouched. Nothing here can
  publish; a generated asset still has to pass the same human gate any
  hand-written one does.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Callable

from radar import settings
from radar.content import drafts

# (system_prompt, user_prompt) -> generated text. Injectable so tests never
# make a network call; radar.content.generation.default_generate_fn() is the
# real implementation used in production.
GenerateFn = Callable[[str, str], str]

VISUAL_SPLIT = "---VISUAL---"
DEFAULT_MODEL_ENV = "RADAR_CONTENT_MODEL"
DEFAULT_MODEL = "claude-sonnet-5"


def _anthropic_generate(system_prompt: str, user_prompt: str, model: str) -> str:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dep installed
        raise RuntimeError(
            "The 'anthropic' package is required for content generation. "
            "Install it with `pip install anthropic` (see requirements.txt)."
        ) from exc
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set (see .env.example) — required to generate finished content."
        )
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=1800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()


def default_generate_fn(model: str | None = None) -> GenerateFn:
    """The real generation function, wired to the Anthropic API. Not used in
    tests (they inject a fake GenerateFn) — only when actually generating."""
    chosen = model or os.environ.get(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    return lambda system_prompt, user_prompt: _anthropic_generate(system_prompt, user_prompt, chosen)


def _facts_block(verified_claims) -> str:
    if not verified_claims:
        return (
            "No verified claims are available. Do not write publishable copy — instead, return a single "
            "honest sentence saying there is not yet enough verified evidence for this signal."
        )
    lines = []
    for c in verified_claims:
        quote = f' Source quote: "{c["source_quote"]}".' if c["source_quote"] else ""
        lines.append(f"- {c['claim_text']}{quote} (source: {c['source_url'] or 'unspecified'})")
    return "\n".join(lines)


def _common_system_prompt(rules: dict) -> str:
    return (
        "You are the writer for Maverick Minds' Export Desk, a LinkedIn/Instagram account for Indian "
        "exporters and MSMEs. Brand thesis: \"First with the consequences, not first with the news.\" "
        "You explain what a trade/export/regulatory development actually means for an exporter, not just "
        "what happened.\n\n"
        "Hard rules, no exceptions:\n"
        "1. You may state as fact ONLY what appears in the FACTS block you are given. Never invent, round, "
        "estimate or infer a number, date, percentage, HS code, trade value, or regulatory detail that is "
        "not literally present there. If the facts are thin, write a shorter, more modest post rather than "
        "add anything.\n"
        "2. Keep interpretation (the ANGLE you are given) visibly distinct from fact — readers must be able "
        "to tell what is confirmed versus what Maverick Minds thinks it means.\n"
        "3. Never use these banned phrases or anything with the same hype register: "
        + "; ".join(rules["banned_phrases"]) + ".\n"
        "4. Output ONLY the finished asset text (plus the ---VISUAL--- section if asked for one). No "
        "preamble, no meta-commentary like 'Here is the post', no markdown headers.\n"
        "5. If a disclosure line is supplied below, include it verbatim, once, near the end.\n"
    )


def _linkedin_user_prompt(variant: int, verified_claims, analysis, disclosure, rules) -> str:
    label, guidance = drafts.LINKEDIN_VARIANT_EMPHASIS[variant]
    li = rules["linkedin"]
    return (
        f"Write LinkedIn post variant {variant} of 3 for the same underlying story: emphasis \"{label}\" — "
        f"{guidance}\n\n"
        f"FACTS (verified only):\n{_facts_block(verified_claims)}\n\n"
        f"ANGLE (Maverick Minds' interpretation, franchise \"{analysis['franchise'] or 'unassigned'}\"): "
        f"{analysis['angle_text']}\n\n"
        f"Requirements: {li['min_words']}-{li['max_words']} words. No exclamation marks. No emoji. End with "
        "a concrete action an exporter should take this week, tied to the angle above — not a generic "
        "call to 'stay informed'. Do not repeat the source URL in the body (it goes in the first LinkedIn "
        "comment separately).\n"
        + (f"\nDisclosure line to include verbatim near the end: {disclosure}" if disclosure else "")
        + f"\n\nReply with the finished post, then a line with exactly {VISUAL_SPLIT}, then one sentence "
        "describing what image or document treatment (if any) should accompany this specific post."
    )


def _reel_user_prompt(reel_number: int, verified_claims, analysis, disclosure, rules) -> str:
    ig = rules["instagram"]
    lo, hi = ig["reel_seconds"]
    concept = drafts.INSTAGRAM_REEL_CONCEPT[reel_number]
    return (
        f"Write Instagram Reel {reel_number} of 2 for the same story: {concept}, built around the angle "
        f"\"{analysis['angle_label']}\".\n\n"
        f"FACTS (verified only):\n{_facts_block(verified_claims)}\n\n"
        f"ANGLE: {analysis['angle_text']}\n\n"
        f"Format ({lo}-{hi} seconds, founder on camera, captions burned in). {ig['language']}. Write:\n"
        "- HOOK (first 3 seconds): a concrete question an MSME owner would actually ask. No income claims.\n"
        "- VO NOTES (3 short beats in English for the Tamil voice-over artist to read from): what changed, "
        "who it affects, what to do.\n"
        "- ON-SCREEN TEXT (English): the short phrases that appear as captions.\n"
        f"Never: {'; '.join(ig['never'])}.\n"
        + (f"\nDisclosure line to include verbatim near the end: {disclosure}" if disclosure else "")
        + f"\n\nReply with the finished script, then a line with exactly {VISUAL_SPLIT}, then one sentence "
        "describing the on-screen graphics/b-roll for this Reel."
    )


def _caption_user_prompt(verified_claims, analysis, primary_ref, disclosure, rules) -> str:
    return (
        f"Write a standalone Instagram caption (2-3 lines) for the angle \"{analysis['angle_label']}\".\n\n"
        f"FACTS (verified only):\n{_facts_block(verified_claims)}\n\n"
        f"ANGLE: {analysis['angle_text']}\n\n"
        f"Name the source plainly in the caption: {primary_ref}\n"
        "Mention Maverick Minds' LEAP programme only if genuinely relevant to this specific story — do not "
        "force it in.\n"
        + (f"\nDisclosure line to include verbatim near the end: {disclosure}" if disclosure else "")
        + f"\n\nReply with the finished caption, then a line with exactly {VISUAL_SPLIT}, then one sentence "
        "describing the accompanying image."
    )


def _carousel_user_prompt(verified_claims, analysis, disclosure, rules) -> str:
    ig = rules["instagram"]
    s_lo, s_hi = ig["carousel_slides"]
    return (
        f"Write a {s_lo}-{s_hi} slide Instagram carousel (built to be saved, not just liked) for the angle "
        f"\"{analysis['angle_label']}\". Do not just copy a LinkedIn post — restructure for a swipeable, "
        "one-idea-per-slide read.\n\n"
        f"FACTS (verified only):\n{_facts_block(verified_claims)}\n\n"
        f"ANGLE: {analysis['angle_text']}\n\n"
        "Write it as 'Slide 1: ...' through 'Slide N: ...', each with the actual on-slide text (short, "
        "not a description of what the slide should contain).\n"
        + (f"\nDisclosure line to include verbatim near the end (on the last slide): {disclosure}" if disclosure else "")
        + f"\n\nReply with the finished slide-by-slide text, then a line with exactly {VISUAL_SPLIT}, then "
        "one sentence describing the slide layout / chart type if a number is shown."
    )


def _visual_direction_prompt(analysis, fmt: str) -> str:
    return (
        f"Write the visual direction brief for the content bundle on the angle \"{analysis['angle_label']}\" "
        f"(franchise: {analysis['franchise'] or 'unassigned'}, primary format: {fmt}). Describe, concretely, "
        "what every visual across this bundle should show — LinkedIn image/document treatment (if any), "
        "Reel on-screen graphics and captions style, and carousel slide layout/chart type if a number is "
        "shown. State explicitly that any number or claim shown visually must match a verified claim, and "
        "show no more precision than the underlying data has (e.g. do not chart an inferred/unstated figure "
        "as if it were confirmed). Four to six sentences."
    )


def _split_visual(generated: str, fallback: str) -> tuple[str, str]:
    if VISUAL_SPLIT in generated:
        body, _, visual = generated.partition(VISUAL_SPLIT)
        return body.strip(), (visual.strip() or fallback)
    return generated.strip(), fallback


def generate_bundle_prose(
    conn: sqlite3.Connection,
    signal_id: str,
    draft_ids: dict[str, int],
    now_iso: str,
    generate_fn: GenerateFn | None = None,
    edited_by: str = "claude_content_engine",
) -> dict[str, dict]:
    """Fills every asset in a scaffolded bundle (see `drafts.create_content_bundle`)
    with genuinely finished prose, saves each as a new draft version, and
    lints the result. Returns {asset_slot: {"draft_id": int, "lint_issues": [...]}}.

    Raises ValueError if the signal has no verified claims — there is
    nothing honest to ground generation in yet, so this refuses rather than
    writing an ungrounded post.
    """
    signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if signal is None:
        raise ValueError(f"No such signal: {signal_id}")
    analysis = drafts._selected_analysis(conn, signal_id)
    if analysis is None:
        raise ValueError(f"{signal_id} has no selected angle.")
    verified = conn.execute(
        "SELECT * FROM claims WHERE signal_id = ? AND status = 'verified' ORDER BY id", (signal_id,)
    ).fetchall()
    if not verified:
        raise ValueError(
            f"{signal_id} has no verified claims yet — refusing to generate content with nothing to ground it in."
        )

    rules = settings.content_rules()
    generate = generate_fn or default_generate_fn()
    system_prompt = _common_system_prompt(rules)
    needs_disclosure = drafts.disclosure_required(conn, signal_id)
    disclosure = rules["disclosure_line"] if needs_disclosure else None
    primary_ref = signal["primary_doc_ref"] or signal["primary_source_url"] or "the primary source document"
    fmt = rules["franchise_format"].get(analysis["franchise"], "Text")

    prompts: dict[str, str] = {
        "linkedin_post_1": _linkedin_user_prompt(1, verified, analysis, disclosure, rules),
        "linkedin_post_2": _linkedin_user_prompt(2, verified, analysis, disclosure, rules),
        "linkedin_post_3": _linkedin_user_prompt(3, verified, analysis, disclosure, rules),
        "instagram_reel_1": _reel_user_prompt(1, verified, analysis, disclosure, rules),
        "instagram_reel_2": _reel_user_prompt(2, verified, analysis, disclosure, rules),
        "instagram_caption": _caption_user_prompt(verified, analysis, primary_ref, disclosure, rules),
        "instagram_carousel": _carousel_user_prompt(verified, analysis, disclosure, rules),
    }

    results: dict[str, dict] = {}
    for slot, user_prompt in prompts.items():
        generated = generate(system_prompt, user_prompt)
        body, visual_brief = _split_visual(generated, fallback=f"{fmt} — matches the verified claims above.")
        new_id = drafts.save_draft_version(
            conn, draft_ids[slot], now_iso, edited_by, ["ai_generated_draft"],
            body=body, visual_brief=visual_brief,
        )
        results[slot] = {"draft_id": new_id, "lint_issues": drafts.lint_draft(conn, new_id)}

    # Visual direction has no channel-voice constraints and no facts/angle
    # split — it is itself the visual brief, generated once and shared.
    vd_body = generate(system_prompt, _visual_direction_prompt(analysis, fmt))
    vd_id = drafts.save_draft_version(
        conn, draft_ids["visual_direction"], now_iso, edited_by, ["ai_generated_draft"],
        body=vd_body, visual_brief=f"{fmt} — see this asset's body for the full visual brief.",
    )
    results["visual_direction"] = {"draft_id": vd_id, "lint_issues": drafts.lint_draft(conn, vd_id)}
    return results


def create_and_generate_bundle(
    conn: sqlite3.Connection,
    signal_id: str,
    now_iso: str,
    generate_fn: GenerateFn | None = None,
) -> dict[str, dict]:
    """One-call entry point: scaffold + generate. Verified signal + selected
    angle in, a finished (linted) content bundle out, ready for the human
    approval gate — the milestone this module exists to close."""
    draft_ids = drafts.create_content_bundle(conn, signal_id, now_iso)
    return generate_bundle_prose(conn, signal_id, draft_ids, now_iso, generate_fn=generate_fn)
