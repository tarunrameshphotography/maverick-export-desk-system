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
