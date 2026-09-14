"""The scheduler tick and the publisher seam Phase 4 plugs into.

`dispatch_due` finds scheduled/retry items whose time has come and, for each:
  1. runs the pre-flight gate (the approved content, approval freshness,
     media, duplicates — all re-checked at the last moment);
  2. dry run (the default): records what *would* be sent and changes no
     state. Live: claims the item with a compare-and-set, commits a numbered
     attempt row BEFORE calling the platform, then records the result.

Neither LinkedIn nor Instagram offers an idempotency key (checked against
their docs, 2026-09-14), so the only safe rule for an ambiguous outcome —
an exception, a timeout, a "success" with no post ID, a lease that expired
mid-call — is `needs_reconciliation`: a human (or, later, an adapter that
looks the post up) decides. It is never retried automatically, because a
retry could double-post.

Live dispatch refuses unless `auto_publish` is true, which
settings.feature_flags() currently makes impossible. No platform publisher
exists in this phase; Phase 4 supplies classes that satisfy `Publisher`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Protocol

from radar import settings
from radar.publishing import queue as q
from radar.publishing import validation as v
from radar.publishing.validation import Issue

OUTCOMES = {"published", "retryable_error", "permanent_error", "unknown"}


class PublishingDisabledError(ValueError):
    """Live publishing was requested while the auto_publish gate is closed."""


@dataclass
class PublishPayload:
    item_id: int
    idempotency_key: str
    platform: str
    post_format: str
    target_account: str
    text: str
    first_comment: str | None
    media: list[dict] = field(default_factory=list)
    risk_tier: str = "red"
    signal_id: str = ""
    draft_id: int = 0


@dataclass
class PublishResult:
    """What a platform publisher reports. `published` must carry the
    platform's post ID or URL; anything it can't be sure about is `unknown`."""
    outcome: str
    external_post_id: str | None = None
    external_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_after_seconds: int | None = None
    first_comment_posted: bool | None = None
    raw_response: dict | None = None


class Publisher(Protocol):
    name: str
    platform: str

    def publish(self, payload: PublishPayload) -> PublishResult: ...


def assert_live_publishing_allowed() -> None:
    """The auto_publish gate. feature_flags() itself raises if the flag is
    ever set true in v1; with it false this refuses. Phase 4 changes this
    deliberately, not by accident."""
    if not settings.feature_flags().get("auto_publish"):
        raise PublishingDisabledError(
            "Live publishing is disabled: auto_publish is false (Section 30). Run a dry run instead, "
            "or post by hand and record it with queue-mark-published."
        )


def build_payload(conn: sqlite3.Connection, item: sqlite3.Row) -> PublishPayload:
    media = [
        {k: m[k] for k in ("media_kind", "position", "local_path", "public_url", "mime_type", "byte_size",
                           "sha256", "width", "height", "duration_seconds", "page_count", "alt_text", "title")}
        for m in conn.execute("SELECT * FROM queue_media WHERE queue_item_id = ? ORDER BY media_kind, position",
                              (item["id"],)).fetchall()
    ]
    return PublishPayload(
        item_id=item["id"], idempotency_key=item["idempotency_key"], platform=item["platform"],
        post_format=item["post_format"], target_account=item["target_account"], text=item["publish_text"],
        first_comment=item["first_comment"], media=media, risk_tier=item["risk_tier"],
        signal_id=item["signal_id"], draft_id=item["draft_id"],
    )


def preflight(conn: sqlite3.Connection, item: sqlite3.Row, now: str) -> list[Issue]:
    """Everything that must be true in the moment before a post goes out."""
    issues = q.content_issues(conn, item)
    issues += v.validate_media(conn, item, for_api=True)
    issues += q.freshness_issues(conn, item, v.parse_stored(now))
    issues += q.duplicate_published_issues(conn, item)
    if (item["risk_tier"] in settings.publishing_rules()["founder_schedules_tiers"]
            and item["scheduled_by"] != "founder"):
        issues.append(Issue("approval_record", f"{item['risk_tier']} tier item was not scheduled by the founder"))
    return issues


def _backoff(attempt_number: int, retry_after_seconds: int | None) -> timedelta:
    r = settings.publishing_rules()["retry"]
    minutes = min(r["backoff_base_minutes"] * (2 ** max(attempt_number - 1, 0)), r["backoff_max_minutes"])
    delay = timedelta(minutes=minutes)
    if retry_after_seconds:
        delay = max(delay, timedelta(seconds=retry_after_seconds))
    return delay


def due_items(conn: sqlite3.Connection, now: str, limit: int | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM publish_queue WHERE (state = 'scheduled' AND scheduled_at <= ?) "
        "OR (state = 'retry_pending' AND next_attempt_at <= ?) "
        "ORDER BY COALESCE(next_attempt_at, scheduled_at), id"
    )
    rows = conn.execute(sql, (now, now)).fetchall()
    return rows[:limit] if limit else rows


def recover_stale_leases(conn: sqlite3.Connection, now_iso: str) -> list[int]:
    """An item left 'publishing' past its lease (crash, killed process) may or
    may not have been posted. It goes to reconciliation, never back to retry."""
    now = q._now(now_iso)
    stale = conn.execute(
        "SELECT * FROM publish_queue WHERE state = 'publishing' AND lease_expires_at < ?", (now,)
    ).fetchall()
    for item in stale:
        conn.execute(
            "UPDATE publish_attempts SET finished_at = ?, outcome = 'unknown', error_code = 'lease_expired', "
            "error_message = 'no result recorded before the lease expired' "
            "WHERE queue_item_id = ? AND mode = 'live' AND finished_at IS NULL",
            (now, item["id"]),
        )
        q._transition(conn, item, "needs_reconciliation", "system", now,
                      "lease expired mid-attempt; the post may or may not exist",
                      lease_expires_at=None, last_error_code="lease_expired")
    conn.commit()
    return [i["id"] for i in stale]


def _insert_attempt(conn, item_id: int, attempt_number: int | None, mode: str, publisher: str, now: str,
                    payload: PublishPayload, outcome: str | None = None, issues: list[Issue] | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO publish_attempts (queue_item_id, attempt_number, mode, publisher, started_at, finished_at, "
        "outcome, error_code, error_message, request_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, attempt_number, mode, publisher, now, now if outcome else None, outcome,
         "preflight" if issues else None, "; ".join(map(str, issues)) if issues else None,
         json.dumps(asdict(payload))),
    )
    return cur.lastrowid


def apply_result(conn: sqlite3.Connection, item_id: int, attempt_id: int, result: PublishResult,
                 now_iso: str, actor: str) -> str:
    """Turns a publisher's report into the next state. Returns that state."""
    now = q._now(now_iso)
    item = q.get_item(conn, item_id)
    outcome = result.outcome if result.outcome in OUTCOMES else "unknown"
    if outcome == "published" and not (result.external_post_id or result.external_url):
        outcome = "unknown"
        result.error_code = result.error_code or "no_post_reference"
        result.error_message = "publisher reported success without a post ID or URL; not trusted"
    conn.execute(
        "UPDATE publish_attempts SET finished_at = ?, outcome = ?, external_post_id = ?, external_url = ?, "
        "error_code = ?, error_message = ?, response_json = ? WHERE id = ?",
        (now, outcome, result.external_post_id, result.external_url, result.error_code, result.error_message,
         json.dumps(result.raw_response) if result.raw_response is not None else None, attempt_id),
    )
    if outcome == "published":
        comment_state = None
        if result.first_comment_posted is True:
            comment_state = "posted"
        elif result.first_comment_posted is False:
            comment_state = "failed"
        q.record_item_published(conn, item, url=result.external_url, external_post_id=result.external_post_id,
                                published_at=now, actor=actor, at=now, via="api",
                                first_comment_state=comment_state)
        target = "published"
    elif outcome == "retryable_error":
        if item["attempt_count"] >= item["max_attempts"]:
            target = "failed"
            q._transition(conn, item, target, actor, now, "retries exhausted",
                          lease_expires_at=None, last_error_code=f"max_attempts_exceeded: {result.error_code}",
                          last_error_message=result.error_message)
        else:
            target = "retry_pending"
            next_at = v.utc_iso(v.parse_stored(now) + _backoff(item["attempt_count"], result.retry_after_seconds))
            q._transition(conn, item, target, actor, now, f"retryable: {result.error_code}",
                          {"next_attempt_at": next_at}, lease_expires_at=None, next_attempt_at=next_at,
                          last_error_code=result.error_code, last_error_message=result.error_message)
    elif outcome == "permanent_error":
        target = "failed"
        q._transition(conn, item, target, actor, now, f"rejected by platform: {result.error_code}",
                      lease_expires_at=None, last_error_code=result.error_code,
                      last_error_message=result.error_message)
    else:
        target = "needs_reconciliation"
        q._transition(conn, item, target, actor, now, "outcome unknown; check the platform before any retry",
                      lease_expires_at=None, last_error_code=result.error_code or "unknown_outcome",
                      last_error_message=result.error_message)
    conn.commit()
    return target


