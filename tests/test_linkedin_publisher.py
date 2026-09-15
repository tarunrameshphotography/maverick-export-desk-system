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
