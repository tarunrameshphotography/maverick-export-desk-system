"""Publishing queue lifecycle tests (Phase 3).

These prove the *state machine*, not just that functions execute: approval
gating, idempotent enqueue, supersession, media/scheduling gates, and the
dispatch outcomes (published / retryable -> backoff -> failed / permanent
error / unknown -> needs_reconciliation / stale lease -> reconciliation).
Live dispatch is exercised with `dispatch.assert_live_publishing_allowed`
monkeypatched to a no-op — `auto_publish` itself must stay hard-refused
(see test_drafts.py::test_auto_publish_flag_cannot_be_enabled) and is never
flipped here.
"""
import struct
from datetime import datetime, timedelta, timezone

import pytest

from radar import settings
from radar.content.drafts import create_content_bundle, record_publication, save_draft_version, set_draft_status
from radar.pipeline.angles import save_angle, select_angle
from radar.pipeline.verify import add_claim
from radar.publishing import dispatch as d
from radar.publishing import queue as q
from radar.publishing import validation as v

BASE = datetime(2026, 9, 14, 9, 0, 0, tzinfo=timezone.utc)  # Monday
DISCLOSURE = settings.content_rules()["disclosure_line"]


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


NOW = iso(BASE)


def _seed(conn, sid, category="market_intelligence", schemes=()):
    conn.execute(
        """
        INSERT INTO signals (id, title, first_seen_at, topic_category, status, primary_source_url,
                              created_at, updated_at)
        VALUES (?, 'Test notice', ?, ?, 'SCORED', 'https://dgft.gov.in/n74', ?, ?)
        """,
        (sid, NOW, category, NOW, NOW),
    )
    for scheme in schemes:
        conn.execute(
            "INSERT INTO signal_entities (signal_id, entity_type, raw_value, normalized_value) VALUES (?, 'scheme', ?, ?)",
            (sid, scheme.lower(), scheme),
        )
    conn.commit()
    add_claim(
        conn, sid, "The notice moves the compliance deadline to 30 September 2026", "fact",
        "https://dgft.gov.in/n74", "primary", NOW,
        source_quote="the compliance deadline is extended to 30.09.2026", verified_by="analyst",
    )
    aid = save_angle(conn, sid, "Countdown", "timing_transition_risk", "Deadline moves",
                     "The deadline moves to 30 September; exporters should act now.", "claude_code", NOW)
    select_angle(conn, aid)
    return sid


def _clean_linkedin_body() -> str:
    text = ("The notice moves the compliance deadline and exporters must file paperwork before then. " * 12).strip()
    return text + "\n\n" + DISCLOSURE


def _clean_short_body(variant: str = "caption") -> str:
    text = {
        "caption": "The notice moves the deadline and exporters should act before it lapses.",
        "reel": "The notice moves the deadline; here is what changes on the floor this week.",
    }[variant]
    return text + "\n\n" + DISCLOSURE


def _approve(conn, draft_id, reviewer, external_check_by=None):
    body = _clean_linkedin_body() if draft_id else None
    set_draft_status(conn, draft_id, "approved", reviewer, NOW, external_check_by=external_check_by)


def _approved_draft(conn, bundle, slot, reviewer="founder", external_check_by=None):
    if slot.startswith("linkedin"):
        body = _clean_linkedin_body()
    else:
        body = _clean_short_body("reel" if "reel" in slot else "caption")
    new_id = save_draft_version(conn, bundle[slot], NOW, "analyst", body=body,
                                visual_brief="Deadline chart", exposure_line="Exposure figure pending.")
    set_draft_status(conn, new_id, "approved", reviewer, NOW, external_check_by=external_check_by)
    return new_id


def _seeded_bundle(conn, sid="SIG-Q-1", category="market_intelligence", schemes=(), reviewer="founder", ec=None):
    _seed(conn, sid, category, schemes)
    bundle = create_content_bundle(conn, sid, NOW)
    li = _approved_draft(conn, bundle, "linkedin_post_1", reviewer, ec)
    cap = _approved_draft(conn, bundle, "instagram_caption", reviewer, ec)
    reel = _approved_draft(conn, bundle, "instagram_reel_1", reviewer, ec)
    return sid, bundle, {"linkedin_post_1": li, "instagram_caption": cap, "instagram_reel_1": reel}


