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
