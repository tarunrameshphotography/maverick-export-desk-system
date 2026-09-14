"""Publishing queue lifecycle (Phase 3).

    approved draft --enqueue--> awaiting_media --attach media--> queued
    queued --schedule--> scheduled --due + pre-flight--> publishing --> published
                                                      \\--> held (our gate stopped it; nothing sent)
    publishing --> retry_pending   (platform said definitely-not-published, e.g. 429/5xx)
               --> failed          (platform rejected it, or retries exhausted)
               --> needs_reconciliation (outcome unknown — NEVER retried automatically)
    any live state --> cancelled (human) | superseded (the approved content changed)

A queue item pins one exact approved draft version (plus, for Reels and
carousels, the approved caption draft) and snapshots the exact text that
would be published. Every state change is a compare-and-set on the current
state and writes a queue_events row. Nothing here talks to a platform; see
dispatch.py for the publisher seam.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta

from radar import settings
from radar.publishing import validation as v
from radar.publishing.validation import Issue

STATES = (
    "awaiting_media", "queued", "scheduled", "publishing", "published", "retry_pending",
    "needs_reconciliation", "held", "failed", "cancelled", "superseded",
)
TERMINAL_STATES = {"published", "cancelled", "superseded"}
# Mirrors the partial unique index in migration 006 (minus 'published').
LIVE_STATES = {"awaiting_media", "queued", "scheduled", "publishing", "retry_pending", "needs_reconciliation", "held"}
# An item whose approved content changed is superseded — unless a platform
# call may already have happened (publishing / needs_reconciliation).
SUPERSEDABLE_STATES = {"awaiting_media", "queued", "scheduled", "retry_pending", "held", "failed"}
# A human may record a hand-made post from any non-terminal state except
# while an automated attempt is in flight.
MANUAL_PUBLISH_FROM = SUPERSEDABLE_STATES | {"needs_reconciliation"}

TRANSITIONS: dict[str, set[str]] = {
    "awaiting_media": {"queued", "cancelled", "superseded", "published"},
    "queued": {"scheduled", "awaiting_media", "cancelled", "superseded", "published"},
    "scheduled": {"scheduled", "queued", "publishing", "held", "cancelled", "superseded", "published"},
    "publishing": {"published", "retry_pending", "failed", "needs_reconciliation"},
    "retry_pending": {"publishing", "held", "failed", "cancelled", "superseded", "published"},
    "needs_reconciliation": {"published", "retry_pending", "failed", "cancelled"},
    "held": {"queued", "awaiting_media", "cancelled", "superseded", "published"},
    "failed": {"queued", "awaiting_media", "cancelled", "superseded", "published"},
    "published": set(),
    "cancelled": set(),
    "superseded": set(),
}
TIER_ORDER = {"green": 0, "amber": 1, "red": 2}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class QueueError(ValueError):
    """A refused queue action. Subclasses ValueError so the CLI reports it as
    'Refused:' with exit code 2, like every other human-gate refusal."""


# ---------------------------------------------------------------- plumbing
def _now(now_iso: str) -> str:
    return v.utc_iso(v.parse_stored(now_iso))


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM publish_queue WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise QueueError(f"No such queue item: {item_id}")
    return row


def _log_event(conn, item_id: int, from_state: str | None, to_state: str, actor: str, at: str,
               reason: str | None = None, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO queue_events (queue_item_id, from_state, to_state, actor, reason, detail_json, occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, from_state, to_state, actor, reason, json.dumps(detail) if detail else None, at),
    )


def _transition(conn, item: sqlite3.Row, to_state: str, actor: str, at: str,
                reason: str | None = None, detail: dict | None = None, **fields) -> None:
    from_state = item["state"]
    if to_state not in TRANSITIONS[from_state]:
        raise QueueError(f"Queue item #{item['id']} cannot move from {from_state} to {to_state}")
    assignments = {"state": to_state, "updated_at": at, **fields}
    cur = conn.execute(
        f"UPDATE publish_queue SET {', '.join(f'{k} = ?' for k in assignments)} WHERE id = ? AND state = ?",
        (*assignments.values(), item["id"], from_state),
    )
    if cur.rowcount != 1:
        raise QueueError(f"Queue item #{item['id']} changed state concurrently; re-read it and retry")
    _log_event(conn, item["id"], from_state, to_state, actor, at, reason, detail)


def _raise_issues(prefix: str, issues: list[Issue]) -> None:
    if issues:
        raise QueueError(prefix + "\n- " + "\n- ".join(str(i) for i in issues))


def content_issues(conn: sqlite3.Connection, item: sqlite3.Row) -> list[Issue]:
    """Is what this item would publish still exactly the approved content?"""
    issues = v.draft_gate(conn, item["draft_id"], "draft")
    if item["caption_draft_id"]:
        issues += v.draft_gate(conn, item["caption_draft_id"], "caption draft")
    issues += v.signal_gate(conn, item["signal_id"])
    text, comment = v.compose_publication(conn, item["draft_id"], item["caption_draft_id"], item["post_format"])
    if text != item["publish_text"] or v.text_fingerprint(text, comment) != item["text_fingerprint"]:
        issues.append(Issue("text_changed", "the draft text no longer matches what was queued"))
    issues += v.validate_text(item["post_format"], item["publish_text"])
    return issues


def freshness_issues(conn: sqlite3.Connection, item: sqlite3.Row, at: datetime) -> list[Issue]:
    issues = v.approval_freshness(conn, item["draft_id"], item["risk_tier"], at, "draft")
    if item["caption_draft_id"]:
        issues += v.approval_freshness(conn, item["caption_draft_id"], item["risk_tier"], at, "caption draft")
    return issues


def duplicate_published_issues(conn: sqlite3.Connection, item: sqlite3.Row) -> list[Issue]:
    dup = conn.execute(
        "SELECT id FROM publish_queue WHERE state = 'published' AND platform = ? AND target_account = ? "
        "AND text_fingerprint = ? AND id != ?",
        (item["platform"], item["target_account"], item["text_fingerprint"], item["id"]),
    ).fetchone()
    if dup:
        return [Issue("duplicate_published",
                      f"this exact text is already published on {item['platform']}/{item['target_account']} "
                      f"(item #{dup['id']})")]
    return []


# ---------------------------------------------------------------- enqueue
def _latest_caption_draft(conn, signal_id: str):
    return conn.execute(
        "SELECT * FROM content_drafts WHERE signal_id = ? AND asset_slot = 'instagram_caption' "
        "ORDER BY version DESC LIMIT 1",
        (signal_id,),
    ).fetchone()


def _same_signal_conflicts(conn, signal_id: str, platform: str, account: str, now: str) -> list[sqlite3.Row]:
    cutoff = v.utc_iso(v.parse_stored(now) - timedelta(days=settings.publishing_rules()["repeat_window_days"]))
    placeholders = ",".join("?" * len(LIVE_STATES))
    return conn.execute(
        f"SELECT id, state, asset_slot FROM publish_queue WHERE signal_id = ? AND platform = ? AND target_account = ? "
        f"AND (state IN ({placeholders}) OR (state = 'published' AND published_at >= ?))",
        (signal_id, platform, account, *sorted(LIVE_STATES), cutoff),
    ).fetchall()


def enqueue(
    conn: sqlite3.Connection,
    draft_id: int,
    actor: str,
    now_iso: str,
    *,
    post_format: str | None = None,
    target_account: str | None = None,
    caption_draft_id: int | None = None,
    commercial_line: str = "none",
    allow_repeat_reason: str | None = None,
) -> tuple[int, bool]:
    """Queues an approved draft for one platform account. Idempotent: queuing
    the same approved text again returns the existing item (created=False)."""
    now = _now(now_iso)
    rules = settings.publishing_rules()
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None:
        raise QueueError(f"No such draft: {draft_id}")
    slot = draft["asset_slot"] or draft["channel"]
    formats = rules["slot_formats"].get(slot)
    if not formats:
        raise QueueError(f"Draft #{draft_id} is slot '{slot}', an internal asset — it cannot be published")
    post_format = post_format or formats[0]
    if post_format not in formats:
        raise QueueError(f"Slot '{slot}' can be published as {', '.join(formats)}, not {post_format}")
    fmt = rules["formats"][post_format]
    platform = fmt["platform"]
    accounts = rules["accounts"][platform]
    target_account = target_account or accounts["default"]
    if target_account not in accounts["allowed"]:
        raise QueueError(f"Unknown {platform} account '{target_account}'; allowed: {', '.join(accounts['allowed'])}")

    caption = None
    if fmt.get("caption_from") == "caption_draft":
        caption = (conn.execute("SELECT * FROM content_drafts WHERE id = ?", (caption_draft_id,)).fetchone()
                   if caption_draft_id else _latest_caption_draft(conn, draft["signal_id"]))
        if caption is None:
            raise QueueError(f"{post_format} publishes an approved instagram_caption draft as its caption; "
                             f"signal {draft['signal_id']} has none")
        if caption["signal_id"] != draft["signal_id"] or caption["asset_slot"] != "instagram_caption":
            raise QueueError(f"Draft #{caption['id']} is not an instagram_caption draft of signal {draft['signal_id']}")
        caption_draft_id = caption["id"]
    elif caption_draft_id is not None:
        raise QueueError(f"{post_format} publishes its own text; a caption draft does not apply")

    issues = v.draft_gate(conn, draft_id, "draft")
    if caption is not None:
        issues += v.draft_gate(conn, caption_draft_id, "caption draft")
    issues += v.signal_gate(conn, draft["signal_id"])
    publish_text, first_comment = v.compose_publication(conn, draft_id, caption_draft_id, post_format)
    issues += v.validate_text(post_format, publish_text)
    _raise_issues(f"Cannot enqueue draft #{draft_id}:", issues)

    fingerprint = v.text_fingerprint(publish_text, first_comment)
    key = hashlib.sha256(
        f"{platform}|{target_account}|{draft_id}|{caption_draft_id or ''}|{fingerprint}".encode("utf-8")
    ).hexdigest()
    existing = conn.execute(
        "SELECT * FROM publish_queue WHERE idempotency_key = ? AND state NOT IN ('cancelled', 'superseded', 'failed')",
        (key,),
    ).fetchone()
    if existing:
        if existing["post_format"] != post_format:
            raise QueueError(f"Draft #{draft_id} is already queued as {existing['post_format']} "
                             f"(item #{existing['id']}); cancel it first to change format")
        return existing["id"], False

    dup = conn.execute(
        "SELECT id FROM publish_queue WHERE state = 'published' AND platform = ? AND target_account = ? "
        "AND text_fingerprint = ?",
        (platform, target_account, fingerprint),
    ).fetchone()
    if dup:
        raise QueueError(f"This exact text was already published on {platform}/{target_account} (item #{dup['id']})")

    conflicts = _same_signal_conflicts(conn, draft["signal_id"], platform, target_account, now)
    if conflicts and not allow_repeat_reason:
        listed = ", ".join(f"#{c['id']} {c['asset_slot']} ({c['state']})" for c in conflicts)
        raise QueueError(f"Signal {draft['signal_id']} already has {platform}/{target_account} items: {listed}. "
                         "Another asset of the same story would repeat it; pass an explicit repeat reason to override.")

    tier = draft["risk_tier"] or "red"
    if caption is not None and TIER_ORDER.get(caption["risk_tier"] or "red", 2) > TIER_ORDER[tier]:
        tier = caption["risk_tier"]
    state = "awaiting_media" if v.requires_media(post_format) else "queued"
    try:
        cur = conn.execute(
            """
            INSERT INTO publish_queue (draft_id, caption_draft_id, signal_id, asset_slot, platform, post_format,
                                       target_account, state, publish_text, first_comment, first_comment_state,
                                       text_fingerprint, idempotency_key, risk_tier, commercial_line, max_attempts,
                                       enqueued_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id, caption_draft_id, draft["signal_id"], slot, platform, post_format, target_account, state,
                publish_text, first_comment, "pending" if first_comment else "not_required", fingerprint, key,
                tier, commercial_line, rules["retry"]["max_attempts"], actor, now, now,
            ),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT id FROM publish_queue WHERE idempotency_key = ? AND state NOT IN ('cancelled', 'superseded', 'failed')",
            (key,),
        ).fetchone()
        if row is None:
            raise
        return row["id"], False
    item_id = cur.lastrowid
    _log_event(conn, item_id, None, state, actor, now, "enqueued", {
        "post_format": post_format, "target_account": target_account, "caption_draft_id": caption_draft_id,
        "repeat_override": allow_repeat_reason, "repeat_conflicts": [c["id"] for c in conflicts] or None,
    })
    conn.commit()
    return item_id, True


