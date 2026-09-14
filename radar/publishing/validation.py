"""Checks shared by enqueue, scheduling and dispatch pre-flight.

Every check returns a list of Issue objects rather than raising, so callers
decide what a failure means at their stage (refuse an enqueue, block a
schedule, hold or supersede a due item). Unknown required metadata — a video
duration nobody declared, image dimensions that can't be read — is an issue,
never a guess.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar import settings
from radar.content import drafts

HASHTAG_PATTERN = re.compile(r"(?<!\w)#\w+")
MENTION_PATTERN = re.compile(r"(?<!\w)@\w+")
BLOCKING_SIGNAL_STATUSES = {"REJECTED", "ARCHIVED", "NEEDS_CORRECTION"}

# Issue codes that mean "the content this item was built from is no longer
# the approved content" — the item is superseded, not merely held.
SUPERSEDING_CODES = {"draft_not_approved", "draft_not_latest", "text_changed"}

EXTENSION_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ---------------------------------------------------------------- time
def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def local_tz() -> timezone:
    offset = settings.publishing_rules()["timezone_offset"]
    sign, hh_mm = offset[0], offset[1:]
    hours, minutes = (int(x) for x in hh_mm.split(":"))
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(delta if sign == "+" else -delta)


def parse_stored(value: str) -> datetime:
    """Stored timestamps are UTC; a naive one (older rows, tests) is read as UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_user_time(value: str) -> datetime:
    """Times typed by a person: an explicit offset wins; a naive time is local (IST)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=local_tz())


# ---------------------------------------------------------------- files
def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + seg_len
    return None


def _sniff_image_mime(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def inspect_file(path: str | Path) -> dict:
    """Facts about a media file read from the file itself — size, hash, real
    image type and pixel dimensions. Video duration and document page count
    are not parsed here (no ffprobe/PDF dependency); they must be declared."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"No such media file: {p}")
    data = p.read_bytes()
    sniffed = _sniff_image_mime(data)
    dims = _image_dimensions(data) if sniffed else None
    return {
        "local_path": str(p.resolve()),
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime_type": sniffed or EXTENSION_MIME.get(p.suffix.lower()),
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
    }


def file_sha256(path: str) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


# ---------------------------------------------------------------- content
def compose_publication(conn: sqlite3.Connection, draft_id: int, caption_draft_id: int | None,
                        post_format: str) -> tuple[str, str | None]:
    """The exact text that would be published, and the LinkedIn first comment.
    For Reels/carousels the published caption is the separately approved
    instagram_caption draft — the primary draft is a script or slide text."""
    fmt = settings.publishing_rules()["formats"][post_format]
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if fmt["platform"] == "linkedin":
        return draft["body"] or "", draft["suggested_first_comment"]
    if fmt.get("caption_from") == "caption_draft":
        caption = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (caption_draft_id,)).fetchone()
        return (caption["body"] if caption else "") or "", None
    return draft["body"] or "", None


def text_fingerprint(publish_text: str, first_comment: str | None) -> str:
    return hashlib.sha256(f"{publish_text}\x1f{first_comment or ''}".encode("utf-8")).hexdigest()


def latest_approval(conn: sqlite3.Connection, draft_id: int):
    return conn.execute(
        "SELECT * FROM approvals WHERE entity_type = 'draft' AND entity_id = ? AND to_status = 'approved' "
        "ORDER BY id DESC LIMIT 1",
        (str(draft_id),),
    ).fetchone()


