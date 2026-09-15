"""Shared HTTP-outcome classification for platform publishers. This module
knows nothing about LinkedIn or Instagram specifically -- it only turns a
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
