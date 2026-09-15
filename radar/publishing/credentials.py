"""Reads platform credentials from environment variables only. This is the
one seam a future token-refresh mechanism replaces: LinkedInPublisher and
InstagramPublisher never call os.environ themselves, only this module does,
and only through the two functions below. A refresh implementation later
would change the body of these two functions (e.g. read a refresh token,
call the platform's token endpoint, write the new token back) without
touching either publisher's constructor signature. A manually supplied
token is not assumed permanent -- an expired/revoked one surfaces as a
platform_http permanent_error at call time (see linkedin.py/instagram.py),
not as a crash here."""
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