def draft_gate(conn: sqlite3.Connection, draft_id: int, label: str = "draft") -> list[Issue]:
    """Re-derives, from the draft row and the approvals audit trail, that this
    exact version is still approved, current, lint-clean and tier-compliant.
    set_draft_status enforced all of this at approval time; re-checking here
    means a later edit, rejection, config change or direct DB write can't
    slip an unapproved text through the queue."""
    draft = conn.execute("SELECT * FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None:
        return [Issue("draft_not_approved", f"{label} #{draft_id} does not exist")]
    issues: list[Issue] = []
    if draft["status"] != "approved":
        issues.append(Issue("draft_not_approved", f"{label} #{draft_id} is '{draft['status']}', not approved"))
    latest = conn.execute(
        "SELECT MAX(version) AS v FROM content_drafts WHERE signal_id = ? AND asset_slot = ?",
        (draft["signal_id"], draft["asset_slot"]),
    ).fetchone()["v"]
    if latest is not None and draft["version"] < latest:
        issues.append(Issue(
            "draft_not_latest",
            f"{label} #{draft_id} is v{draft['version']} but slot '{draft['asset_slot']}' now has v{latest}",
        ))
    for lint_issue in drafts.lint_draft(conn, draft_id):
        issues.append(Issue("lint", f"{label} #{draft_id}: {lint_issue}"))
    approval = latest_approval(conn, draft_id)
    if draft["status"] == "approved":
        if approval is None:
            issues.append(Issue("approval_record", f"{label} #{draft_id} has no approval in the audit trail"))
        else:
            if draft["risk_tier"] in ("amber", "red") and approval["reviewer"] != "founder":
                issues.append(Issue("approval_record",
                                    f"{label} #{draft_id} is {draft['risk_tier']} tier but was approved by "
                                    f"'{approval['reviewer']}', not the founder"))
            if draft["risk_tier"] == "red" and "external check:" not in (approval["decision"] or ""):
                issues.append(Issue("approval_record",
                                    f"{label} #{draft_id} is red tier with no recorded external check"))
    return issues


def approval_freshness(conn: sqlite3.Connection, draft_id: int, tier: str, at: datetime,
                       label: str = "draft") -> list[Issue]:
    max_age = settings.publishing_rules()["approval_max_age_hours"].get(tier)
    if max_age is None:
        return []
    approval = latest_approval(conn, draft_id)
    if approval is None:
        return [Issue("approval_stale", f"{label} #{draft_id} has no approval to be fresh")]
    approved_at = parse_stored(approval["decided_at"])
    if at - approved_at > timedelta(hours=max_age):
        return [Issue(
            "approval_stale",
            f"{label} #{draft_id} ({tier}) was approved {utc_iso(approved_at)}; approvals for {tier} tier "
            f"expire after {max_age}h, so it would be stale at {utc_iso(at)}. Re-approve it.",
        )]
    return []


def signal_gate(conn: sqlite3.Connection, signal_id: str) -> list[Issue]:
    row = conn.execute("SELECT status FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if row and row["status"] in BLOCKING_SIGNAL_STATUSES:
        return [Issue("signal_status", f"signal {signal_id} is {row['status']}")]
    return []


def validate_text(post_format: str, publish_text: str) -> list[Issue]:
    fmt = settings.publishing_rules()["formats"][post_format]
    issues: list[Issue] = []
    if not publish_text.strip():
        issues.append(Issue("text", "publish text is empty"))
    if len(publish_text) > fmt["text_max_chars"]:
        issues.append(Issue("text", f"{len(publish_text)} characters; {post_format} allows {fmt['text_max_chars']}"))
    if "max_hashtags" in fmt and len(HASHTAG_PATTERN.findall(publish_text)) > fmt["max_hashtags"]:
        issues.append(Issue("text", f"more than {fmt['max_hashtags']} hashtags"))
    if "max_mentions" in fmt and len(MENTION_PATTERN.findall(publish_text)) > fmt["max_mentions"]:
        issues.append(Issue("text", f"more than {fmt['max_mentions']} @ mentions"))
    return issues


# ---------------------------------------------------------------- media
def _check_media_row(row, platform_rules: dict, fmt: dict, for_api: bool, check_integrity: bool) -> list[Issue]:
    kind = row["media_kind"]
    rule = platform_rules.get(kind, {})
    where = f"{kind} #{row['position']}"
    issues: list[Issue] = []
    if rule.get("mime") and row["mime_type"] not in rule["mime"]:
        issues.append(Issue("media", f"{where} is {row['mime_type']}; allowed: {', '.join(rule['mime'])}"))
    size = row["byte_size"]
    if size is not None:
        if "max_bytes" in rule and size > rule["max_bytes"]:
            issues.append(Issue("media", f"{where} is {size} bytes; max {rule['max_bytes']}"))
        if "min_bytes" in rule and size < rule["min_bytes"]:
            issues.append(Issue("media", f"{where} is {size} bytes; min {rule['min_bytes']}"))
    needs_dims = any(k in rule for k in ("max_pixels", "min_width", "max_width", "min_aspect", "max_aspect"))
    w, h = row["width"], row["height"]
    if needs_dims and not (w and h):
        issues.append(Issue("media", f"{where}: pixel dimensions unknown (could not be read from the file)"))
    elif w and h:
        if "max_pixels" in rule and w * h >= rule["max_pixels"]:
            issues.append(Issue("media", f"{where} is {w}x{h}; must be under {rule['max_pixels']} pixels"))
        if "min_width" in rule and w < rule["min_width"]:
            issues.append(Issue("media", f"{where} is {w}px wide; min {rule['min_width']}"))
        if "max_width" in rule and w > rule["max_width"]:
            issues.append(Issue("media", f"{where} is {w}px wide; max {rule['max_width']}"))
        aspect = w / h
        if "min_aspect" in rule and aspect < rule["min_aspect"] - 1e-9:
            issues.append(Issue("media", f"{where} aspect {aspect:.2f}; min {rule['min_aspect']}"))
        if "max_aspect" in rule and aspect > rule["max_aspect"] + 1e-9:
            issues.append(Issue("media", f"{where} aspect {aspect:.2f}; max {rule['max_aspect']}"))
    if "min_seconds" in rule or "max_seconds" in rule:
        d = row["duration_seconds"]
        if d is None:
            issues.append(Issue("media", f"{where}: duration not declared (pass --duration)"))
        elif not (rule.get("min_seconds", 0) <= d <= rule.get("max_seconds", float("inf"))):
            issues.append(Issue("media", f"{where} runs {d}s; allowed {rule.get('min_seconds')}-{rule.get('max_seconds')}s"))
    if "max_pages" in rule:
        pages = row["page_count"]
        if pages is None:
            issues.append(Issue("media", f"{where}: page count not declared (pass --pages)"))
        elif pages > rule["max_pages"]:
            issues.append(Issue("media", f"{where} has {pages} pages; max {rule['max_pages']}"))
    if kind == "document" and fmt.get("requires_document_title") and not row["title"]:
        issues.append(Issue("media", f"{where}: a document title is required (pass --title)"))
    if check_integrity and row["local_path"]:
        current = file_sha256(row["local_path"])
        if current is None:
            issues.append(Issue("media", f"{where}: file {row['local_path']} is missing"))
        elif row["sha256"] and current != row["sha256"]:
            issues.append(Issue("media", f"{where}: file changed on disk since it was attached"))
    if for_api:
        need = platform_rules.get("api_needs")
        if need == "public_url" and not (row["public_url"] or "").startswith("https://"):
            issues.append(Issue("media", f"{where}: no https public URL (the platform fetches media from one)"))
        if need == "local_path" and not (row["local_path"] and Path(row["local_path"]).is_file()):
            issues.append(Issue("media", f"{where}: no local file to upload"))
    return issues


def validate_media(conn: sqlite3.Connection, item, *, for_api: bool = False,
                   check_integrity: bool = True) -> list[Issue]:
    rules = settings.publishing_rules()
    fmt = rules["formats"][item["post_format"]]
    platform_rules = rules["media_rules"][item["platform"]]
    rows = conn.execute(
        "SELECT * FROM queue_media WHERE queue_item_id = ? ORDER BY media_kind, position", (item["id"],)
    ).fetchall()
    issues: list[Issue] = []
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["media_kind"]] = counts.get(row["media_kind"], 0) + 1
        if row["media_kind"] not in fmt["media"]:
            issues.append(Issue("media", f"{item['post_format']} takes no {row['media_kind']} media"))
    for kind, (lo, hi) in fmt["media"].items():
        n = counts.get(kind, 0)
        if n < lo:
            issues.append(Issue("media", f"{item['post_format']} needs at least {lo} {kind}(s); has {n}"))
        if n > hi:
            issues.append(Issue("media", f"{item['post_format']} allows at most {hi} {kind}(s); has {n}"))
    for row in rows:
        issues += _check_media_row(row, platform_rules, fmt, for_api, check_integrity)
    return issues


def requires_media(post_format: str) -> bool:
    return any(lo > 0 for lo, _ in settings.publishing_rules()["formats"][post_format]["media"].values())
