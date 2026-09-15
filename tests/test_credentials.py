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