def _claim(conn, item: sqlite3.Row, publisher: str, payload: PublishPayload, now: str) -> int | None:
    """Compare-and-set claim, committed before the platform call so a crash
    mid-call leaves a durable 'publishing' marker and a numbered attempt."""
    lease = v.utc_iso(v.parse_stored(now) + timedelta(minutes=settings.publishing_rules()["lease_minutes"]))
    attempt_number = item["attempt_count"] + 1
    try:
        q._transition(conn, item, "publishing", "dispatcher", now, f"attempt {attempt_number} via {publisher}",
                      attempt_count=attempt_number, lease_expires_at=lease)
    except q.QueueError:
        conn.rollback()
        return None
    attempt_id = _insert_attempt(conn, item["id"], attempt_number, "live", publisher, now, payload)
    conn.commit()
    return attempt_id


def dispatch_due(
    conn: sqlite3.Connection,
    now_iso: str,
    *,
    dry_run: bool = True,
    publishers: dict[str, Publisher] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """One scheduler tick. Dry run by default; live requires the auto_publish
    gate and a publisher for the item's platform."""
    now = q._now(now_iso)
    publishers = publishers or {}
    if not dry_run:
        assert_live_publishing_allowed()
        recover_stale_leases(conn, now)
    reports: list[dict] = []
    for item in due_items(conn, now, limit):
        payload = build_payload(conn, item)
        issues = preflight(conn, item, now)
        report = {"item_id": item["id"], "platform": item["platform"], "post_format": item["post_format"],
                  "issues": [str(i) for i in issues]}
        if dry_run:
            _insert_attempt(conn, item["id"], None, "dry_run", "dry_run", now, payload,
                            outcome="preflight_blocked" if issues else "dry_run", issues=issues)
            conn.commit()
            reports.append({**report, "result": "would be blocked" if issues else "would publish",
                            "state": item["state"]})
            continue
        if issues:
            target = "superseded" if any(i.code in v.SUPERSEDING_CODES for i in issues) else "held"
            q._transition(conn, item, target, "dispatcher", now, "pre-flight check failed; nothing was sent",
                          {"issues": [str(i) for i in issues]}, lease_expires_at=None)
            conn.commit()
            reports.append({**report, "result": target, "state": target})
            continue
        publisher = publishers.get(item["platform"])
        if publisher is None:
            reports.append({**report, "result": f"skipped: no publisher for {item['platform']}",
                            "state": item["state"]})
            continue
        attempt_id = _claim(conn, item, publisher.name, payload, now)
        if attempt_id is None:
            reports.append({**report, "result": "skipped: claimed elsewhere", "state": q.get_item(conn, item["id"])["state"]})
            continue
        try:
            result = publisher.publish(payload)
        except Exception as exc:  # the post may or may not exist — never assume either way
            result = PublishResult("unknown", error_code="publisher_exception", error_message=repr(exc))
        state = apply_result(conn, item["id"], attempt_id, result, now, "dispatcher")
        reports.append({**report, "result": state, "state": state})
    return reports


def render_dispatch_report(reports: list[dict], dry_run: bool) -> str:
    if not reports:
        return "Nothing is due."
    lines = [f"{'DRY RUN — nothing was sent, no state changed' if dry_run else 'LIVE dispatch'}: "
             f"{len(reports)} due item(s)"]
    for r in reports:
        lines.append(f"- #{r['item_id']} {r['platform']}/{r['post_format']}: {r['result']}")
        lines += [f"    ! {i}" for i in r["issues"]]
    return "\n".join(lines)
