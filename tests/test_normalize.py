from radar.pipeline.normalize import canonicalize_url, compute_content_hash


def test_canonicalize_strips_tracking_params():
    a = canonicalize_url("https://example.com/notif?utm_source=fb&id=74")
    b = canonicalize_url("https://example.com/notif?id=74")
    assert a == b


def test_canonicalize_ignores_www_and_case():
    a = canonicalize_url("https://WWW.Example.com/Notif")
    b = canonicalize_url("https://example.com/Notif")
    assert a == b


def test_canonicalize_sorts_query_params():
    a = canonicalize_url("https://example.com/x?b=2&a=1")
    b = canonicalize_url("https://example.com/x?a=1&b=2")
    assert a == b


def test_canonicalize_strips_trailing_slash():
    a = canonicalize_url("https://example.com/notif/")
    b = canonicalize_url("https://example.com/notif")
    assert a == b


def test_canonicalize_empty_string_is_empty():
    assert canonicalize_url("") == ""


def test_content_hash_same_url_same_hash():
    url = canonicalize_url("https://example.com/notif?id=74")
    assert compute_content_hash(url, "Title A") == compute_content_hash(url, "Title B")


def test_content_hash_different_url_different_hash():
    u1 = canonicalize_url("https://example.com/a")
    u2 = canonicalize_url("https://example.com/b")
    assert compute_content_hash(u1, "same title") != compute_content_hash(u2, "same title")


def test_content_hash_falls_back_to_title_when_no_url():
    h1 = compute_content_hash("", "DGFT extends RoDTEP rates")
    h2 = compute_content_hash("", "dgft   extends RODTEP rates!!")
    assert h1 == h2  # normalized-title fallback ignores case/punctuation/whitespace