# ---------------------------------------------------------------- media fixtures
def _jpeg_bytes(width=1080, height=1080) -> bytes:
    header = bytes([0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x0B, 0x08]) + struct.pack(">HH", height, width) + bytes([0x01, 0x01, 0x11, 0x00])
    return header


def _write_jpeg(tmp_path, name="photo.jpg", width=1080, height=1080):
    p = tmp_path / name
    p.write_bytes(_jpeg_bytes(width, height))
    return str(p)


def _write_mp4(tmp_path, name="clip.mp4", payload=b"fake mp4 bytes"):
    p = tmp_path / name
    p.write_bytes(payload)
    return str(p)


# ================================================================== enqueue / approval gating
def test_enqueue_requires_an_approved_draft(conn):
    _seed(conn, "SIG-Q-1")
    bundle = create_content_bundle(conn, "SIG-Q-1", NOW)
    with pytest.raises(q.QueueError, match="not approved"):
        q.enqueue(conn, bundle["linkedin_post_1"], "founder", NOW)


def test_enqueue_refuses_an_internal_slot(conn):
    _seed(conn, "SIG-Q-1")
    bundle = create_content_bundle(conn, "SIG-Q-1", NOW)
    with pytest.raises(q.QueueError, match="internal asset"):
        q.enqueue(conn, bundle["visual_direction"], "founder", NOW)


