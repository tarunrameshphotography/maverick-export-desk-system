"""Collector interface. Every collector returns a list of RawItem dicts and
must never raise past collect() — failures are caught by registry.run_collection
so one dead source cannot take down the run (Section 29 / 32 "failed-source handling")."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class RawItem:
    source_id: str
    title: str
    url: str
    body_text: str = ""
    published_at: Optional[str] = None  # ISO 8601, may be None if unknown
    language: str = "en"


class CollectorError(Exception):
    """Raised by a collector implementation; caught and logged by the registry."""


class Collector:
    """Base class. Subclasses implement collect(source_config) -> list[RawItem]."""

    method: str = "base"

    def collect(self, source: dict) -> list[RawItem]:
        raise NotImplementedError


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
