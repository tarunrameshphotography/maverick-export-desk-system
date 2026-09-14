"""Mocked/sample export signals (Section 32/33) so the pipeline can be
exercised end-to-end without live web access. Ten raw items forming five
underlying stories (RoDTEP/RoSCTL extension x4, FEMA 2026 x1, US CVD
oleoresin paprika x2, WTO trade monitoring x1, e-scrip trade notice x1,
Export Promotion Mission x1) — enough duplication to test dedup/clustering."""
from __future__ import annotations

import json
from pathlib import Path

from radar.collectors.base import Collector, RawItem

FIXTURE_PATH = Path(__file__).resolve().parent / "sample_signals.json"


def load_sample_raw_items() -> list[RawItem]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [RawItem(**row) for row in payload]


class SampleDataCollector(Collector):
    """Ignores the source config's URL entirely; returns the fixture items
    whose source_id matches. Used only when --sample is requested."""

    method = "sample"

    def collect(self, source: dict) -> list[RawItem]:
        return [item for item in load_sample_raw_items() if item.source_id == source["id"]]