def test_enqueue_refuses_a_stale_draft_version(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    stale = approved["linkedin_post_1"]
    save_draft_version(conn, stale, NOW, "analyst", headline="A newer take")  # now has a v3
    with pytest.raises(q.QueueError, match="now has v"):
        q.enqueue(conn, stale, "founder", NOW)


def test_enqueue_is_idempotent(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    id1, created1 = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    id2, created2 = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    assert created1 is True
    assert created2 is False
    assert id1 == id2
    assert conn.execute("SELECT COUNT(*) FROM publish_queue").fetchone()[0] == 1


def test_enqueue_refuses_repeating_the_same_signal_without_an_override(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    li2 = _approved_draft(conn, bundle, "linkedin_post_2")
    q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    with pytest.raises(q.QueueError, match="already has"):
        q.enqueue(conn, li2, "founder", NOW)
    item_id, created = q.enqueue(conn, li2, "founder", NOW, allow_repeat_reason="two variants, editorial call")
    assert created is True


def test_enqueue_reel_requires_an_approved_caption(conn):
    _seed(conn, "SIG-Q-1")
    bundle = create_content_bundle(conn, "SIG-Q-1", NOW)
    reel = _approved_draft(conn, bundle, "instagram_reel_1")  # caption still unapproved
    with pytest.raises(q.QueueError, match="caption draft"):
        q.enqueue(conn, reel, "founder", NOW, post_format="instagram_reel")


def test_enqueue_reel_publishes_the_caption_text_not_the_script(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_reel_1"], "founder", NOW, post_format="instagram_reel")
    item = q.get_item(conn, item_id)
    caption_body = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (approved["instagram_caption"],)).fetchone()["body"]
    assert item["publish_text"] == caption_body
    reel_body = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (approved["instagram_reel_1"],)).fetchone()["body"]
    assert item["publish_text"] != reel_body
    assert item["state"] == "awaiting_media"  # instagram_reel needs a video


# ================================================================== media
def test_media_starts_awaiting_media_and_moves_to_queued_once_valid(conn, tmp_path):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_reel_1"], "founder", NOW, post_format="instagram_reel")
    assert q.get_item(conn, item_id)["state"] == "awaiting_media"
    video = _write_mp4(tmp_path)
    q.attach_media(conn, item_id, video, "video", "founder", NOW, duration_seconds=30)
    assert q.get_item(conn, item_id)["state"] == "queued"


def test_attach_media_rejects_a_kind_the_format_does_not_take(conn, tmp_path):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_caption"], "founder", NOW, post_format="instagram_image")
    doc = _write_mp4(tmp_path, name="doc.mp4")
    with pytest.raises(q.QueueError, match="takes"):
        q.attach_media(conn, item_id, doc, "video", "founder", NOW)


def test_attach_media_unrecognised_file_type_refused(conn, tmp_path):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_caption"], "founder", NOW, post_format="instagram_image")
    bogus = tmp_path / "photo.xyz"
    bogus.write_bytes(b"not an image")
    with pytest.raises(q.QueueError, match="Unrecognised media type"):
        q.attach_media(conn, item_id, str(bogus), "image", "founder", NOW)


def test_media_file_changed_on_disk_after_attach_is_flagged(conn, tmp_path):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_caption"], "founder", NOW, post_format="instagram_image")
    photo = _write_jpeg(tmp_path)
    q.attach_media(conn, item_id, photo, "image", "founder", NOW)
    assert q.get_item(conn, item_id)["state"] == "queued"
    with open(photo, "ab") as f:
        f.write(b"tampered")
    issues = v.validate_media(conn, q.get_item(conn, item_id))
    assert any("changed on disk" in str(i) for i in issues)


# ================================================================== scheduling
def test_schedule_refuses_a_past_time(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    past = iso(BASE - timedelta(days=1))
    with pytest.raises(q.QueueError, match="in the past"):
        q.schedule(conn, item_id, past, "founder", NOW)


def test_amber_tier_scheduling_requires_the_founder(conn):
    sid, bundle, approved = _seeded_bundle(conn, category="trade_statistics")  # amber, no disclosure
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    when = iso(BASE + timedelta(hours=2))
    with pytest.raises(q.QueueError, match="founder"):
        q.schedule(conn, item_id, when, "analyst", NOW)
    at = q.schedule(conn, item_id, when, "founder", NOW)
    assert at == v.utc_iso(v.parse_stored(when))


def test_schedule_blocks_a_weekend_post_for_linkedin_unless_overridden(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    saturday = iso(datetime(2026, 9, 19, 10, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(q.QueueError, match="cadence"):
        q.schedule(conn, item_id, saturday, "founder", NOW)
    q.schedule(conn, item_id, saturday, "founder", NOW, override_reason="founder wants a weekend post this once")
    assert q.get_item(conn, item_id)["state"] == "scheduled"


def test_rescheduling_moves_the_time_and_unscheduling_returns_to_queued(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    t1 = iso(BASE + timedelta(hours=2))
    t2 = iso(BASE + timedelta(hours=3))
    q.schedule(conn, item_id, t1, "founder", NOW)
    at2 = q.schedule(conn, item_id, t2, "founder", NOW)
    assert at2 == v.utc_iso(v.parse_stored(t2))
    q.unschedule(conn, item_id, "founder", NOW, "founder wants to re-time it")
    item = q.get_item(conn, item_id)
    assert item["state"] == "queued"
    assert item["scheduled_at"] is None


def test_cancel_requires_a_reason(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    with pytest.raises(q.QueueError, match="reason"):
        q.cancel(conn, item_id, "founder", NOW, "")
    q.cancel(conn, item_id, "founder", NOW, "story dropped")
    assert q.get_item(conn, item_id)["state"] == "cancelled"


# ================================================================== supersession
def test_a_newer_draft_version_supersedes_the_queued_item(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    save_draft_version(conn, approved["linkedin_post_1"], NOW, "analyst", headline="A sharper hook")
    assert q.get_item(conn, item_id)["state"] == "superseded"


def test_rejecting_the_draft_supersedes_the_queued_item(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    set_draft_status(conn, approved["linkedin_post_1"], "rejected", "founder", NOW, reason_code="Too promotional")
    assert q.get_item(conn, item_id)["state"] == "superseded"


def test_a_new_caption_version_supersedes_a_reel_item_using_it(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["instagram_reel_1"], "founder", NOW, post_format="instagram_reel")
    save_draft_version(conn, approved["instagram_caption"], NOW, "analyst", headline="A different caption angle")
    assert q.get_item(conn, item_id)["state"] == "superseded"


# ================================================================== invalid transitions / requeue
def test_a_terminal_item_refuses_further_transitions(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    q.cancel(conn, item_id, "founder", NOW, "dropped")
    with pytest.raises(q.QueueError, match="cannot move from cancelled"):
        q.cancel(conn, item_id, "founder", NOW, "cancel it again")


def test_requeue_gives_a_fresh_attempt_budget(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    conn.execute("UPDATE publish_queue SET state = 'failed', attempt_count = 3, max_attempts = 3 WHERE id = ?", (item_id,))
    conn.commit()
    target = q.requeue(conn, item_id, "founder", NOW, "fixed the underlying issue")
    item = q.get_item(conn, item_id)
    assert target == "queued"
    assert item["state"] == "queued"
    assert item["attempt_count"] == 3  # numbering keeps counting up, not reset
    assert item["max_attempts"] == 3 + settings.publishing_rules()["retry"]["max_attempts"]


def test_requeue_of_a_no_longer_approved_draft_is_refused(conn):
    # Simulates content going unapproved through a path the supersession hooks
    # in drafts.py don't cover (e.g. a direct DB edit) rather than via
    # set_draft_status/save_draft_version, which would supersede it outright.
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    conn.execute("UPDATE publish_queue SET state = 'held' WHERE id = ?", (item_id,))
    conn.execute("UPDATE content_drafts SET status = 'rejected' WHERE id = ?", (approved["linkedin_post_1"],))
    conn.commit()
    with pytest.raises(q.QueueError, match="no longer approved"):
        q.requeue(conn, item_id, "founder", NOW, "trying anyway")


# ================================================================== dispatch: dry run / gate
def test_dry_run_dispatch_changes_no_state(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    when = iso(BASE + timedelta(hours=1))
    q.schedule(conn, item_id, when, "founder", NOW)
    reports = d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=True)
    assert len(reports) == 1
    assert reports[0]["result"] == "would publish"
    assert q.get_item(conn, item_id)["state"] == "scheduled"


def test_dispatch_skips_items_not_yet_due(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    when = iso(BASE + timedelta(hours=5))
    q.schedule(conn, item_id, when, "founder", NOW)
    reports = d.dispatch_due(conn, iso(BASE + timedelta(hours=1)), dry_run=True)
    assert reports == []


def test_live_dispatch_is_refused_while_auto_publish_is_disabled(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    q.schedule(conn, item_id, iso(BASE + timedelta(hours=1)), "founder", NOW)
    with pytest.raises(d.PublishingDisabledError, match="auto_publish is false"):
        d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False)
    assert q.get_item(conn, item_id)["state"] == "scheduled"  # nothing changed


# ================================================================== dispatch: live outcomes (gate monkeypatched)
class _FakePublisher:
    def __init__(self, platform, result_fn):
        self.name = "fake_" + platform
        self.platform = platform
        self._result_fn = result_fn
        self.calls = 0

    def publish(self, payload):
        self.calls += 1
        return self._result_fn(payload)


@pytest.fixture()
def live_gate_open(monkeypatch):
    monkeypatch.setattr(d, "assert_live_publishing_allowed", lambda: None)


def _scheduled_item(conn, when_offset_hours=1):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    q.schedule(conn, item_id, iso(BASE + timedelta(hours=when_offset_hours)), "founder", NOW)
    return item_id


def test_live_dispatch_success_writes_published_content(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("published", external_post_id="urn:li:share:1",
                                                              external_url="https://linkedin.com/x/1"))
    reports = d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    assert reports[0]["result"] == "published"
    item = q.get_item(conn, item_id)
    assert item["state"] == "published"
    assert item["external_post_id"] == "urn:li:share:1"
    pc = conn.execute("SELECT * FROM published_content WHERE queue_item_id = ?", (item_id,)).fetchone()
    assert pc["platform_post_id"] == "urn:li:share:1"
    assert pub.calls == 1


def test_live_dispatch_success_without_a_post_id_is_not_trusted(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("published"))  # no id, no url
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    assert q.get_item(conn, item_id)["state"] == "needs_reconciliation"


def test_live_dispatch_permanent_error_fails_immediately(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("permanent_error", error_code="policy_violation"))
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    item = q.get_item(conn, item_id)
    assert item["state"] == "failed"
    assert item["last_error_code"] == "policy_violation"


def test_live_dispatch_publisher_exception_is_treated_as_unknown_and_not_retried(conn, live_gate_open):
    item_id = _scheduled_item(conn)

    def boom(payload):
        raise RuntimeError("network exploded")

    pub = _FakePublisher("linkedin", boom)
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    item = q.get_item(conn, item_id)
    assert item["state"] == "needs_reconciliation"
    assert item["last_error_code"] == "publisher_exception"
    # needs_reconciliation items are never picked up by due_items again
    again = d.dispatch_due(conn, iso(BASE + timedelta(hours=3)), dry_run=False, publishers={"linkedin": pub})
    assert again == []
    assert pub.calls == 1


def test_live_dispatch_retryable_error_backs_off_then_fails_at_max_attempts(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("retryable_error", error_code="rate_limited"))
    max_attempts = settings.publishing_rules()["retry"]["max_attempts"]

    t = BASE + timedelta(hours=2)
    for expected_attempt in range(1, max_attempts + 1):
        d.dispatch_due(conn, iso(t), dry_run=False, publishers={"linkedin": pub})
        item = q.get_item(conn, item_id)
        assert item["attempt_count"] == expected_attempt
        if expected_attempt < max_attempts:
            assert item["state"] == "retry_pending"
            t = v.parse_stored(item["next_attempt_at"]) + timedelta(minutes=1)
        else:
            assert item["state"] == "failed"
    assert pub.calls == max_attempts


def test_stale_publishing_lease_goes_to_reconciliation_never_retry(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    item = q.get_item(conn, item_id)
    from radar.publishing.dispatch import build_payload
    payload = build_payload(conn, item)
    claim_time = iso(BASE + timedelta(hours=2))
    attempt_id = d._claim(conn, item, "fake_linkedin", payload, claim_time)
    assert attempt_id is not None
    assert q.get_item(conn, item_id)["state"] == "publishing"
    later = iso(BASE + timedelta(hours=2, minutes=settings.publishing_rules()["lease_minutes"] + 5))
    recovered = d.recover_stale_leases(conn, later)
    assert recovered == [item_id]
    assert q.get_item(conn, item_id)["state"] == "needs_reconciliation"


def test_cas_claim_refuses_an_item_that_already_moved(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    item = q.get_item(conn, item_id)
    q.cancel(conn, item_id, "founder", NOW, "dropped after scheduling")
    from radar.publishing.dispatch import build_payload
    payload = build_payload(conn, item)  # stale in-memory row, still 'scheduled'
    result = d._claim(conn, item, "fake_linkedin", payload, iso(BASE + timedelta(hours=2)))
    assert result is None
    assert q.get_item(conn, item_id)["state"] == "cancelled"  # untouched


# ================================================================== manual publish / reconcile
def test_mark_published_manually_prevents_redispatch(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    q.mark_published_manually(conn, item_id, "https://linkedin.com/x/9", "founder", NOW, external_post_id="9")
    item = q.get_item(conn, item_id)
    assert item["state"] == "published"
    with pytest.raises(q.QueueError, match="cannot be recorded as published"):
        q.mark_published_manually(conn, item_id, "https://linkedin.com/x/9", "founder", NOW)


def test_reconcile_published_writes_the_publication_record(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("published"))  # forced to unknown
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    assert q.get_item(conn, item_id)["state"] == "needs_reconciliation"
    q.reconcile(conn, item_id, "published", "founder", iso(BASE + timedelta(hours=3)),
               "checked the LinkedIn feed by hand", url="https://linkedin.com/x/9", external_post_id="9")
    item = q.get_item(conn, item_id)
    assert item["state"] == "published"
    assert item["external_post_id"] == "9"


def test_reconcile_not_published_returns_to_retry(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    from radar.publishing.dispatch import PublishResult
    pub = _FakePublisher("linkedin", lambda p: PublishResult("published"))
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    q.reconcile(conn, item_id, "not_published", "founder", iso(BASE + timedelta(hours=3)), "not on the feed; safe to retry")
    item = q.get_item(conn, item_id)
    assert item["state"] == "retry_pending"


def test_record_publication_reconciles_the_matching_queue_item_and_refuses_a_duplicate(conn):
    sid, bundle, approved = _seeded_bundle(conn)
    item_id, _ = q.enqueue(conn, approved["linkedin_post_1"], "founder", NOW)
    body = conn.execute("SELECT body FROM content_drafts WHERE id = ?", (approved["linkedin_post_1"],)).fetchone()["body"]
    pid = record_publication(conn, approved["linkedin_post_1"], "https://linkedin.com/x/7", body, NOW)
    assert pid > 0
    assert q.get_item(conn, item_id)["state"] == "published"
    with pytest.raises(ValueError, match="already"):
        record_publication(conn, approved["linkedin_post_1"], "https://linkedin.com/x/7", body, NOW)
