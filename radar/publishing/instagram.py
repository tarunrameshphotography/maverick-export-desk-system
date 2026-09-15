"""InstagramPublisher -- the Meta Graph API content-publishing seam Phase 4
supplies. Contract verified against developers.facebook.com/docs/
instagram-platform/content-publishing/ on 2026-09-15 (v25.0 was current) --
re-check before this system ever goes live, Meta ships a new major roughly
quarterly. `access_token` is sent as a request parameter here, the
long-documented and still-supported Graph API form; Meta's docs also show a
newer `Authorization: Bearer` header form in places -- re-verify which is
current before going live. `auto_publish` stays hard-refused; nothing here
changes that."""
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
        HTTP 400 -- checked before falling back to the generic HTTP-status
        classification."""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            if err.get("code") in _RETRYABLE_GRAPH_ERROR_CODES:
                return "retryable_error", None
            if err.get("type") == "OAuthException":
                return "permanent_error", None
            return "permanent_error", None
        return platform_http.classify_response(resp.status_code, resp.headers)

    def publish(self, payload: PublishPayload) -> PublishResult:
        try:
            if payload.post_format == "instagram_image":
                return self._publish_image(payload)
            if payload.post_format == "instagram_carousel":
                return self._publish_carousel(payload)
            if payload.post_format == "instagram_reel":
                return self._publish_reel(payload)
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