# ---------------------------------------------------------------- media
def _refresh_media_state(conn, item_id: int, actor: str, at: str) -> None:
    item = get_item(conn, item_id)
    if item["state"] not in ("awaiting_media", "queued"):
        return
    issues = v.validate_media(conn, item)
    target = "awaiting_media" if issues else "queued"
    if target != item["state"]:
        _transition(conn, item, target, actor, at,
                    "media complete" if target == "queued" else "media incomplete",
                    {"issues": [str(i) for i in issues]} if issues else None)


def attach_media(
    conn: sqlite3.Connection,
    item_id: int,
    path: str,
    kind: str,
    actor: str,
    now_iso: str,
    *,
    position: int = 1,
    public_url: str | None = None,
    alt_text: str | None = None,
    title: str | None = None,
    duration_seconds: float | None = None,
    page_count: int | None = None,
) -> int:
    """Attaches (or replaces, by kind + position) a media file. Size, hash,
    real image type and pixel dimensions are read from the file; duration and
    page count must be declared because they aren't parsed."""
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] not in ("awaiting_media", "queued", "held"):
        raise QueueError(f"Media can only change while an item is awaiting_media/queued/held "
                         f"(#{item_id} is {item['state']}; unschedule it first)")
    fmt = settings.publishing_rules()["formats"][item["post_format"]]
    if kind not in fmt["media"]:
        raise QueueError(f"{item['post_format']} takes {', '.join(fmt['media']) or 'no'} media, not {kind}")
    if public_url and not public_url.startswith("https://"):
        raise QueueError("public_url must be an https URL")
    facts = v.inspect_file(path)
    if facts["mime_type"] is None:
        raise QueueError(f"Unrecognised media type for {path}")
    cur = conn.execute(
        """
        INSERT INTO queue_media (queue_item_id, media_kind, position, local_path, public_url, mime_type, byte_size,
                                 sha256, width, height, duration_seconds, page_count, alt_text, title, attached_by,
                                 attached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (queue_item_id, media_kind, position) DO UPDATE SET
            local_path = excluded.local_path, public_url = excluded.public_url, mime_type = excluded.mime_type,
            byte_size = excluded.byte_size, sha256 = excluded.sha256, width = excluded.width,
            height = excluded.height, duration_seconds = excluded.duration_seconds,
            page_count = excluded.page_count, alt_text = excluded.alt_text, title = excluded.title,
            attached_by = excluded.attached_by, attached_at = excluded.attached_at
        """,
        (item_id, kind, position, facts["local_path"], public_url, facts["mime_type"], facts["byte_size"],
         facts["sha256"], facts["width"], facts["height"], duration_seconds, page_count, alt_text, title, actor, now),
    )
    _log_event(conn, item_id, item["state"], item["state"], actor, now, "media attached",
               {"kind": kind, "position": position, "sha256": facts["sha256"], "mime_type": facts["mime_type"]})
    _refresh_media_state(conn, item_id, actor, now)
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- scheduling
def _cadence_issues(conn, item: sqlite3.Row, when: datetime) -> list[Issue]:
    rules = settings.publishing_rules()["cadence"][item["platform"]]
    tz = v.local_tz()
    local = when.astimezone(tz)
    issues: list[Issue] = []
    if WEEKDAYS[local.weekday()] not in rules["allowed_weekdays"]:
        issues.append(Issue("cadence", f"{item['platform']} does not post on {local.strftime('%A')}s"))
    others = conn.execute(
        "SELECT id, state, scheduled_at, published_at FROM publish_queue WHERE platform = ? AND target_account = ? "
        "AND id != ? AND state IN ('scheduled', 'publishing', 'retry_pending', 'needs_reconciliation', 'published')",
        (item["platform"], item["target_account"], item["id"]),
    ).fetchall()
    times = []
    for o in others:
        stamp = o["published_at"] if o["state"] == "published" else o["scheduled_at"]
        if stamp:
            times.append((o["id"], v.parse_stored(stamp)))
    same_day = [i for i, t in times if t.astimezone(tz).date() == local.date()]
    if len(same_day) >= rules["max_per_day"]:
        issues.append(Issue("cadence", f"{local.date()} already has {len(same_day)} {item['platform']} post(s) "
                                       f"(items {same_day}); max {rules['max_per_day']}/day"))
    week = local.isocalendar()[:2]
    same_week = [i for i, t in times if t.astimezone(tz).isocalendar()[:2] == week]
    if len(same_week) >= rules["max_per_week"]:
        issues.append(Issue("cadence", f"that week already has {len(same_week)} {item['platform']} post(s); "
                                       f"max {rules['max_per_week']}/week"))
    gap = timedelta(minutes=rules["min_gap_minutes"])
    close = [i for i, t in times if abs(t - when) < gap]
    if close:
        issues.append(Issue("cadence", f"within {rules['min_gap_minutes']} min of items {close}"))
    return issues


