"""URL canonicalisation and content hashing (Section 6 dedup keys, Section 32 tests)."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "ncid", "mc_cid", "mc_eid"}


def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop tracking params and fragment, sort remaining
    query params, strip a single trailing slash. Two URLs that differ only in
    tracking parameters or param order canonicalise to the same string."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[len("www."):]

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PARAM_PREFIXES)
    ]
    kept_params.sort()
    query = urlencode(kept_params)

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_title(title: str) -> str:
    text = title.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compute_content_hash(canonical_url: str, title: str) -> str:
    """Prefer the canonical URL as the identity key (it is a strong signal
    two collectors saw the same document). Fall back to a normalized-title
    hash for sources where different pages legitimately share a URL pattern
    but not the exact document (rare in this registry, but keeps the
    function honest for pagewatch listings with query-string-only links)."""
    basis = canonical_url if canonical_url else _normalize_title(title)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
