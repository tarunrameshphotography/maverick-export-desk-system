"""CBIC Tax Information Portal collector — customs notifications & circulars.

Section 4 "TOP PRIORITY gap": the old cbic.gov.in/entities/customs-notfns page
is an Angular SPA whose route now 404s back to the homepage; CBIC's current
notification/circular feed lives on a separate portal, taxinformation.cbic.gov.in
(its own Angular app), behind a plain JSON API discovered on 14 Sep 2026 by
watching that portal's own network calls in a real browser:
GET /api/cbic-notification-msts/fetchUpdatesByTaxId/{tax_id}.

No stable per-document permalink was found on this portal — its file-download
route (`/download/{id}/{language}`) needs a `language` argument whose valid
values aren't exposed anywhere public, and returns base64-encoded bytes rather
than a fetchable URL even when it works. Every item's URL therefore points at
the portal itself, with the exact circular/notification number and date
carried in body_text so a human (or a Claude Code verification pass) can look
the document up by that reference. That's a documented limitation, not a
silent gap: Section 8's "no claim without a quote" already requires a human
to open the primary document before anything is published, for every source.

taxinformation.cbic.gov.in serves an incomplete TLS certificate chain
(confirmed 14 Sep 2026 — Chrome tolerates it via AIA fetching; Python's ssl
module, and a fully up-to-date certifi bundle, do not). verify=False is used
deliberately and only for this one known government domain, not globally.
"""
from __future__ import annotations

from datetime import datetime

import requests
import urllib3

from radar.collectors.base import BROWSER_UA, Collector, CollectorError, RawItem

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT_SECONDS = 20
PORTAL_URL = "https://taxinformation.cbic.gov.in/"
TAX_IDS = {"customs": 1000002, "gst": 1000001, "central_excise": 1000003, "service_tax": 1000004}


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


class CbicCollector(Collector):
    method = "cbic_api"

    def collect(self, source: dict) -> list[RawItem]:
        tax_id = TAX_IDS.get(source.get("tax", "customs"), TAX_IDS["customs"])
        url = f"https://taxinformation.cbic.gov.in/api/cbic-notification-msts/fetchUpdatesByTaxId/{tax_id}"
        try:
            resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": BROWSER_UA}, verify=False)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise CollectorError(f"{source['id']}: request failed: {exc}") from exc
        except ValueError as exc:
            raise CollectorError(f"{source['id']}: invalid JSON response: {exc}") from exc

        items: list[RawItem] = []
        for entry in payload:
            name = (entry.get("notificationName") or "").strip()
            if not name:
                continue
            update_type = entry.get("updateType") or "Notification"
            number = entry.get("notificationNo") or ""
            date_raw = entry.get("updatedDate")
            title = f"CBIC {update_type} {number}: {name}".strip()
            items.append(RawItem(
                source_id=source["id"],
                title=title,
                url=PORTAL_URL,
                body_text=(
                    f"CBIC {update_type} No. {number}, dated {date_raw or 'unknown'}: {name} "
                    f"(look this up on the CBIC Tax Information Portal by exact number and date — "
                    f"no direct document link is available from this source)."
                ),
                published_at=_iso_date(date_raw),
            ))
        return items