def schedule(
    conn: sqlite3.Connection,
    item_id: int,
    when: str,
    actor: str,
    now_iso: str,
    *,
    override_reason: str | None = None,
) -> str:
    """Gives a queued item a publish time (or moves a scheduled one). The
    approval must still be fresh at that time; amber/red need the founder;
    editorial cadence can only be overridden with a recorded reason."""
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] not in ("queued", "scheduled"):
        raise QueueError(f"Only queued or scheduled items can be scheduled (#{item_id} is {item['state']})")
    when_dt = v.parse_user_time(when)
    if when_dt < v.parse_stored(now) - timedelta(seconds=60):
        raise QueueError(f"{v.utc_iso(when_dt)} is in the past")
    if item["risk_tier"] in settings.publishing_rules()["founder_schedules_tiers"] and actor != "founder":
        raise QueueError(f"{item['risk_tier']} tier items can only be scheduled by the founder")
    _raise_issues(f"Cannot schedule #{item_id}:",
                  content_issues(conn, item) + v.validate_media(conn, item) + freshness_issues(conn, item, when_dt)
                  + duplicate_published_issues(conn, item))
    cadence = _cadence_issues(conn, item, when_dt)
    if cadence and not override_reason:
        _raise_issues(f"Cannot schedule #{item_id} (editorial cadence; pass an override reason to force):", cadence)
    scheduled_at = v.utc_iso(when_dt)
    _transition(conn, item, "scheduled", actor, now,
                "rescheduled" if item["state"] == "scheduled" else "scheduled",
                {"scheduled_at": scheduled_at, "cadence_override": override_reason,
                 "cadence_issues": [str(i) for i in cadence] or None},
                scheduled_at=scheduled_at, scheduled_by=actor)
    conn.commit()
    return scheduled_at


