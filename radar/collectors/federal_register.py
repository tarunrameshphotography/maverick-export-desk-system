"""US Federal Register API collector — public API, no key required.
https://www.federalregister.gov/developers/documentation/api/v1
"""
from __future__ import annotations

import requests

from radar.collectors.base import Collector, CollectorError, RawItem

TIMEOUT_SECONDS = 20


class FederalRegisterCollector(Collector):
    method = "api"

    def collect(self, source: dict) -> list[RawItem]:
        query = source.get("query", "India")
        params = {
            "conditions[term]": query,
            "order": "newest",
            "per_page": 20,
            "fields[]": ["title", "html_url", "abstract", "publication_date", "document_number"],
        }
        try:
            resp = requests.get(source["url"], params=params, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise CollectorError(f"{source['id']}: request failed: {exc}") from exc
        except ValueError as exc:
            raise CollectorError(f"{source['id']}: invalid JSON response: {exc}") from exc

        items: list[RawItem] = []
        for doc in payload.get("results", []):
            title = (doc.get("title") or "").strip()
            url = doc.get("html_url") or ""
            if not title or not url:
                continue
            body = doc.get("abstract") or ""
            doc_number = doc.get("document_number")
            if doc_number:
                body = f"[Federal Register doc {doc_number}] {body}"
            items.append(
                RawItem(
                    source_id=source["id"],
                    title=title,
                    url=url,
                    body_text=body,
                    published_at=doc.get("publication_date"),
                )
            )
        return items
