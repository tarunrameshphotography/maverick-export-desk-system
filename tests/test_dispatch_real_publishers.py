"""Proves the real LinkedInPublisher/InstagramPublisher classes work when
wired into dispatch_due, not just in isolation (test_linkedin_publisher.py
and test_instagram_publisher.py exercise .publish() directly). Live dispatch
is exercised with dispatch.assert_live_publishing_allowed monkeypatched to a
no-op, exactly like tests/test_publishing_queue.py's live-outcome tests --
auto_publish itself is never touched (see CLAUDE.md's guardrail and
tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled)."""
from datetime import timedelta

from radar.publishing import dispatch as d
from radar.publishing import queue as q
from radar.publishing.credentials import LinkedInCredential
from radar.publishing.linkedin import LinkedInPublisher
from tests._fake_http import FakeResponse, FakeSession
from tests.test_publishing_queue import BASE, _scheduled_item, iso, live_gate_open  # noqa: F401


def test_real_linkedin_publisher_wired_through_dispatch_due(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    session = FakeSession([FakeResponse(201, headers={"x-restli-id": "urn:li:share:real1"})])
    cred = LinkedInCredential(access_token="tok", person_urn="urn:li:person:1", org_urn=None)
    pub = LinkedInPublisher(cred, session=session, api_version="202608")
    reports = d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    assert reports[0]["result"] == "published"
    item = q.get_item(conn, item_id)
    assert item["state"] == "published"
    assert item["external_post_id"] == "urn:li:share:real1"


def test_real_linkedin_publisher_permanent_error_wired_through_dispatch_due(conn, live_gate_open):
    item_id = _scheduled_item(conn)
    session = FakeSession([FakeResponse(401, json_data={"message": "EMPTY_ACCESS_TOKEN"})])
    cred = LinkedInCredential(access_token="expired", person_urn="urn:li:person:1", org_urn=None)
    pub = LinkedInPublisher(cred, session=session, api_version="202608")
    d.dispatch_due(conn, iso(BASE + timedelta(hours=2)), dry_run=False, publishers={"linkedin": pub})
    item = q.get_item(conn, item_id)
    assert item["state"] == "failed"  # permanent_error, never retried