def unschedule(conn: sqlite3.Connection, item_id: int, actor: str, now_iso: str, reason: str) -> None:
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] != "scheduled":
        raise QueueError(f"#{item_id} is {item['state']}, not scheduled")
    _transition(conn, item, "queued", actor, now, reason, scheduled_at=None, scheduled_by=None)
    conn.commit()


def cancel(conn: sqlite3.Connection, item_id: int, actor: str, now_iso: str, reason: str) -> None:
    if not reason:
        raise QueueError("Cancelling needs a reason")
    now = _now(now_iso)
    item = get_item(conn, item_id)
    _transition(conn, item, "cancelled", actor, now, reason, lease_expires_at=None, next_attempt_at=None)
    conn.commit()


def requeue(conn: sqlite3.Connection, item_id: int, actor: str, now_iso: str, reason: str) -> str:
    """Brings a held or failed item back after a human fixed the cause. It
    gets a fresh retry budget; attempt numbers keep counting up."""
    if not reason:
        raise QueueError("Re-queueing needs a reason")
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] not in ("held", "failed"):
        raise QueueError(f"Only held or failed items can be re-queued (#{item_id} is {item['state']})")
    issues = content_issues(conn, item)
    if any(i.code in v.SUPERSEDING_CODES for i in issues):
        raise QueueError(f"#{item_id} was built from content that is no longer approved; cancel it and enqueue "
                         "the current approved version instead:\n- " + "\n- ".join(map(str, issues)))
    _raise_issues(f"Cannot re-queue #{item_id}:", issues)
    target = "awaiting_media" if v.validate_media(conn, item) else "queued"
    _transition(conn, item, target, actor, now, reason,
                scheduled_at=None, scheduled_by=None, next_attempt_at=None, lease_expires_at=None,
                last_error_code=None, last_error_message=None,
                max_attempts=item["attempt_count"] + settings.publishing_rules()["retry"]["max_attempts"])
    conn.commit()
    return target


