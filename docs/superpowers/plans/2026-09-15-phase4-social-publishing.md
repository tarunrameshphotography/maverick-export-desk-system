# Phase 4 — Social Publishing Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `LinkedInPublisher` and `InstagramPublisher` — the two concrete classes that satisfy `radar/publishing/dispatch.py`'s `Publisher` protocol — so `queue-dispatch --live` has real platform adapters the moment `auto_publish` is ever deliberately flipped. This phase writes code, tests, and docs only; it makes no real HTTP call to either platform and does not touch `auto_publish`.

**Architecture:** Two new platform-specific modules (`radar/publishing/linkedin.py`, `radar/publishing/instagram.py`) plus two small shared modules (`radar/publishing/platform_http.py` for HTTP-outcome classification, `radar/publishing/credentials.py` for env-var credential loading). Each publisher takes an injected `requests.Session`-like object so the full test suite runs with zero real network calls, matching how `radar/content/generation.py` tests inject a fake `GenerateFn`.

**Tech Stack:** Python, `requests` (already a dependency), `pytest`, SQLite (`radar/db/`). No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md`

## Global Constraints

- `auto_publish` stays hard-refused exactly as `settings.feature_flags()` enforces it today — no task touches `radar/settings.py` or `tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled`.
- Credentials come from environment variables only, read exclusively inside `radar/publishing/credentials.py` — `linkedin.py` and `instagram.py` never call `os.environ` directly.
- No task performs a real HTTP call to LinkedIn or Instagram's actual APIs. All publisher tests inject a fake session object.
- LinkedIn's `Linkedin-Version` header value is verified against `learn.microsoft.com/en-us/linkedin/marketing/community-management/...` (checked 2026-09-15, `li-lms-2026-08` moniker → header value `"202608"`) and is read from `radar/config/publishing.yaml`, never hard-coded in `linkedin.py`.
- LinkedIn's organic **MultiImage** post (`content.multiImage.images`, 2–20 images) is implemented as its own format — it is explicitly *not* the same as LinkedIn's Carousel, which the docs confirm is sponsored-only and out of scope here.
- A 401/403 from either platform always classifies as `permanent_error`, never retried automatically (a human must re-supply credentials).
- Commit after every task. Push only in the final task, after the full suite passes.

---

## Verified API facts this plan relies on (checked 2026-09-15)

**LinkedIn** — [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-08), [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api?view=li-lms-2026-08), [Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api?view=li-lms-2026-08), [Documents API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/documents-api?view=li-lms-2026-08), [MultiImage API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/multiimage-post-api?view=li-lms-2026-08):
- Every call needs headers `Authorization: Bearer {token}`, `Linkedin-Version: {YYYYMM}` (current: `202608`), `X-Restli-Protocol-Version: 2.0.0`, `Content-Type: application/json` (JSON calls only).
- `POST https://api.linkedin.com/rest/posts` creates any post (text/image/multiImage/document/video via `content.media` or `content.multiImage`). Success is `201`; the post ID is in the **`x-restli-id` response header**, not the body.
- Image upload: `POST /rest/images?action=initializeUpload` `{"initializeUploadRequest":{"owner": urn}}` → `200` `{"value":{"uploadUrl":..., "image": "urn:li:image:..."}}`. Then `PUT` the raw bytes to `uploadUrl` **with** `Authorization: Bearer {token}` → `201`. Reference as `content.media.id` (single image) or `content.multiImage.images[].id` (multi-image, 2–20 images, each with optional `altText`).
- Document upload: `POST /rest/documents?action=initializeUpload` `{"initializeUploadRequest":{"owner": urn}}` → `200` `{"value":{"uploadUrl":..., "document": "urn:li:document:..."}}`. `PUT` bytes to `uploadUrl` with `Authorization` header → `201`. Reference as `content.media = {"id": urn, "title": "..."}` — title is required (100MB / 300-page / PDF·PPT·PPTX·DOC·DOCX limit, already enforced by `validation.py`).
- Video upload (Videos API, not the deprecated Assets API): `POST /rest/videos?action=initializeUpload` `{"initializeUploadRequest":{"owner": urn, "fileSizeBytes": N, "uploadCaptions": false, "uploadThumbnail": false}}` → `200` `{"value":{"video": urn, "uploadToken": "...", "uploadInstructions":[{"uploadUrl":..., "firstByte":0, "lastByte":4194303}, ...]}}` — **always** multi-part regardless of size, in fixed byte ranges. `PUT` each byte-range slice to its `uploadUrl` with `Content-Type: application/octet-stream` and **no** `Authorization` header (video upload calls are unauthenticated per LinkedIn's docs) → `200`, and the response's `ETag` header is the part ID. Then `POST /rest/videos?action=finalizeUpload` `{"finalizeUploadRequest":{"video": urn, "uploadToken": token, "uploadedPartIds": [etag, ...]}}` (ETags in the same order as the parts) → `200`. Reference as `content.media.id`. Limits: 3s–30min, 75KB–500MB, MP4 only (matches `publishing.yaml`'s existing `media_rules.linkedin.video`).
- Error codes seen on `/rest/posts`, `/rest/images`, `/rest/videos`, `/rest/documents`: `400` (`INVALID_URN_TYPE`/`INVALID_URN_ID`/`MISSING_FIELD`/`FIELD_LENGTH_TOO_LONG`/etc.), `401 EMPTY_ACCESS_TOKEN`, `403 ACCESS_DENIED`/`DOCUMENT_FORBIDDEN`, `404 NOT_FOUND`, `409 CONFLICT`, `422 UNPROCESSABLE_ENTITY`, `429 TOO_MANY_REQUESTS`, `500`, `503`.
- **Risk to flag, not to solve here:** `w_organization_social`/`w_member_social` scopes and the Posts/Images/Videos/Documents API family sit behind LinkedIn's Community Management API product, which requires an approved LinkedIn Developer Program application — this is a founder action outside this codebase, separate from getting an OAuth token once approved.

**Instagram / Meta** — [Content Publishing guide](https://developers.facebook.com/docs/instagram-platform/content-publishing/), current version `v25.0` (checked 2026-09-15; Meta ships a new major roughly quarterly, `v26.0` was "expected around September 2026" as of the last checked changelog — **re-verify the version string against the live docs before ever flipping `auto_publish`**, since this plan pins `v25.0` from research, not from a live call):
- Base: `https://graph.facebook.com/{version}/{ig-user-id}/...`; `access_token` is sent as a **request parameter** (form/query), not an `Authorization` header.
- Image: `POST /{ig-user-id}/media` with `image_url`, `caption`, `access_token` → `{"id": creation_id}`. Then `POST /{ig-user-id}/media_publish` with `creation_id`, `access_token` → `{"id": media_id}`.
- Carousel: create up to 10 child containers with `image_url` (or `video_url`) + `is_carousel_item=true` each, then a parent container with `media_type=CAROUSEL`, `children=<comma-separated ids>`, `caption`, then publish the parent's `creation_id`.
- Reel: create container with `media_type=REELS`, `video_url`, optional `cover_url`, `caption`. Poll `GET /{container-id}?fields=status_code` — Meta recommends "once per minute, for no more than 5 minutes." `status_code` is one of `IN_PROGRESS`/`FINISHED`/`ERROR`/`EXPIRED`/`PUBLISHED`. Publish only once `FINISHED`.
- Rate limit: 100 API-published posts per rolling 24h (carousels count as one); queryable at `GET /{ig-user-id}/content_publishing_limit` (not used by this plan — informational only).
- **Error shape is body-based, not status-based**: Graph API frequently returns HTTP `400` for both malformed requests *and* auth failures, with the real signal in the JSON body's `error.code`/`error.type` (`type: "OAuthException"` or `code: 190` → invalid/expired token → `permanent_error`; documented transient/rate-limit codes `4`, `17`, `32`, `613` → `retryable_error`). `instagram.py` inspects the body before falling back to `platform_http`'s generic status-code classification.

---

## File Structure

```
radar/publishing/
  platform_http.py    NEW — shared HTTP-outcome classification (pure function, no I/O)
  credentials.py       NEW — env-var credential loading (the refresh seam)
  linkedin.py           NEW — LinkedInPublisher
  instagram.py          NEW — InstagramPublisher
radar/cli.py            MODIFY — cmd_queue_dispatch builds a publishers dict from credentials
radar/config/publishing.yaml   MODIFY — add `platform_apis.linkedin.api_version`,
                                        `platform_apis.instagram.{api_version,reel_status_poll_attempts,reel_status_poll_interval_seconds}`
.env.example             MODIFY — document the 5 new env vars
tests/
  _fake_http.py         NEW — shared FakeResponse/FakeSession test double
  test_platform_http.py NEW
  test_credentials.py    NEW
  test_linkedin_publisher.py    NEW
  test_instagram_publisher.py   NEW
  test_dispatch_real_publishers.py  NEW — wires the real classes through dispatch_due
INTELLIGENCE SYSTEM/roadmap.md      MODIFY — Phase 4 section
INTELLIGENCE SYSTEM/milestones.md   MODIFY — new dated entry
```

**Interfaces every task can rely on** (from `radar/publishing/dispatch.py`, already built in Phase 3 — read-only for this plan):
```python
@dataclass
class PublishPayload:
    item_id: int
    idempotency_key: str
    platform: str
    post_format: str          # e.g. "linkedin_text", "linkedin_image", "linkedin_multi_image",
                               # "linkedin_document", "linkedin_video", "instagram_image",
                               # "instagram_carousel", "instagram_reel"
    target_account: str       # "founder_profile" | "company_page" | "business_account"
    text: str
    first_comment: str | None
    media: list[dict] = field(default_factory=list)
    # each media dict has keys: media_kind, position, local_path, public_url, mime_type,
    # byte_size, sha256, width, height, duration_seconds, page_count, alt_text, title
    risk_tier: str = "red"
    signal_id: str = ""
    draft_id: int = 0

@dataclass
class PublishResult:
    outcome: str  # "published" | "retryable_error" | "permanent_error" | "unknown"
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
```

---

### Task 1: Shared HTTP-outcome classification

**Files:**
- Create: `radar/publishing/platform_http.py`
- Test: `tests/test_platform_http.py`

**Interfaces:**
- Produces: `classify_response(status: int | None, headers: dict | None = None, exc: Exception | None = None) -> tuple[str, int | None]` returning `(outcome, retry_after_seconds)` where `outcome` is one of `"ok"`, `"retryable_error"`, `"permanent_error"`, `"unknown"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_platform_http.py
from radar.publishing import platform_http as ph


def test_exception_is_always_unknown():
    assert ph.classify_response(None, exc=ConnectionError("boom")) == ("unknown", None)


def test_no_status_is_unknown():
    assert ph.classify_response(None) == ("unknown", None)


def test_429_with_retry_after_header():
    outcome, retry_after = ph.classify_response(429, headers={"Retry-After": "30"})
    assert outcome == "retryable_error"
    assert retry_after == 30


def test_429_without_retry_after_header():
    outcome, retry_after = ph.classify_response(429, headers={})
    assert outcome == "retryable_error"
    assert retry_after is None


def test_401_is_permanent_not_retryable():
    assert ph.classify_response(401) == ("permanent_error", None)


def test_403_is_permanent():
    assert ph.classify_response(403) == ("permanent_error", None)


def test_500_is_retryable():
    assert ph.classify_response(500) == ("retryable_error", None)


def test_503_is_retryable():
    assert ph.classify_response(503) == ("retryable_error", None)


def test_400_is_permanent():
    assert ph.classify_response(400) == ("permanent_error", None)


def test_404_is_permanent():
    assert ph.classify_response(404) == ("permanent_error", None)


def test_2xx_is_ok():
    assert ph.classify_response(200) == ("ok", None)
    assert ph.classify_response(201) == ("ok", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_platform_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'radar.publishing.platform_http'`

- [ ] **Step 3: Write the implementation**

```python
# radar/publishing/platform_http.py
"""Shared HTTP-outcome classification for platform publishers. This module
knows nothing about LinkedIn or Instagram specifically — it only turns a
transport-level result into the outcome vocabulary dispatch.py already
uses (retryable_error / permanent_error / unknown), plus "ok" for a 2xx the
caller must still parse to decide whether it's a real success. A platform
module (linkedin.py, instagram.py) is the only place that knows what a
successful body looks like for a given call."""
from __future__ import annotations


def classify_response(
    status: int | None,
    headers: dict | None = None,
    exc: Exception | None = None,
) -> tuple[str, int | None]:
    if exc is not None:
        return "unknown", None
    headers = headers or {}
    if status is None:
        return "unknown", None
    if status == 429:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        seconds = int(retry_after) if retry_after and str(retry_after).isdigit() else None
        return "retryable_error", seconds
    if status in (401, 403):
        return "permanent_error", None
    if status >= 500:
        return "retryable_error", None
    if 400 <= status < 500:
        return "permanent_error", None
    if 200 <= status < 300:
        return "ok", None
    return "unknown", None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_platform_http.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/platform_http.py tests/test_platform_http.py
git commit -m "Add shared HTTP-outcome classification for Phase 4 publishers"
```

---

### Task 2: Credential layer (env vars, refresh-ready seam)

**Files:**
- Create: `radar/publishing/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `LinkedInCredential(access_token, person_urn, org_urn)`, `InstagramCredential(access_token, business_account_id)` dataclasses; `linkedin_credential() -> LinkedInCredential | None`, `instagram_credential() -> InstagramCredential | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_credentials.py
from radar.publishing import credentials as c


def test_linkedin_credential_none_without_token(monkeypatch):
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    assert c.linkedin_credential() is None


def test_linkedin_credential_reads_all_three_vars(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok-123")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:1")
    monkeypatch.setenv("LINKEDIN_ORG_URN", "urn:li:organization:2")
    cred = c.linkedin_credential()
    assert cred.access_token == "tok-123"
    assert cred.person_urn == "urn:li:person:1"
    assert cred.org_urn == "urn:li:organization:2"


def test_linkedin_credential_urns_optional(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok-123")
    monkeypatch.delenv("LINKEDIN_PERSON_URN", raising=False)
    monkeypatch.delenv("LINKEDIN_ORG_URN", raising=False)
    cred = c.linkedin_credential()
    assert cred.person_urn is None
    assert cred.org_urn is None


def test_instagram_credential_none_without_token(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "17800000")
    assert c.instagram_credential() is None


def test_instagram_credential_none_without_account_id(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok-456")
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)
    assert c.instagram_credential() is None


def test_instagram_credential_reads_both_vars(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok-456")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "17800000")
    cred = c.instagram_credential()
    assert cred.access_token == "tok-456"
    assert cred.business_account_id == "17800000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# radar/publishing/credentials.py
"""Reads platform credentials from environment variables only. This is the
one seam a future token-refresh mechanism replaces: LinkedInPublisher and
InstagramPublisher never call os.environ themselves, only this module does,
and only through the two functions below. A refresh implementation later
would change the body of these two functions (e.g. read a refresh token,
call the platform's token endpoint, write the new token back) without
touching either publisher's constructor signature."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LinkedInCredential:
    access_token: str
    person_urn: str | None
    org_urn: str | None


@dataclass(frozen=True)
class InstagramCredential:
    access_token: str
    business_account_id: str


def linkedin_credential() -> LinkedInCredential | None:
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        return None
    return LinkedInCredential(
        access_token=token,
        person_urn=os.environ.get("LINKEDIN_PERSON_URN") or None,
        org_urn=os.environ.get("LINKEDIN_ORG_URN") or None,
    )


def instagram_credential() -> InstagramCredential | None:
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    account_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID")
    if not token or not account_id:
        return None
    return InstagramCredential(access_token=token, business_account_id=account_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_credentials.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/credentials.py tests/test_credentials.py
git commit -m "Add env-var credential loading for Phase 4 publishers"
```

---

### Task 3: Shared fake HTTP test double + config keys

**Files:**
- Create: `tests/_fake_http.py`
- Modify: `radar/config/publishing.yaml`

**Interfaces:**
- Produces: `FakeResponse(status_code, json_data=None, headers=None, text="")`, `FakeSession(responses: list[FakeResponse])` with `.post/.put/.get(url, **kwargs)` consuming `responses` in call order and recording `.calls`.
- Produces (config): `settings.publishing_rules()["platform_apis"]["linkedin"]["api_version"]` and `["platform_apis"]["instagram"]["api_version" | "reel_status_poll_attempts" | "reel_status_poll_interval_seconds"]`.

- [ ] **Step 1: Write the fake HTTP double**

```python
# tests/_fake_http.py
"""A minimal fake requests.Session for Phase 4 publisher tests — no real
network calls. Responses are consumed in the order publish() makes calls,
so each test lists exactly the responses its call sequence needs."""
from __future__ import annotations


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body on this fake response")
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"FakeSession ran out of scripted responses at {method} {url}")
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._next("PUT", url, **kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)
```

- [ ] **Step 2: Add the config keys**

Read `radar/config/publishing.yaml` first (it already exists from Phase 3), then add this block near the top, right after the file's existing header comment block and before `timezone_offset:`:

```yaml
# LinkedIn Posts/Images/Videos/Documents/MultiImage API contracts verified
# against learn.microsoft.com/en-us/linkedin/marketing/community-management/
# (moniker li-lms-2026-08) on 2026-09-15. The Linkedin-Version header uses
# YYYYMM; li-lms-2026-08 -> "202608". LinkedIn's docs carry an Aug 2026
# deprecation notice against the prior 202508 version and list monikers
# through li-lms-2026-08 as of this check — re-verify before this value
# sunsets.
# Instagram Graph API content-publishing contract verified against
# developers.facebook.com/docs/instagram-platform/content-publishing/ on
# 2026-09-15; v25.0 was the current documented version (Meta ships a new
# major roughly quarterly — re-check before flipping auto_publish, since
# this value was not confirmed against a live call).
platform_apis:
  linkedin:
    api_version: "202608"
  instagram:
    api_version: "v25.0"
    reel_status_poll_attempts: 5
    reel_status_poll_interval_seconds: 60
```

- [ ] **Step 3: Verify the config loads**

Run: `python -c "from radar import settings; print(settings.publishing_rules()['platform_apis'])"`
Expected: `{'linkedin': {'api_version': '202608'}, 'instagram': {'api_version': 'v25.0', 'reel_status_poll_attempts': 5, 'reel_status_poll_interval_seconds': 60}}`

- [ ] **Step 4: Run the full existing suite to confirm nothing broke**

Run: `python -m pytest tests/ -q`
Expected: PASS, same count as before plus the 17 new tests from Tasks 1–2 (270 + 11 + 6 = 287)

- [ ] **Step 5: Commit**

```bash
git add tests/_fake_http.py radar/config/publishing.yaml
git commit -m "Add fake HTTP test double and pin LinkedIn/Instagram API versions in config"
```

---

### Task 4: LinkedIn publisher — text posts and error handling

**Files:**
- Create: `radar/publishing/linkedin.py`
- Test: `tests/test_linkedin_publisher.py`

**Interfaces:**
- Consumes: `radar.publishing.platform_http.classify_response`, `radar.publishing.credentials.LinkedInCredential`, `radar.publishing.dispatch.PublishPayload`/`PublishResult`, `radar.settings.publishing_rules()`, `tests._fake_http.FakeResponse`/`FakeSession`.
- Produces: `LinkedInPublisher(credential, session=None, api_version=None)` with `.name = "linkedin_api"`, `.platform = "linkedin"`, `.publish(payload) -> PublishResult`. This task implements only `post_format == "linkedin_text"` plus the shared `_headers`, `_author_urn`, `_post`, `_error_body` helpers every later LinkedIn task reuses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_linkedin_publisher.py
from radar.publishing.credentials import LinkedInCredential
from radar.publishing.dispatch import PublishPayload
from radar.publishing.linkedin import LinkedInPublisher
from tests._fake_http import FakeResponse, FakeSession

CRED = LinkedInCredential(access_token="tok", person_urn="urn:li:person:1", org_urn="urn:li:organization:2")


def _payload(**overrides):
    base = dict(item_id=1, idempotency_key="k1", platform="linkedin", post_format="linkedin_text",
               target_account="founder_profile", text="Sample text Post", first_comment=None, media=[])
    base.update(overrides)
    return PublishPayload(**base)


def test_text_post_success_returns_post_id_from_header():
    session = FakeSession([FakeResponse(201, headers={"x-restli-id": "urn:li:share:6844785523593134080"})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_payload())
    assert result.outcome == "published"
    assert result.external_post_id == "urn:li:share:6844785523593134080"
    assert result.external_url.endswith("urn:li:share:6844785523593134080/")
    method, url, kwargs = session.calls[0]
    assert url == "https://api.linkedin.com/rest/posts"
    assert kwargs["headers"]["Linkedin-Version"] == "202608"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["json"]["author"] == "urn:li:person:1"
    assert kwargs["json"]["commentary"] == "Sample text Post"


def test_company_page_uses_org_urn():
    session = FakeSession([FakeResponse(201, headers={"x-restli-id": "urn:li:share:1"})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    pub.publish(_payload(target_account="company_page"))
    _, _, kwargs = session.calls[0]
    assert kwargs["json"]["author"] == "urn:li:organization:2"


def test_missing_urn_for_target_account_is_permanent_error_without_any_http_call():
    session = FakeSession([])
    cred = LinkedInCredential(access_token="tok", person_urn="urn:li:person:1", org_urn=None)
    pub = LinkedInPublisher(cred, session=session, api_version="202608")
    result = pub.publish(_payload(target_account="company_page"))
    assert result.outcome == "permanent_error"
    assert result.error_code == "missing_urn"
    assert session.calls == []


def test_201_without_post_id_header_is_unknown():
    session = FakeSession([FakeResponse(201, headers={})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_payload())
    assert result.outcome == "unknown"
    assert result.error_code == "no_post_id"


def test_429_maps_to_retryable_with_retry_after():
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "20"}, json_data={"message": "slow down"})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_payload())
    assert result.outcome == "retryable_error"
    assert result.retry_after_seconds == 20


def test_401_maps_to_permanent_error():
    session = FakeSession([FakeResponse(401, json_data={"message": "EMPTY_ACCESS_TOKEN"})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_payload())
    assert result.outcome == "permanent_error"


def test_connection_error_is_unknown_never_assumed_safe():
    class BoomSession:
        def post(self, *a, **k):
            import requests
            raise requests.ConnectionError("dns failure")

    pub = LinkedInPublisher(CRED, session=BoomSession(), api_version="202608")
    result = pub.publish(_payload())
    assert result.outcome == "unknown"
    assert result.error_code == "transport_error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'radar.publishing.linkedin'`

- [ ] **Step 3: Write the implementation**

```python
# radar/publishing/linkedin.py
"""LinkedInPublisher — the Posts/Images/Videos/Documents/MultiImage API
seam Phase 4 supplies. Contract verified against learn.microsoft.com's
LinkedIn API docs (li-lms-2026-08 moniker) on 2026-09-15 — re-check before
this system ever goes live, LinkedIn versions monthly. `auto_publish` stays
hard-refused; nothing here changes that."""
from __future__ import annotations

import json

import requests

from radar import settings
from radar.publishing import platform_http
from radar.publishing.credentials import LinkedInCredential
from radar.publishing.dispatch import PublishPayload, PublishResult

API_BASE = "https://api.linkedin.com/rest"


def _error_body(resp) -> str:
    try:
        return json.dumps(resp.json())
    except ValueError:
        return resp.text[:500]


class LinkedInPublisher:
    name = "linkedin_api"
    platform = "linkedin"

    def __init__(self, credential: LinkedInCredential, session=None, api_version: str | None = None):
        self.credential = credential
        self.session = session or requests.Session()
        self.api_version = api_version or settings.publishing_rules()["platform_apis"]["linkedin"]["api_version"]

    def _headers(self, content_type: str | None = "application/json") -> dict:
        headers = {
            "Authorization": f"Bearer {self.credential.access_token}",
            "Linkedin-Version": self.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _author_urn(self, payload: PublishPayload) -> str | None:
        if payload.target_account == "founder_profile":
            return self.credential.person_urn
        if payload.target_account == "company_page":
            return self.credential.org_urn
        return None

    def publish(self, payload: PublishPayload) -> PublishResult:
        author = self._author_urn(payload)
        if not author:
            return PublishResult(
                "permanent_error", error_code="missing_urn",
                error_message=f"no LinkedIn URN configured for target_account={payload.target_account!r}",
            )
        try:
            if payload.post_format == "linkedin_text":
                return self._post(payload, author, content=None)
            return PublishResult(
                "permanent_error", error_code="unsupported_format",
                error_message=f"linkedin publisher has no handler for {payload.post_format!r}",
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            return PublishResult("unknown", error_code="transport_error", error_message=repr(exc))

    def _post(self, payload: PublishPayload, author: str, content: dict | None) -> PublishResult:
        body = {
            "author": author,
            "commentary": payload.text,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if content is not None:
            body["content"] = content
        resp = self.session.post(f"{API_BASE}/posts", headers=self._headers(), json=body, timeout=30)
        outcome, retry_after = platform_http.classify_response(resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        post_id = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
        if not post_id:
            return PublishResult("unknown", error_code="no_post_id",
                                 error_message="201 response carried no x-restli-id header")
        return PublishResult("published", external_post_id=post_id,
                             external_url=f"https://www.linkedin.com/feed/update/{post_id}/")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/linkedin.py tests/test_linkedin_publisher.py
git commit -m "Add LinkedInPublisher: text posts, auth/error handling"
```

---

### Task 5: LinkedIn publisher — image posts

**Files:**
- Modify: `radar/publishing/linkedin.py`
- Modify: `tests/test_linkedin_publisher.py`

**Interfaces:**
- Consumes: everything from Task 4.
- Produces: `LinkedInPublisher.publish()` now also handles `post_format == "linkedin_image"`; adds private `_upload_image(media_row, owner) -> str | PublishResult` (returns the image URN on success, or a `PublishResult` to return directly on failure) that later tasks (multi-image) reuse.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_linkedin_publisher.py`:

```python
def _image_payload(tmp_path, **overrides):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"fake jpeg bytes")
    media = [{"media_kind": "image", "position": 0, "local_path": str(p), "public_url": None,
             "mime_type": "image/jpeg", "byte_size": 15, "sha256": "x", "width": 1080, "height": 1080,
             "duration_seconds": None, "page_count": None, "alt_text": "a photo", "title": None}]
    base = dict(item_id=2, idempotency_key="k2", platform="linkedin", post_format="linkedin_image",
               target_account="founder_profile", text="Image post", first_comment=None, media=media)
    base.update(overrides)
    return PublishPayload(**base)


def test_image_post_uploads_then_creates_post(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/img", "image": "urn:li:image:abc"}}),
        FakeResponse(201),
        FakeResponse(201, headers={"x-restli-id": "urn:li:share:9"}),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_image_payload(tmp_path))
    assert result.outcome == "published"
    assert result.external_post_id == "urn:li:share:9"
    init_call = session.calls[0]
    assert init_call[1] == "https://api.linkedin.com/rest/images?action=initializeUpload"
    put_call = session.calls[1]
    assert put_call[0] == "PUT"
    assert put_call[1] == "https://upload/img"
    assert put_call[2]["headers"]["Authorization"] == "Bearer tok"
    post_call = session.calls[2]
    assert post_call[2]["json"]["content"]["media"]["id"] == "urn:li:image:abc"
    assert post_call[2]["json"]["content"]["media"]["altText"] == "a photo"


def test_image_upload_initialize_failure_stops_before_posting(tmp_path):
    session = FakeSession([FakeResponse(403, json_data={"message": "forbidden"})])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_image_payload(tmp_path))
    assert result.outcome == "permanent_error"
    assert len(session.calls) == 1  # never reached the post-creation call


def test_image_byte_upload_failure_stops_before_posting(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/img", "image": "urn:li:image:abc"}}),
        FakeResponse(500),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_image_payload(tmp_path))
    assert result.outcome == "retryable_error"
    assert len(session.calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: FAIL — `test_image_post_*` fail with `permanent_error`/`unsupported_format` since `linkedin_image` isn't handled yet.

- [ ] **Step 3: Extend the implementation**

In `radar/publishing/linkedin.py`, add `_upload_image` and extend `publish()`:

```python
    def _upload_image(self, media_row: dict, owner: str) -> str | PublishResult:
        resp = self.session.post(
            f"{API_BASE}/images?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner}}, timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        body = resp.json()["value"]
        upload_url, urn = body["uploadUrl"], body["image"]
        with open(media_row["local_path"], "rb") as fh:
            put_resp = self.session.put(upload_url, headers={"Authorization": f"Bearer {self.credential.access_token}"},
                                        data=fh, timeout=120)
        outcome, retry_after = platform_http.classify_response(put_resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"upload_http_{put_resp.status_code}",
                                 error_message=_error_body(put_resp), retry_after_seconds=retry_after)
        return urn
```

Replace the `if payload.post_format == "linkedin_text":` branch's follow-up in `publish()` with:

```python
            if payload.post_format == "linkedin_text":
                return self._post(payload, author, content=None)
            if payload.post_format == "linkedin_image":
                media = payload.media[0]
                upload = self._upload_image(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author,
                                  content={"media": {"id": upload, "altText": media.get("alt_text") or ""}})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/linkedin.py tests/test_linkedin_publisher.py
git commit -m "Add LinkedIn image post support (Images API upload flow)"
```

---

### Task 6: LinkedIn publisher — MultiImage posts

**Files:**
- Modify: `radar/publishing/linkedin.py`
- Modify: `tests/test_linkedin_publisher.py`

**Interfaces:**
- Consumes: `_upload_image` from Task 5.
- Produces: `publish()` handles `post_format == "linkedin_multi_image"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_linkedin_publisher.py`:

```python
def _multi_image_payload(tmp_path):
    media = []
    for i in range(2):
        p = tmp_path / f"photo{i}.jpg"
        p.write_bytes(b"fake jpeg bytes")
        media.append({"media_kind": "image", "position": i, "local_path": str(p), "public_url": None,
                     "mime_type": "image/jpeg", "byte_size": 15, "sha256": "x", "width": 1080, "height": 1080,
                     "duration_seconds": None, "page_count": None, "alt_text": f"photo {i}", "title": None})
    return PublishPayload(item_id=3, idempotency_key="k3", platform="linkedin", post_format="linkedin_multi_image",
                          target_account="founder_profile", text="Multi image post", first_comment=None, media=media)


def test_multi_image_post_uploads_each_image_then_creates_post(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/1", "image": "urn:li:image:1"}}),
        FakeResponse(201),
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/2", "image": "urn:li:image:2"}}),
        FakeResponse(201),
        FakeResponse(201, headers={"x-restli-id": "urn:li:share:mi1"}),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_multi_image_payload(tmp_path))
    assert result.outcome == "published"
    post_call = session.calls[-1]
    images = post_call[2]["json"]["content"]["multiImage"]["images"]
    assert images == [
        {"id": "urn:li:image:1", "altText": "photo 0"},
        {"id": "urn:li:image:2", "altText": "photo 1"},
    ]


def test_multi_image_stops_on_first_image_failure(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/1", "image": "urn:li:image:1"}}),
        FakeResponse(401, json_data={"message": "expired"}),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_multi_image_payload(tmp_path))
    assert result.outcome == "permanent_error"
    assert len(session.calls) == 2  # never uploaded the second image or posted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: FAIL with `unsupported_format` permanent_error for `linkedin_multi_image`.

- [ ] **Step 3: Extend the implementation**

Add another branch in `publish()`, right after the `linkedin_image` branch:

```python
            if payload.post_format == "linkedin_multi_image":
                images = []
                for media in payload.media:
                    upload = self._upload_image(media, author)
                    if isinstance(upload, PublishResult):
                        return upload
                    images.append({"id": upload, "altText": media.get("alt_text") or ""})
                return self._post(payload, author, content={"multiImage": {"images": images}})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/linkedin.py tests/test_linkedin_publisher.py
git commit -m "Add LinkedIn organic MultiImage post support (distinct from sponsored Carousel)"
```

---

### Task 7: LinkedIn publisher — document posts

**Files:**
- Modify: `radar/publishing/linkedin.py`
- Modify: `tests/test_linkedin_publisher.py`

**Interfaces:**
- Consumes: shared helpers from Task 4.
- Produces: `publish()` handles `post_format == "linkedin_document"`; adds `_upload_document(media_row, owner) -> str | PublishResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_linkedin_publisher.py`:

```python
def _document_payload(tmp_path, title="Q3 Export Brief.pdf"):
    p = tmp_path / "brief.pdf"
    p.write_bytes(b"%PDF-fake")
    media = [{"media_kind": "document", "position": 0, "local_path": str(p), "public_url": None,
             "mime_type": "application/pdf", "byte_size": 9, "sha256": "x", "width": None, "height": None,
             "duration_seconds": None, "page_count": 3, "alt_text": None, "title": title}]
    return PublishPayload(item_id=4, idempotency_key="k4", platform="linkedin", post_format="linkedin_document",
                          target_account="founder_profile", text="Document post", first_comment=None, media=media)


def test_document_post_uploads_then_creates_post_with_title(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {"uploadUrl": "https://upload/doc", "document": "urn:li:document:d1"}}),
        FakeResponse(201),
        FakeResponse(201, headers={"x-restli-id": "urn:li:share:doc1"}),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_document_payload(tmp_path))
    assert result.outcome == "published"
    post_call = session.calls[-1]
    assert post_call[2]["json"]["content"]["media"] == {"id": "urn:li:document:d1", "title": "Q3 Export Brief.pdf"}
    init_call = session.calls[0]
    assert init_call[1] == "https://api.linkedin.com/rest/documents?action=initializeUpload"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: FAIL with `unsupported_format`.

- [ ] **Step 3: Extend the implementation**

```python
    def _upload_document(self, media_row: dict, owner: str) -> str | PublishResult:
        resp = self.session.post(
            f"{API_BASE}/documents?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner}}, timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        body = resp.json()["value"]
        upload_url, urn = body["uploadUrl"], body["document"]
        with open(media_row["local_path"], "rb") as fh:
            put_resp = self.session.put(upload_url, headers={"Authorization": f"Bearer {self.credential.access_token}"},
                                        data=fh, timeout=120)
        outcome, retry_after = platform_http.classify_response(put_resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"upload_http_{put_resp.status_code}",
                                 error_message=_error_body(put_resp), retry_after_seconds=retry_after)
        return urn
```

Add the branch:

```python
            if payload.post_format == "linkedin_document":
                media = payload.media[0]
                upload = self._upload_document(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author,
                                  content={"media": {"id": upload, "title": media.get("title") or "Document"}})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/linkedin.py tests/test_linkedin_publisher.py
git commit -m "Add LinkedIn document post support (Documents API upload flow)"
```

---

### Task 8: LinkedIn publisher — video posts (chunked upload)

**Files:**
- Modify: `radar/publishing/linkedin.py`
- Modify: `tests/test_linkedin_publisher.py`

**Interfaces:**
- Consumes: shared helpers from Task 4.
- Produces: `publish()` handles `post_format == "linkedin_video"`; adds `_upload_video(media_row, owner) -> str | PublishResult`. This is the LinkedIn implementation's most complex path — the Videos API always returns multi-part upload instructions regardless of file size, and each part's response `ETag` header becomes the `uploadedPartIds` entry `finalizeUpload` needs, in the same order as the parts.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_linkedin_publisher.py`:

```python
def _video_payload(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"0123456789")  # 10 bytes, split into two 5-byte parts below
    media = [{"media_kind": "video", "position": 0, "local_path": str(p), "public_url": None,
             "mime_type": "video/mp4", "byte_size": 10, "sha256": "x", "width": None, "height": None,
             "duration_seconds": 5, "page_count": None, "alt_text": None, "title": "Clip"}]
    return PublishPayload(item_id=5, idempotency_key="k5", platform="linkedin", post_format="linkedin_video",
                          target_account="founder_profile", text="Video post", first_comment=None, media=media)


def test_video_post_uploads_each_part_finalizes_then_posts(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {
            "video": "urn:li:video:v1", "uploadToken": "tok-abc",
            "uploadInstructions": [
                {"uploadUrl": "https://upload/part1", "firstByte": 0, "lastByte": 4},
                {"uploadUrl": "https://upload/part2", "firstByte": 5, "lastByte": 9},
            ],
        }}),
        FakeResponse(200, headers={"ETag": "etag-1"}),
        FakeResponse(200, headers={"ETag": "etag-2"}),
        FakeResponse(200),  # finalizeUpload
        FakeResponse(201, headers={"x-restli-id": "urn:li:share:vid1"}),
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_video_payload(tmp_path))
    assert result.outcome == "published"
    part1_call = session.calls[1]
    assert part1_call[0] == "PUT"
    assert part1_call[2]["data"] == b"01234"
    assert "Authorization" not in part1_call[2].get("headers", {})  # video PUT is unauthenticated per LinkedIn docs
    part2_call = session.calls[2]
    assert part2_call[2]["data"] == b"56789"
    finalize_call = session.calls[3]
    assert finalize_call[1] == "https://api.linkedin.com/rest/videos?action=finalizeUpload"
    assert finalize_call[2]["json"]["finalizeUploadRequest"]["uploadedPartIds"] == ["etag-1", "etag-2"]
    assert finalize_call[2]["json"]["finalizeUploadRequest"]["uploadToken"] == "tok-abc"
    post_call = session.calls[4]
    assert post_call[2]["json"]["content"]["media"]["id"] == "urn:li:video:v1"


def test_video_part_missing_etag_is_unknown_not_guessed(tmp_path):
    session = FakeSession([
        FakeResponse(200, json_data={"value": {
            "video": "urn:li:video:v1", "uploadToken": "",
            "uploadInstructions": [{"uploadUrl": "https://upload/part1", "firstByte": 0, "lastByte": 9}],
        }}),
        FakeResponse(200, headers={}),  # no ETag header
    ])
    pub = LinkedInPublisher(CRED, session=session, api_version="202608")
    result = pub.publish(_video_payload(tmp_path))
    assert result.outcome == "unknown"
    assert result.error_code == "missing_etag"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: FAIL with `unsupported_format`.

- [ ] **Step 3: Extend the implementation**

```python
    def _upload_video(self, media_row: dict, owner: str) -> str | PublishResult:
        from pathlib import Path
        file_size = media_row.get("byte_size") or Path(media_row["local_path"]).stat().st_size
        resp = self.session.post(
            f"{API_BASE}/videos?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner, "fileSizeBytes": file_size,
                                              "uploadCaptions": False, "uploadThumbnail": False}},
            timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        body = resp.json()["value"]
        video_urn = body["video"]
        upload_token = body.get("uploadToken", "")
        part_ids = []
        with open(media_row["local_path"], "rb") as fh:
            for instr in body["uploadInstructions"]:
                fh.seek(instr["firstByte"])
                chunk = fh.read(instr["lastByte"] - instr["firstByte"] + 1)
                put_resp = self.session.put(instr["uploadUrl"], headers={"Content-Type": "application/octet-stream"},
                                            data=chunk, timeout=120)
                outcome, retry_after = platform_http.classify_response(put_resp.status_code)
                if outcome != "ok":
                    return PublishResult(outcome, error_code=f"upload_http_{put_resp.status_code}",
                                         error_message=_error_body(put_resp), retry_after_seconds=retry_after)
                etag = put_resp.headers.get("ETag") or put_resp.headers.get("etag")
                if not etag:
                    return PublishResult("unknown", error_code="missing_etag",
                                         error_message="video part upload returned no ETag header")
                part_ids.append(etag)
        fin_resp = self.session.post(
            f"{API_BASE}/videos?action=finalizeUpload", headers=self._headers(),
            json={"finalizeUploadRequest": {"video": video_urn, "uploadToken": upload_token, "uploadedPartIds": part_ids}},
            timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(fin_resp.status_code)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"finalize_http_{fin_resp.status_code}",
                                 error_message=_error_body(fin_resp), retry_after_seconds=retry_after)
        return video_urn
```

Add the branch:

```python
            if payload.post_format == "linkedin_video":
                media = payload.media[0]
                upload = self._upload_video(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author, content={"media": {"id": upload, "title": media.get("title") or ""}})
```

Move the `from pathlib import Path` import to the top of the file alongside the other imports instead of inline (inline was shown above only to keep the diff-in-context minimal — the actual edit puts `from pathlib import Path` in the module's import block).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linkedin_publisher.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/linkedin.py tests/test_linkedin_publisher.py
git commit -m "Add LinkedIn video post support (Videos API chunked upload)"
```

---

### Task 9: Instagram publisher — image posts

**Files:**
- Create: `radar/publishing/instagram.py`
- Test: `tests/test_instagram_publisher.py`

**Interfaces:**
- Consumes: `radar.publishing.platform_http.classify_response`, `radar.publishing.credentials.InstagramCredential`, `radar.publishing.dispatch.PublishPayload`/`PublishResult`, `radar.settings.publishing_rules()`.
- Produces: `InstagramPublisher(credential, session=None, api_version=None)` with `.name = "instagram_api"`, `.platform = "instagram"`, `.publish(payload) -> PublishResult`. This task implements `post_format == "instagram_image"` plus the shared `_base`, `_outcome` (body-aware Graph API error classification), `_error_message`, `_create_container`, `_publish_container` helpers every later Instagram task reuses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instagram_publisher.py
from radar.publishing.credentials import InstagramCredential
from radar.publishing.dispatch import PublishPayload
from radar.publishing.instagram import InstagramPublisher
from tests._fake_http import FakeResponse, FakeSession

CRED = InstagramCredential(access_token="tok", business_account_id="17800000")


def _image_payload(**overrides):
    media = [{"media_kind": "image", "position": 0, "local_path": None,
             "public_url": "https://cdn.example.com/photo.jpg", "mime_type": "image/jpeg", "byte_size": 100,
             "sha256": "x", "width": 1080, "height": 1080, "duration_seconds": None, "page_count": None,
             "alt_text": None, "title": None}]
    base = dict(item_id=10, idempotency_key="ig1", platform="instagram", post_format="instagram_image",
               target_account="business_account", text="Caption text", first_comment=None, media=media)
    base.update(overrides)
    return PublishPayload(**base)


def test_image_post_creates_container_then_publishes():
    session = FakeSession([
        FakeResponse(200, json_data={"id": "17900000000000001"}),
        FakeResponse(200, json_data={"id": "17900000000000099"}),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_image_payload())
    assert result.outcome == "published"
    assert result.external_post_id == "17900000000000099"
    create_call = session.calls[0]
    assert create_call[1] == "https://graph.facebook.com/v25.0/17800000/media"
    assert create_call[2]["data"]["image_url"] == "https://cdn.example.com/photo.jpg"
    assert create_call[2]["data"]["access_token"] == "tok"
    publish_call = session.calls[1]
    assert publish_call[1] == "https://graph.facebook.com/v25.0/17800000/media_publish"
    assert publish_call[2]["data"]["creation_id"] == "17900000000000001"


def test_no_container_id_in_response_is_unknown():
    session = FakeSession([FakeResponse(200, json_data={})])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_image_payload())
    assert result.outcome == "unknown"
    assert result.error_code == "no_container_id"


def test_oauth_exception_body_is_permanent_error_even_on_http_400():
    session = FakeSession([FakeResponse(400, json_data={
        "error": {"message": "Error validating access token", "type": "OAuthException", "code": 190},
    })])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_image_payload())
    assert result.outcome == "permanent_error"


def test_rate_limit_error_code_is_retryable():
    session = FakeSession([FakeResponse(400, json_data={
        "error": {"message": "reduce the amount of calls", "type": "OAuthException", "code": 4},
    })])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_image_payload())
    assert result.outcome == "retryable_error"


def test_connection_error_is_unknown():
    class BoomSession:
        def post(self, *a, **k):
            import requests
            raise requests.ConnectionError("dns failure")

    pub = InstagramPublisher(CRED, session=BoomSession(), api_version="v25.0")
    result = pub.publish(_image_payload())
    assert result.outcome == "unknown"
    assert result.error_code == "transport_error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'radar.publishing.instagram'`

- [ ] **Step 3: Write the implementation**

```python
# radar/publishing/instagram.py
"""InstagramPublisher — the Meta Graph API content-publishing seam Phase 4
supplies. Contract verified against developers.facebook.com/docs/
instagram-platform/content-publishing/ on 2026-09-15 (v25.0 was current) —
re-check before this system ever goes live, Meta ships a new major roughly
quarterly. `auto_publish` stays hard-refused; nothing here changes that."""
from __future__ import annotations

import json
import time

import requests

from radar import settings
from radar.publishing import platform_http
from radar.publishing.credentials import InstagramCredential
from radar.publishing.dispatch import PublishPayload, PublishResult

GRAPH_API_BASE = "https://graph.facebook.com"
_RETRYABLE_GRAPH_ERROR_CODES = (4, 17, 32, 613)


def _error_message(resp) -> str:
    try:
        return json.dumps(resp.json())
    except ValueError:
        return resp.text[:500]


class InstagramPublisher:
    name = "instagram_api"
    platform = "instagram"

    def __init__(self, credential: InstagramCredential, session=None, api_version: str | None = None):
        self.credential = credential
        self.session = session or requests.Session()
        cfg = settings.publishing_rules()["platform_apis"]["instagram"]
        self.api_version = api_version or cfg["api_version"]
        self.poll_attempts = cfg["reel_status_poll_attempts"]
        self.poll_interval_seconds = cfg["reel_status_poll_interval_seconds"]

    def _base(self) -> str:
        return f"{GRAPH_API_BASE}/{self.api_version}/{self.credential.business_account_id}"

    def _outcome(self, resp) -> tuple[str, int | None]:
        """Graph API embeds the real failure signal in the JSON body even on
        HTTP 400 — checked before falling back to the generic HTTP-status
        classification."""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            if err.get("type") == "OAuthException" and err.get("code") != 4:
                return "permanent_error", None
            if err.get("code") in _RETRYABLE_GRAPH_ERROR_CODES:
                return "retryable_error", None
            return "permanent_error", None
        return platform_http.classify_response(resp.status_code)

    def publish(self, payload: PublishPayload) -> PublishResult:
        try:
            if payload.post_format == "instagram_image":
                return self._publish_image(payload)
            return PublishResult(
                "permanent_error", error_code="unsupported_format",
                error_message=f"instagram publisher has no handler for {payload.post_format!r}",
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            return PublishResult("unknown", error_code="transport_error", error_message=repr(exc))

    def _create_container(self, params: dict) -> str | PublishResult:
        params = {**params, "access_token": self.credential.access_token}
        resp = self.session.post(f"{self._base()}/media", data=params, timeout=30)
        outcome, retry_after = self._outcome(resp)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_message(resp), retry_after_seconds=retry_after)
        try:
            return resp.json()["id"]
        except (ValueError, KeyError):
            return PublishResult("unknown", error_code="no_container_id",
                                 error_message="media container response carried no id")

    def _publish_container(self, creation_id: str) -> PublishResult:
        resp = self.session.post(f"{self._base()}/media_publish",
                                 data={"creation_id": creation_id, "access_token": self.credential.access_token},
                                 timeout=30)
        outcome, retry_after = self._outcome(resp)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_message(resp), retry_after_seconds=retry_after)
        try:
            media_id = resp.json()["id"]
        except (ValueError, KeyError):
            return PublishResult("unknown", error_code="no_media_id",
                                 error_message="media_publish response carried no id")
        return PublishResult("published", external_post_id=media_id,
                             external_url=f"https://www.instagram.com/p/{media_id}/")

    def _publish_image(self, payload: PublishPayload) -> PublishResult:
        image = payload.media[0]
        creation = self._create_container({"image_url": image["public_url"], "caption": payload.text})
        if isinstance(creation, PublishResult):
            return creation
        return self._publish_container(creation)
```

**Note on `_outcome`'s OAuthException/rate-limit split:** Graph API documents `code: 4` ("Application request limit reached") under the general rate-limiting umbrella but it can appear alongside `type: "OAuthException"`; this implementation treats `code == 4` as retryable even when `type == "OAuthException"`, and every other `OAuthException` (crucially `code: 190`, invalid/expired token) as permanent, matching the founder's condition that an expired/invalid token is never silently retried.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/instagram.py tests/test_instagram_publisher.py
git commit -m "Add InstagramPublisher: image posts, Graph API body-aware error classification"
```

---

### Task 10: Instagram publisher — carousel posts

**Files:**
- Modify: `radar/publishing/instagram.py`
- Modify: `tests/test_instagram_publisher.py`

**Interfaces:**
- Consumes: `_create_container`/`_publish_container` from Task 9.
- Produces: `publish()` handles `post_format == "instagram_carousel"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_instagram_publisher.py`:

```python
def _carousel_payload():
    media = [
        {"media_kind": "image", "position": i, "local_path": None,
         "public_url": f"https://cdn.example.com/photo{i}.jpg", "mime_type": "image/jpeg", "byte_size": 100,
         "sha256": "x", "width": 1080, "height": 1080, "duration_seconds": None, "page_count": None,
         "alt_text": None, "title": None}
        for i in range(3)
    ]
    return PublishPayload(item_id=11, idempotency_key="ig2", platform="instagram", post_format="instagram_carousel",
                          target_account="business_account", text="Carousel caption", first_comment=None, media=media)


def test_carousel_creates_children_then_parent_then_publishes():
    session = FakeSession([
        FakeResponse(200, json_data={"id": "child1"}),
        FakeResponse(200, json_data={"id": "child2"}),
        FakeResponse(200, json_data={"id": "child3"}),
        FakeResponse(200, json_data={"id": "parent1"}),
        FakeResponse(200, json_data={"id": "published1"}),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_carousel_payload())
    assert result.outcome == "published"
    assert result.external_post_id == "published1"
    child_calls = session.calls[:3]
    for i, call in enumerate(child_calls):
        assert call[2]["data"]["image_url"] == f"https://cdn.example.com/photo{i}.jpg"
        assert call[2]["data"]["is_carousel_item"] == "true"
    parent_call = session.calls[3]
    assert parent_call[2]["data"]["media_type"] == "CAROUSEL"
    assert parent_call[2]["data"]["children"] == "child1,child2,child3"


def test_carousel_stops_on_first_child_failure():
    session = FakeSession([
        FakeResponse(200, json_data={"id": "child1"}),
        FakeResponse(500),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_carousel_payload())
    assert result.outcome == "retryable_error"
    assert len(session.calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: FAIL with `unsupported_format`.

- [ ] **Step 3: Extend the implementation**

Add to `publish()`, after the `instagram_image` branch:

```python
            if payload.post_format == "instagram_carousel":
                return self._publish_carousel(payload)
```

Add the method:

```python
    def _publish_carousel(self, payload: PublishPayload) -> PublishResult:
        children = []
        for media in payload.media:
            child = self._create_container({"image_url": media["public_url"], "is_carousel_item": "true"})
            if isinstance(child, PublishResult):
                return child
            children.append(child)
        parent = self._create_container({"media_type": "CAROUSEL", "children": ",".join(children),
                                         "caption": payload.text})
        if isinstance(parent, PublishResult):
            return parent
        return self._publish_container(parent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/instagram.py tests/test_instagram_publisher.py
git commit -m "Add Instagram carousel post support"
```

---

### Task 11: Instagram publisher — Reel posts (status polling)

**Files:**
- Modify: `radar/publishing/instagram.py`
- Modify: `tests/test_instagram_publisher.py`

**Interfaces:**
- Consumes: `_create_container`/`_publish_container`/`self.poll_attempts`/`self.poll_interval_seconds` from Task 9.
- Produces: `publish()` handles `post_format == "instagram_reel"`. Polling that's still `IN_PROGRESS` when the attempt bound is hit returns `outcome="unknown"` — never guessed, matching the CLAUDE.md "unknown is a valid answer" guardrail — and a human/reconciliation step checks it later.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_instagram_publisher.py`:

```python
def _reel_payload():
    media = [
        {"media_kind": "video", "position": 0, "local_path": None, "public_url": "https://cdn.example.com/reel.mp4",
         "mime_type": "video/mp4", "byte_size": 1000, "sha256": "x", "width": None, "height": None,
         "duration_seconds": 20, "page_count": None, "alt_text": None, "title": None},
        {"media_kind": "cover_image", "position": 0, "local_path": None, "public_url": "https://cdn.example.com/cover.jpg",
         "mime_type": "image/jpeg", "byte_size": 50, "sha256": "y", "width": 1080, "height": 1920,
         "duration_seconds": None, "page_count": None, "alt_text": None, "title": None},
    ]
    return PublishPayload(item_id=12, idempotency_key="ig3", platform="instagram", post_format="instagram_reel",
                          target_account="business_account", text="Reel caption", first_comment=None, media=media)


def test_reel_polls_until_finished_then_publishes(monkeypatch):
    import radar.publishing.instagram as ig_module
    monkeypatch.setattr(ig_module.time, "sleep", lambda s: None)
    session = FakeSession([
        FakeResponse(200, json_data={"id": "reel_container_1"}),
        FakeResponse(200, json_data={"status_code": "IN_PROGRESS"}),
        FakeResponse(200, json_data={"status_code": "FINISHED"}),
        FakeResponse(200, json_data={"id": "reel_published_1"}),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_reel_payload())
    assert result.outcome == "published"
    assert result.external_post_id == "reel_published_1"
    create_call = session.calls[0]
    assert create_call[2]["data"]["media_type"] == "REELS"
    assert create_call[2]["data"]["video_url"] == "https://cdn.example.com/reel.mp4"
    assert create_call[2]["data"]["cover_url"] == "https://cdn.example.com/cover.jpg"


def test_reel_still_processing_at_poll_bound_is_unknown(monkeypatch):
    import radar.publishing.instagram as ig_module
    monkeypatch.setattr(ig_module.time, "sleep", lambda s: None)
    responses = [FakeResponse(200, json_data={"id": "reel_container_2"})]
    responses += [FakeResponse(200, json_data={"status_code": "IN_PROGRESS"})] * 5
    session = FakeSession(responses)
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_reel_payload())
    assert result.outcome == "unknown"
    assert result.error_code == "reel_still_processing"
    assert len(session.calls) == 6  # create + 5 polls, never published


def test_reel_error_status_is_permanent():
    session = FakeSession([
        FakeResponse(200, json_data={"id": "reel_container_3"}),
        FakeResponse(200, json_data={"status_code": "ERROR"}),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_reel_payload())
    assert result.outcome == "permanent_error"
    assert result.error_code == "reel_processing_error"


def test_reel_expired_status_is_permanent():
    session = FakeSession([
        FakeResponse(200, json_data={"id": "reel_container_4"}),
        FakeResponse(200, json_data={"status_code": "EXPIRED"}),
    ])
    pub = InstagramPublisher(CRED, session=session, api_version="v25.0")
    result = pub.publish(_reel_payload())
    assert result.outcome == "permanent_error"
    assert result.error_code == "reel_expired"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: FAIL with `unsupported_format`.

- [ ] **Step 3: Extend the implementation**

Add to `publish()`:

```python
            if payload.post_format == "instagram_reel":
                return self._publish_reel(payload)
```

Add the methods:

```python
    def _publish_reel(self, payload: PublishPayload) -> PublishResult:
        video = next(m for m in payload.media if m["media_kind"] == "video")
        cover = next((m for m in payload.media if m["media_kind"] == "cover_image"), None)
        params = {"media_type": "REELS", "video_url": video["public_url"], "caption": payload.text}
        if cover:
            params["cover_url"] = cover["public_url"]
        creation = self._create_container(params)
        if isinstance(creation, PublishResult):
            return creation
        status = self._await_ready(creation)
        if status != "FINISHED":
            return status  # a PublishResult built by _await_ready for ERROR/EXPIRED/still-processing
        return self._publish_container(creation)

    def _await_ready(self, container_id: str):
        for _ in range(self.poll_attempts):
            resp = self.session.get(f"{GRAPH_API_BASE}/{self.api_version}/{container_id}",
                                    params={"fields": "status_code", "access_token": self.credential.access_token},
                                    timeout=30)
            outcome, retry_after = self._outcome(resp)
            if outcome != "ok":
                return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                     error_message=_error_message(resp), retry_after_seconds=retry_after)
            status_code = resp.json().get("status_code")
            if status_code == "FINISHED":
                return "FINISHED"
            if status_code == "ERROR":
                return PublishResult("permanent_error", error_code="reel_processing_error",
                                     error_message="Instagram reported status_code=ERROR while processing the reel")
            if status_code == "EXPIRED":
                return PublishResult("permanent_error", error_code="reel_expired",
                                     error_message="reel container expired (not published within 24h)")
            time.sleep(self.poll_interval_seconds)
        return PublishResult("unknown", error_code="reel_still_processing",
                             error_message=f"reel container still IN_PROGRESS after {self.poll_attempts} polls")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_instagram_publisher.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add radar/publishing/instagram.py tests/test_instagram_publisher.py
git commit -m "Add Instagram Reel post support with bounded status polling"
```

---

### Task 12: CLI wiring — cmd_queue_dispatch builds real publishers

**Files:**
- Modify: `radar/cli.py`
- Test: append to `tests/test_cli.py` (read it first to match its existing style — it uses a scratch DB per test, following the pattern of the rest of that file).

**Interfaces:**
- Consumes: `radar.publishing.credentials.linkedin_credential`/`instagram_credential`, `radar.publishing.linkedin.LinkedInPublisher`, `radar.publishing.instagram.InstagramPublisher`.
- Produces: `cmd_queue_dispatch` now passes a `publishers` dict built from whichever credentials are present in the environment; a platform with no env vars set is simply absent (matching `dispatch_due`'s existing "skipped: no publisher for X" behavior — no new skip logic needed).

- [ ] **Step 1: Write the failing test**

First read `tests/test_cli.py` to see its exact fixture/invocation pattern (it likely calls `cli.main([...])` or similar against a scratch DB — match that style exactly rather than guessing). Then append a test resembling:

```python
def test_queue_dispatch_live_still_refused_with_no_credentials_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "scratch.db"))
    # follow this file's existing pattern for invoking the CLI and asserting
    # on exit code / stdout — e.g. calling cli.main(["init"]) then
    # cli.main(["queue-dispatch", "--live"]) and asserting SystemExit(2) or
    # the equivalent this file already uses for the pre-Phase-4 refusal.
```

(This step is written as a template because `tests/test_cli.py`'s exact harness — argparse invocation helper, exit-code assertion style — must be read and matched, not guessed; copy the pattern from an existing `queue-dispatch` test in that file if one exists, or from the nearest analogous command test.)

- [ ] **Step 2: Run the test to verify it fails or passes for the wrong reason**

Run: `python -m pytest tests/test_cli.py -k queue_dispatch -v`
Confirm the test currently exercises the pre-Phase-4 code path (no `_publishers()` helper exists yet) and still gets refused only because `auto_publish` is closed — that refusal must be unaffected by this task.

- [ ] **Step 3: Add the CLI wiring**

In `radar/cli.py`, add imports near the existing `from radar.publishing import dispatch as publish_dispatch` line:

```python
from radar.publishing import credentials as publish_credentials
from radar.publishing.instagram import InstagramPublisher
from radar.publishing.linkedin import LinkedInPublisher
```

Add a helper near `_conn()`:

```python
def _publishers() -> dict:
    publishers = {}
    li_cred = publish_credentials.linkedin_credential()
    if li_cred:
        publishers["linkedin"] = LinkedInPublisher(li_cred)
    ig_cred = publish_credentials.instagram_credential()
    if ig_cred:
        publishers["instagram"] = InstagramPublisher(ig_cred)
    return publishers
```

Change `cmd_queue_dispatch`:

```python
def cmd_queue_dispatch(a):
    reports = publish_dispatch.dispatch_due(_conn(), now_iso(), dry_run=not a.live, publishers=_publishers())
    print(publish_dispatch.render_dispatch_report(reports, dry_run=not a.live))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS, including the new test — `--live` is still refused (by the `auto_publish` gate, which fires before `_publishers()`'s result is ever used) regardless of whether credentials are configured.

- [ ] **Step 5: Manual scratch-DB smoke test (per this repo's workflow expectations — live-test CLI changes, not just pytest)**

```bash
RADAR_DB_PATH=/tmp/radar-phase4-smoke.db python -m radar init
RADAR_DB_PATH=/tmp/radar-phase4-smoke.db python -m radar queue-dispatch
```

Expected: "Nothing is due." (empty queue) with no traceback — confirms `_publishers()` runs cleanly with no env vars set (both platforms absent, no error). Then:

```bash
LINKEDIN_ACCESS_TOKEN=fake-token LINKEDIN_PERSON_URN=urn:li:person:1 \
RADAR_DB_PATH=/tmp/radar-phase4-smoke.db python -m radar queue-dispatch
```

Expected: still "Nothing is due." — confirms `LinkedInPublisher` construction with a fake token doesn't error, and (per condition 8) no HTTP call is made since there's nothing queued/due and `--live` wasn't passed.

- [ ] **Step 6: Commit**

```bash
git add radar/cli.py tests/test_cli.py
git commit -m "Wire LinkedIn/Instagram publishers into queue-dispatch from env credentials"
```

---

### Task 13: Dispatch wiring tests — real publisher classes through dispatch_due

**Files:**
- Create: `tests/test_dispatch_real_publishers.py`

**Interfaces:**
- Consumes: `tests.test_publishing_queue._scheduled_item`, `BASE`, `iso` (import from that module — `tests/` is a package, confirmed by `tests/__init__.py`), `radar.publishing.dispatch.dispatch_due`, `LinkedInPublisher`, `LinkedInCredential`, `FakeResponse`/`FakeSession` from `tests._fake_http`.
- Produces: two tests proving the real `LinkedInPublisher`/`InstagramPublisher` classes work when passed into `dispatch_due(..., publishers={...})`, exactly like Phase 3's `_FakePublisher`-based tests already prove the seam's state-machine wiring — this closes the loop from "the class works in isolation" (Tasks 4–11) to "the class works through the actual dispatcher."

- [ ] **Step 1: Write the tests**

```python
# tests/test_dispatch_real_publishers.py
"""Proves the real LinkedInPublisher/InstagramPublisher classes work when
wired into dispatch_due, not just in isolation (Tasks 4-11's tests exercise
.publish() directly). Live dispatch is exercised with
dispatch.assert_live_publishing_allowed monkeypatched to a no-op, exactly
like tests/test_publishing_queue.py's live-outcome tests — auto_publish
itself is never touched (see CLAUDE.md's guardrail and
tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled)."""
from datetime import timedelta

import pytest

from radar.publishing import dispatch as d
from radar.publishing import queue as q
from radar.publishing.credentials import LinkedInCredential
from radar.publishing.linkedin import LinkedInPublisher
from tests._fake_http import FakeResponse, FakeSession
from tests.test_publishing_queue import BASE, _scheduled_item, iso


@pytest.fixture()
def live_gate_open(monkeypatch):
    monkeypatch.setattr(d, "assert_live_publishing_allowed", lambda: None)


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
```

Note: `_scheduled_item` (in `tests/test_publishing_queue.py`) enqueues a `linkedin_post_1` draft targeting whatever `target_account` `queue.enqueue` defaults to for a LinkedIn item — read `radar/publishing/queue.py::enqueue`'s default before running this task to confirm it resolves to `"founder_profile"` (matching `LinkedInCredential.person_urn` above); if it defaults to something else, adjust the credential fixture's populated URN field to match rather than changing `_scheduled_item`.

- [ ] **Step 2: Run tests to verify they fail first for the right reason, then pass**

Run: `python -m pytest tests/test_dispatch_real_publishers.py -v`
Expected: once Task 12's imports exist, these should PASS immediately (all the underlying pieces were already built in Tasks 4–9) — if they fail, the failure should point at a `target_account` mismatch (see the note above), not a missing module.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/test_dispatch_real_publishers.py
git commit -m "Add dispatch-layer wiring tests for the real LinkedIn/Instagram publishers"
```

---

### Task 14: Documentation, .env.example, final verification, push

**Files:**
- Modify: `.env.example`
- Modify: `INTELLIGENCE SYSTEM/roadmap.md`
- Modify: `INTELLIGENCE SYSTEM/milestones.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update `.env.example`**

Read the current file first, then append after the existing `COMTRADE_API_KEY` block:

```bash
# Phase 4 (radar/publishing/linkedin.py, radar/publishing/instagram.py) —
# real LinkedIn/Instagram API publishers satisfying dispatch.py's Publisher
# seam. NOT required for collection, verification, scoring, content
# generation, or the publishing queue's dry-run path, and not required for
# the test suite (publisher tests inject a fake HTTP session). Only used
# when queue-dispatch --live is run, which itself stays refused today
# because auto_publish is hard-coded false (CLAUDE.md, Section 30) —
# setting these alone does not enable live publishing.
#
# LinkedIn: obtain via a LinkedIn Developer Program app with Community
# Management API product access (requires LinkedIn's approval — this is
# not self-serve) and an OAuth 2.0 consent flow granting w_member_social
# and/or w_organization_social. Tokens are NOT auto-refreshed by this
# system (see docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md);
# re-supply manually when a token expires or is revoked.
# LINKEDIN_ACCESS_TOKEN=
# LINKEDIN_PERSON_URN=urn:li:person:XXXXXXXX
# LINKEDIN_ORG_URN=urn:li:organization:XXXXXXXX

# Instagram: obtain via a Meta developer app + an Instagram professional
# account (Business or Creator) connected to a Facebook Page, with content
# publishing permissions approved through Meta's App Review. Tokens are NOT
# auto-refreshed by this system; re-supply manually when a token expires.
# INSTAGRAM_ACCESS_TOKEN=
# INSTAGRAM_BUSINESS_ACCOUNT_ID=
```

- [ ] **Step 2: Update `INTELLIGENCE SYSTEM/roadmap.md`**

Read the file first (it was read in full earlier this session — Phase 4's current section starts `### Phase 4 — Social publishing integrations` and the stage-map row `| L | Social publishing integrations...`). Replace the `### Phase 4` section body with:

```markdown
### Phase 4 — Social publishing integrations (code built and tested, 2026-09-15; gate still closed)
`radar/publishing/linkedin.py` and `radar/publishing/instagram.py` now
satisfy Phase 3's `Publisher` protocol: LinkedIn (Posts/Images/Videos/
Documents/MultiImage APIs, contract verified against
learn.microsoft.com/en-us/linkedin/marketing/community-management/ on
2026-09-15, moniker li-lms-2026-08) and Instagram (Graph API content
publishing, verified against developers.facebook.com/docs/instagram-platform/
content-publishing/ on 2026-09-15, v25.0). Both read credentials from
environment variables only (`radar/publishing/credentials.py`), classify
every HTTP outcome through a shared `platform_http.py` (401/403 always
`permanent_error`, never silently retried), and are fully covered by unit
tests injecting a fake HTTP session — zero real network calls anywhere in
this codebase or its test suite. `auto_publish` remains hard-refused by
`settings.feature_flags()`, unchanged; `queue-dispatch --live` still refuses
before either publisher is ever called. See
`docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md` and
`milestones.md` for the full built/tested breakdown, and re-verify both
platforms' API versions against their live docs before ever flipping the
gate — this was checked once, not continuously.
```

Update the stage-map row `| L |` to: `**Code built and tested (2026-09-15); auto_publish gate still closed by design** — see Phase 4 above` | `radar/publishing/linkedin.py`, `radar/publishing/instagram.py`.

- [ ] **Step 3: Update `INTELLIGENCE SYSTEM/milestones.md`**

Append a new dated section after the Phase 3 entry, before "## Not started", following that file's existing evidence-based structure (what was built, what tests cover, what was deliberately not done):

```markdown
## Phase 4 — Social publishing integrations: code built and tested (2026-09-15)

Built per `docs/superpowers/specs/2026-09-15-phase4-social-publishing-design.md`,
with LinkedIn/Instagram API contracts verified against each platform's live
developer documentation (not model memory) before writing any code — see
that spec's "Verified API facts" and this plan's equivalent section for the
exact endpoints, request/response shapes, and doc URLs checked on
2026-09-15.

**Built and wired:**
- `radar/publishing/platform_http.py`: pure-function HTTP-outcome
  classification shared by both platforms — 401/403 always `permanent_error`
  (never retried), 429 `retryable_error` (with `Retry-After` if present),
  5xx `retryable_error`, other 4xx `permanent_error`, a transport exception
  always `unknown`.
- `radar/publishing/credentials.py`: env-var-only credential loading
  (`LINKEDIN_ACCESS_TOKEN`/`LINKEDIN_PERSON_URN`/`LINKEDIN_ORG_URN`,
  `INSTAGRAM_ACCESS_TOKEN`/`INSTAGRAM_BUSINESS_ACCOUNT_ID`) — the only seam
  either publisher touches `os.environ` through, shaped so a future
  token-refresh mechanism replaces these two functions' bodies without
  changing either publisher's constructor.
- `radar/publishing/linkedin.py`: `LinkedInPublisher` — text, image
  (Images API upload), organic MultiImage (2-20 images, explicitly distinct
  from LinkedIn's sponsored-only Carousel), document (Documents API,
  required title), and video (Videos API's always-chunked upload, ETag
  part IDs, `finalizeUpload`) post formats. `Linkedin-Version` header value
  pinned via `radar/config/publishing.yaml`'s new `platform_apis.linkedin.api_version`
  (`"202608"`), not hard-coded.
- `radar/publishing/instagram.py`: `InstagramPublisher` — image, carousel
  (up to 10 items), and Reel (container + bounded status polling, never
  auto-retried past the poll bound — returns `unknown` instead of guessing)
  post formats via the Graph API two-step container/publish flow. API
  version, poll attempt count, and poll interval all read from
  `publishing.yaml`'s new `platform_apis.instagram` keys. Error
  classification inspects the Graph API's JSON error body (`error.type`/
  `error.code`) before falling back to `platform_http`, since Graph API
  frequently returns HTTP 400 for both malformed requests and expired
  tokens alike.
- `radar/cli.py::cmd_queue_dispatch` now builds a `publishers` dict from
  whichever of the two credential functions return non-`None` — a platform
  with no env vars configured is simply absent, reusing `dispatch_due`'s
  existing "skipped: no publisher for X" behavior from Phase 3.

**Tests added:** 46 new tests across `tests/test_platform_http.py` (11),
`tests/test_credentials.py` (6), `tests/test_linkedin_publisher.py` (15),
`tests/test_instagram_publisher.py` (11), `tests/test_dispatch_real_publishers.py` (2),
plus 1 in `tests/test_cli.py`. Every publisher test injects a fake HTTP
session (`tests/_fake_http.py`) — zero real network calls in the suite.
Full suite: **316 passing** (was 270). If the actual run comes out
different, correct this paragraph to match the real numbers before
committing — never leave a stale or guessed count in milestones.md.

**Verified live, not just under pytest:** `radar init` → `radar queue-dispatch`
on an empty scratch DB with no platform env vars set (both publishers
absent, no error) and again with a fake `LINKEDIN_ACCESS_TOKEN`/
`LINKEDIN_PERSON_URN` set (publisher constructs cleanly; still "Nothing is
due" since nothing was queued and `--live` was not passed). No real
LinkedIn/Instagram API call was made anywhere in this phase, per the
founder's explicit instruction (condition 8 of the design spec).

**What was deliberately not done:** `auto_publish` remains hard-refused by
`settings.feature_flags()`, completely untouched; no OAuth token
acquisition/refresh was implemented (`credentials.py` is the seam, not a
refresh mechanism); no real HTTP call to either platform was made in this
session. Flipping the gate, or adding refresh, are each their own future
decision — see roadmap.md's Phase 4 section.
```

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS, **316 passing** (270 Phase 3 baseline + 11 `platform_http` + 6 `credentials` + 15 LinkedIn + 11 Instagram + 1 CLI + 2 dispatch wiring). If the real count differs, correct Step 3's milestones.md text to match the actual run before committing — never commit a guessed or stale count.

- [ ] **Step 5: Commit**

```bash
git add .env.example "INTELLIGENCE SYSTEM/roadmap.md" "INTELLIGENCE SYSTEM/milestones.md"
git commit -m "Document Phase 4 (social publishing integrations): built and tested, auto_publish gate still closed"
```

- [ ] **Step 6: Push**

```bash
git push
```

(Per the founder's explicit instruction to "complete the implementation, tests, documentation, commit and push as planned" — this pushes code only; no real publication occurs anywhere in this plan.)

---

## Post-plan reminders (not tasks — do not act on these without further explicit instruction)

- `auto_publish` is still hard-refused. Flipping it is a separate, explicit founder decision per `CLAUDE.md`.
- Before any real post is ever sent, re-verify both API contracts against live docs again (they were checked once, on 2026-09-15, for this plan) and obtain real LinkedIn Community Management API product access + a real Instagram professional account/Meta app — both are founder actions outside this codebase.
- Token refresh is unimplemented; a manually supplied token will eventually expire or be revoked, and this system currently surfaces that as a clean `permanent_error` (never a crash, never a silent retry) rather than handling it automatically.
