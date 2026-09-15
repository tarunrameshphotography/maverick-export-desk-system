"""What each source is *for*: discovery, evidence, context, saturation.

Tiers and their role ratings live in sources.yaml (`source_tiers`). This
module resolves a source's profile, counts independent corroborating origins
(syndicated copies of one wire story are one origin, not five), and assembles
a signal's provenance grouped by role: primary evidence, independent
corroboration, discovery leads, context, saturation."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

from radar import settings
from radar.pipeline.dedupe import _parse_date, title_similarity


def source_profile(source: dict, tiers: dict | None = None) -> dict:
    tier_name = source.get("tier") or ("primary_official" if source.get("kind") == "primary" else "secondary_media")
    tier = (tiers or settings.source_tiers())[tier_name]
    return {
        "tier": tier_name,
        "kind": tier["kind"],
        "evidence": tier["evidence"],
        "corroborates": tier["corroborates"],
        **tier["roles"],
        **(source.get("roles") or {}),
    }


def profiles_by_source_id() -> dict[str, dict]:
    tiers = settings.source_tiers()
    return {s["id"]: source_profile(s, tiers) for s in settings.sources()}


def profile_for(source_id: str, kind: str, profiles: dict[str, dict] | None = None) -> dict:
    profiles = profiles_by_source_id() if profiles is None else profiles
    return profiles.get(source_id) or source_profile({"kind": kind})


def _publisher(item: dict) -> str:
    return (item.get("publisher") or urlsplit(item.get("canonical_url") or item.get("url") or "").netloc).removeprefix("www.")


def _wire(item: dict, wires: dict) -> str | None:
    """The wire service behind an item: credited in its text, or its own domain."""
    publisher = _publisher(item)
    text = f"{item.get('title') or ''} {item.get('body_text') or ''}"
    for name, domain in wires.items():
        if (domain and publisher == domain) or re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def _same_origin(a: dict, b: dict, threshold: float, wires: dict) -> bool:
    pub_a = _publisher(a)
    if pub_a and pub_a == _publisher(b):
        return True
    wire_a = _wire(a, wires)
    if wire_a and wire_a == _wire(b, wires):
        return True
    return title_similarity(a.get("title") or "", b.get("title") or "") >= threshold


def independent_origins(items: list[dict]) -> list[list[dict]]:
    """Groups items into independent origins; each group is one original report
    plus its copies."""
    cfg = settings.provenance_config()
    threshold = cfg.get("syndication_title_similarity", 0.75)
    wires = cfg.get("wire_services") or {}
    origins: list[list[dict]] = []
    for item in items:
        home = next((o for o in origins if any(_same_origin(item, m, threshold, wires) for m in o)), None)
        if home is None:
            origins.append([item])
        else:
            home.append(item)
    return origins


def corroborating_origin_count(rows: list[dict], profiles: dict[str, dict] | None = None) -> int:
    """Independent secondary origins that may count toward the two-source rule.
    Lead-only tiers (social, reposts, field notes) never count."""
    profiles = profiles_by_source_id() if profiles is None else profiles
    corroborating = [
        r for r in rows
        if r["kind"] == "secondary" and profile_for(r["source_id"], r["kind"], profiles)["corroborates"]
    ]
    return len(independent_origins(corroborating))


def _sort_key(row: dict) -> datetime:
    return _parse_date(row.get("published_at")) or _parse_date(row.get("collected_at")) or datetime.max.replace(tzinfo=timezone.utc)


def signal_provenance(conn: sqlite3.Connection, signal_id: str) -> dict:
    rows = [dict(r) for r in conn.execute(
        """
        SELECT ri.id AS raw_item_id, ri.source_id, ri.title, ri.body_text, ri.url, ri.canonical_url,
               ri.publisher, ri.published_at, ri.collected_at, s.kind, s.name AS source_name
        FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ?
        """,
        (signal_id,),
    ).fetchall()]
    rows.sort(key=_sort_key)
    profiles = profiles_by_source_id()
    for r in rows:
        r["profile"] = profile_for(r["source_id"], r["kind"], profiles)

    primary = [r for r in rows if r["profile"]["evidence"] == "primary"]
    corroborating = [r for r in rows if r["profile"]["evidence"] == "secondary" and r["profile"]["corroborates"]]
    saturating = [r for r in rows if r["profile"]["saturation"] in ("medium", "high")]
    return {
        "signal_id": signal_id,
        "first_seen": rows[0] if rows else None,
        "primary": primary,
        "corroboration": independent_origins(corroborating),
        "leads": [r for r in rows if r["profile"]["evidence"] == "lead_only"],
        "context": [r for r in rows if r["profile"]["context"] == "high" and r["profile"]["evidence"] != "primary"],
        "saturation": {"items": len(saturating), "publishers": sorted({_publisher(r) for r in saturating} - {""})},
    }


def _line(r: dict) -> str:
    return f"  - {r['source_name']} [{r['profile']['tier']}] — {r['title']} <{r['canonical_url'] or r['url']}>"


def format_provenance(p: dict) -> str:
    out = [f"Provenance for {p['signal_id']}"]
    if p["first_seen"]:
        out.append(f"FIRST SEEN: {p['first_seen']['source_name']} ({p['first_seen']['published_at'] or 'undated'})")
    out.append("PRIMARY (evidence authority):")
    out += [_line(r) for r in p["primary"]] or ["  (none — no fact claim can be verified until the primary document is found)"]
    out.append(f"SECONDARY (independent corroboration: {len(p['corroboration'])} origin(s)):")
    for origin in p["corroboration"]:
        out.append(_line(origin[0]) + (f"  (+{len(origin) - 1} copies)" if len(origin) > 1 else ""))
    out.append("DISCOVERY LEADS (lead only — not evidence):")
    out += [_line(r) for r in p["leads"]] or ["  (none)"]
    out.append("CONTEXT:")
    out += [_line(r) for r in p["context"]] or ["  (none)"]
    sat = p["saturation"]
    out.append(f"SATURATION: {sat['items']} item(s) across {len(sat['publishers'])} outlet(s)")
    return "\n".join(out)