# ---------------------------------------------------------------- publication records
def record_item_published(
    conn: sqlite3.Connection,
    item: sqlite3.Row,
    *,
    url: str | None,
    external_post_id: str | None,
    published_at: str,
    actor: str,
    at: str,
    via: str,
    first_comment_state: str | None = None,
    published_content_id: int | None = None,
) -> int:
    """The one place a queue item becomes 'published'. Writes the
    published_content row the performance/learning loop reads (unless the
    legacy path already wrote it) and refuses without evidence of the post."""
    if not (url or external_post_id):
        raise QueueError("Recording a publication needs the post URL or the platform's post ID")
    if published_content_id is None:
        cur = conn.execute(
            """
            INSERT INTO published_content (draft_id, channel, published_at, url, final_text, status, commercial_line,
                                           platform_post_id, queue_item_id)
            VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?)
            """,
            (item["draft_id"], item["platform"], published_at, url, item["publish_text"], item["commercial_line"],
             external_post_id, item["id"]),
        )
        published_content_id = cur.lastrowid
    else:
        conn.execute("UPDATE published_content SET queue_item_id = ? WHERE id = ?", (item["id"], published_content_id))
    fields = dict(external_post_id=external_post_id, external_url=url, published_at=published_at,
                  published_content_id=published_content_id, lease_expires_at=None, next_attempt_at=None)
    if first_comment_state and item["first_comment"]:
        fields["first_comment_state"] = first_comment_state
    _transition(conn, item, "published", actor, at, f"published ({via})",
                {"url": url, "external_post_id": external_post_id, "published_content_id": published_content_id},
                **fields)
    return published_content_id


