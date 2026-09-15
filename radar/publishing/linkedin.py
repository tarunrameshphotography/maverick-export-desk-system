"""LinkedInPublisher -- the Posts/Images/Videos/Documents/MultiImage API
seam Phase 4 supplies. Contract verified against learn.microsoft.com's
LinkedIn API docs (li-lms-2026-08 moniker) on 2026-09-15 -- re-check before
this system ever goes live, LinkedIn versions monthly. `auto_publish` stays
hard-refused; nothing here changes that."""
from __future__ import annotations

import json
from pathlib import Path

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
            if payload.post_format == "linkedin_image":
                media = payload.media[0]
                upload = self._upload_image(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author,
                                  content={"media": {"id": upload, "altText": media.get("alt_text") or ""}})
            if payload.post_format == "linkedin_multi_image":
                images = []
                for media in payload.media:
                    upload = self._upload_image(media, author)
                    if isinstance(upload, PublishResult):
                        return upload
                    images.append({"id": upload, "altText": media.get("alt_text") or ""})
                return self._post(payload, author, content={"multiImage": {"images": images}})
            if payload.post_format == "linkedin_document":
                media = payload.media[0]
                upload = self._upload_document(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author,
                                  content={"media": {"id": upload, "title": media.get("title") or "Document"}})
            if payload.post_format == "linkedin_video":
                media = payload.media[0]
                upload = self._upload_video(media, author)
                if isinstance(upload, PublishResult):
                    return upload
                return self._post(payload, author, content={"media": {"id": upload, "title": media.get("title") or ""}})
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
        outcome, retry_after = platform_http.classify_response(resp.status_code, resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        post_id = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
        if not post_id:
            return PublishResult("unknown", error_code="no_post_id",
                                 error_message="201 response carried no x-restli-id header")
        return PublishResult("published", external_post_id=post_id,
                             external_url=f"https://www.linkedin.com/feed/update/{post_id}/")

    def _upload_image(self, media_row: dict, owner: str) -> str | PublishResult:
        resp = self.session.post(
            f"{API_BASE}/images?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner}}, timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code, resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        body = resp.json()["value"]
        upload_url, urn = body["uploadUrl"], body["image"]
        with open(media_row["local_path"], "rb") as fh:
            put_resp = self.session.put(upload_url, headers={"Authorization": f"Bearer {self.credential.access_token}"},
                                        data=fh, timeout=120)
        outcome, retry_after = platform_http.classify_response(put_resp.status_code, put_resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"upload_http_{put_resp.status_code}",
                                 error_message=_error_body(put_resp), retry_after_seconds=retry_after)
        return urn

    def _upload_document(self, media_row: dict, owner: str) -> str | PublishResult:
        resp = self.session.post(
            f"{API_BASE}/documents?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner}}, timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code, resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"http_{resp.status_code}",
                                 error_message=_error_body(resp), retry_after_seconds=retry_after)
        body = resp.json()["value"]
        upload_url, urn = body["uploadUrl"], body["document"]
        with open(media_row["local_path"], "rb") as fh:
            put_resp = self.session.put(upload_url, headers={"Authorization": f"Bearer {self.credential.access_token}"},
                                        data=fh, timeout=120)
        outcome, retry_after = platform_http.classify_response(put_resp.status_code, put_resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"upload_http_{put_resp.status_code}",
                                 error_message=_error_body(put_resp), retry_after_seconds=retry_after)
        return urn

    def _upload_video(self, media_row: dict, owner: str) -> str | PublishResult:
        file_size = media_row.get("byte_size") or Path(media_row["local_path"]).stat().st_size
        resp = self.session.post(
            f"{API_BASE}/videos?action=initializeUpload", headers=self._headers(),
            json={"initializeUploadRequest": {"owner": owner, "fileSizeBytes": file_size,
                                              "uploadCaptions": False, "uploadThumbnail": False}},
            timeout=30,
        )
        outcome, retry_after = platform_http.classify_response(resp.status_code, resp.headers)
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
                outcome, retry_after = platform_http.classify_response(put_resp.status_code, put_resp.headers)
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
        outcome, retry_after = platform_http.classify_response(fin_resp.status_code, fin_resp.headers)
        if outcome != "ok":
            return PublishResult(outcome, error_code=f"finalize_http_{fin_resp.status_code}",
                                 error_message=_error_body(fin_resp), retry_after_seconds=retry_after)
        return video_urn