def mark_published_manually(
    conn: sqlite3.Connection,
    item_id: int,
    url: str,
    actor: str,
    now_iso: str,
    *,
    published_at: str | None = None,
    external_post_id: str | None = None,
) -> int:
    """Records a post a human made by hand from this queue item, so the
    dispatcher can never publish it a second time."""
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] == "publishing":
        raise QueueError(f"#{item_id} has an automated attempt in flight; wait for it (or its lease to expire) "
                         "and reconcile instead")
    if item["state"] not in MANUAL_PUBLISH_FROM:
        raise QueueError(f"#{item_id} is {item['state']}; it cannot be recorded as published")
    if not url.startswith("https://"):
        raise QueueError("The post URL must be an https URL")
    draft = conn.execute("SELECT status FROM content_drafts WHERE id = ?", (item["draft_id"],)).fetchone()
    if draft["status"] != "approved":
        raise QueueError("Only approved drafts can be recorded as published")
    needs_disclosure = conn.execute(
        "SELECT MAX(disclosure_required) AS d FROM content_drafts WHERE id IN (?, ?)",
        (item["draft_id"], item["caption_draft_id"] or item["draft_id"]),
    ).fetchone()["d"]
    if needs_disclosure and settings.content_rules()["disclosure_line"] not in item["publish_text"]:
        raise QueueError("Published text is missing the required disclosure line")
    stamp = v.utc_iso(v.parse_user_time(published_at)) if published_at else now
    pid = record_item_published(conn, item, url=url, external_post_id=external_post_id, published_at=stamp,
                                actor=actor, at=now, via="manual")
    conn.commit()
    return pid


def reconcile(
    conn: sqlite3.Connection,
    item_id: int,
    outcome: str,
    actor: str,
    now_iso: str,
    reason: str,
    *,
    url: str | None = None,
    external_post_id: str | None = None,
) -> str:
    """Resolves an unknown outcome after someone checked the platform:
    'published' (with evidence) or 'not_published' (safe to try again)."""
    if not reason:
        raise QueueError("Reconciling needs a reason (what was checked)")
    now = _now(now_iso)
    item = get_item(conn, item_id)
    if item["state"] != "needs_reconciliation":
        raise QueueError(f"#{item_id} is {item['state']}, not needs_reconciliation")
    if outcome == "published":
        record_item_published(conn, item, url=url, external_post_id=external_post_id, published_at=now,
                              actor=actor, at=now, via=f"reconciled: {reason}")
        target = "published"
    elif outcome == "not_published":
        if item["attempt_count"] >= item["max_attempts"]:
            target = "failed"
            _transition(conn, item, target, actor, now, reason, last_error_code="max_attempts_exceeded",
                        lease_expires_at=None)
        else:
            target = "retry_pending"
            _transition(conn, item, target, actor, now, reason, next_attempt_at=now, lease_expires_at=None)
    else:
        raise QueueError("outcome must be 'published' or 'not_published'")
    conn.commit()
    return target


def active_items_for_draft(conn: sqlite3.Connection, draft_id: int) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(LIVE_STATES))
    return conn.execute(
        f"SELECT * FROM publish_queue WHERE draft_id = ? AND state IN ({placeholders}) ORDER BY id",
        (draft_id, *sorted(LIVE_STATES)),
    ).fetchall()


# ---------------------------------------------------------------- supersession hooks (called from drafts.py)
def _supersede(conn, rows, at: str, reason: str, actor: str) -> list[int]:
    ids = []
    for row in rows:
        _transition(conn, row, "superseded", actor, at, reason, lease_expires_at=None, next_attempt_at=None)
        ids.append(row["id"])
    return ids


def supersede_for_slot(conn: sqlite3.Connection, signal_id: str, asset_slot: str, now_iso: str,
                       reason: str, actor: str = "system") -> list[int]:
    """A new version of a slot means the queued text is no longer the text
    being worked on — never publish the stale approved version."""
    placeholders = ",".join("?" * len(SUPERSEDABLE_STATES))
    rows = conn.execute(
        f"""
        SELECT * FROM publish_queue WHERE state IN ({placeholders}) AND signal_id = ?
          AND (asset_slot = ? OR caption_draft_id IN
               (SELECT id FROM content_drafts WHERE signal_id = ? AND asset_slot = ?))
        """,
        (*sorted(SUPERSEDABLE_STATES), signal_id, asset_slot, signal_id, asset_slot),
    ).fetchall()
    return _supersede(conn, rows, _now(now_iso), reason, actor)


def supersede_for_draft(conn: sqlite3.Connection, draft_id: int, now_iso: str, reason: str,
                        actor: str = "system") -> list[int]:
    placeholders = ",".join("?" * len(SUPERSEDABLE_STATES))
    rows = conn.execute(
        f"SELECT * FROM publish_queue WHERE state IN ({placeholders}) AND (draft_id = ? OR caption_draft_id = ?)",
        (*sorted(SUPERSEDABLE_STATES), draft_id, draft_id),
    ).fetchall()
    return _supersede(conn, rows, _now(now_iso), reason, actor)


# ---------------------------------------------------------------- views
def list_items(conn: sqlite3.Connection, states: list[str] | None = None) -> list[sqlite3.Row]:
    if states:
        placeholders = ",".join("?" * len(states))
        return conn.execute(
            f"SELECT * FROM publish_queue WHERE state IN ({placeholders}) "
            "ORDER BY COALESCE(scheduled_at, next_attempt_at, created_at), id", tuple(states),
        ).fetchall()
    return conn.execute("SELECT * FROM publish_queue ORDER BY COALESCE(scheduled_at, created_at), id").fetchall()


def _local(stamp: str | None) -> str:
    return v.parse_stored(stamp).astimezone(v.local_tz()).strftime("%a %d %b %H:%M") if stamp else "-"


def render_queue(conn: sqlite3.Connection, include_terminal: bool = False) -> str:
    states = None if include_terminal else [s for s in STATES if s not in TERMINAL_STATES]
    rows = list_items(conn, states)
    if not rows:
        return "Queue is empty."
    tz = settings.publishing_rules()["timezone_offset"]
    lines = [f"{'#':>4}  {'state':<21} {'platform/format':<30} {'slot':<19} {'tier':<5} {'when (' + tz + ')':<17} signal"]
    for r in rows:
        when = r["next_attempt_at"] if r["state"] == "retry_pending" else r["scheduled_at"] or r["published_at"]
        lines.append(f"{r['id']:>4}  {r['state']:<21} {r['platform'] + '/' + r['post_format']:<30} "
                     f"{r['asset_slot']:<19} {r['risk_tier']:<5} {_local(when):<17} {r['signal_id']}")
    return "\n".join(lines)


def render_item(conn: sqlite3.Connection, item_id: int, now_iso: str) -> str:
    item = get_item(conn, item_id)
    now = v.parse_stored(_now(now_iso))
    lines = [
        f"# Queue item #{item_id} — {item['state']}",
        f"platform/format: {item['platform']}/{item['post_format']} -> {item['target_account']}",
        f"draft #{item['draft_id']} ({item['asset_slot']})"
        + (f", caption draft #{item['caption_draft_id']}" if item["caption_draft_id"] else "")
        + f", signal {item['signal_id']}, risk tier {item['risk_tier']}",
        f"scheduled: {_local(item['scheduled_at'])} by {item['scheduled_by'] or '-'}; "
        f"attempts {item['attempt_count']}/{item['max_attempts']}; next attempt {_local(item['next_attempt_at'])}",
    ]
    if item["external_url"] or item["external_post_id"]:
        lines.append(f"published: {item['external_url'] or '-'} (platform id {item['external_post_id'] or 'unknown'})")
    if item["last_error_code"]:
        lines.append(f"last error: {item['last_error_code']} — {item['last_error_message'] or ''}")
    lines += ["", "## Text to publish", item["publish_text"]]
    if item["first_comment"]:
        lines += ["", f"First comment ({item['first_comment_state']}): {item['first_comment']}"]
    media = conn.execute("SELECT * FROM queue_media WHERE queue_item_id = ? ORDER BY media_kind, position",
                         (item_id,)).fetchall()
    lines += ["", "## Media"] + ([
        f"- {m['media_kind']} #{m['position']}: {m['mime_type']}, {m['byte_size']} bytes"
        + (f", {m['width']}x{m['height']}" if m["width"] else "")
        + (f", {m['duration_seconds']}s" if m["duration_seconds"] else "")
        + (f", {m['page_count']} pages" if m["page_count"] else "")
        + f", {m['local_path']}" + (f", {m['public_url']}" if m["public_url"] else "")
        for m in media
    ] or ["(none)"])
    if item["state"] not in TERMINAL_STATES:
        checks = (content_issues(conn, item) + v.validate_media(conn, item, for_api=True)
                  + freshness_issues(conn, item, now) + duplicate_published_issues(conn, item))
        lines += ["", "## Checks for automated publishing now"] + ([f"- {i}" for i in checks] or ["- all clear"])
    lines += ["", "## History"]
    for e in conn.execute("SELECT * FROM queue_events WHERE queue_item_id = ? ORDER BY id", (item_id,)).fetchall():
        lines.append(f"- {e['occurred_at']} {e['from_state'] or '(new)'} -> {e['to_state']} by {e['actor']}: "
                     f"{e['reason'] or ''}")
    for a in conn.execute("SELECT * FROM publish_attempts WHERE queue_item_id = ? ORDER BY id", (item_id,)).fetchall():
        lines.append(f"- attempt {a['attempt_number'] or '-'} [{a['mode']}] {a['started_at']}: {a['outcome']} "
                     f"{a['error_code'] or ''} {a['error_message'] or ''}".rstrip())
    return "\n".join(lines)
