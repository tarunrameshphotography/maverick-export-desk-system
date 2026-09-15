"""Verification pipeline (Section 11, Part 11 verification protocol).

The mechanical half (this module) extracts candidate claims — sentences that
look like they assert a fact, rate, date or rule — into the claims table with
status='pending'. The actual verification (opening the primary document,
recording the exact quote) is analyst/Claude-Code work per the hybrid
decision; mark_claim() is the guardrail that enforces "no claim without a
quote" (automation_architecture.md) no matter who calls it.
"""
from __future__ import annotations

import re
import sqlite3

CLAIM_WORTHY_PATTERN = re.compile(
    # numbers and money
    r"\d+(\.\d+)?\s*%|₹\s*\d|\$\s*\d|\bcrore\b|\bmillion\b|\bbillion\b|"
    # dates and timing
    r"\beffective\b|\bnotified\b|\bextended\b|\bdeadline\b|\bw\.e\.f\.?\b|\binto effect\b|"
    # rule statements (eligibility, validity, obligation, determinations) — Section 11's
    # "laws, regulations, customs rules, eligibility, compliance" all need verifying
    r"\brates?\b|\bvalid(?:ity)?\b|\btransfer(?:red|able)?\b|\beligib\w*|\bmandatory\b|\brequired\b|"
    r"\bshall\b|\bprohibit\w*|\bwithdrawn\b|\bamend\w*|\bdetermin\w*|\bclarif\w*|\bsubstitut\w*",
    re.I,
)
VALID_CLAIM_TYPES = {"fact", "interpretation", "forecast", "opinion", "verify"}
VALID_STATUSES = {"verified", "pending", "failed", "superseded"}


def _split_sentences(text: str) -> list[str]:
    # Deliberately simple (Section 24 "lightweight dependencies") — good
    # enough for notification/press-release prose, not literary text.
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if s.strip()]


def extract_candidate_claims(text: str) -> list[str]:
    from radar.pipeline.entities import DATE_LITERAL

    sentences = _split_sentences(text)
    seen: set[str] = set()
    candidates = []
    for sentence in sentences:
        worthy = CLAIM_WORTHY_PATTERN.search(sentence) or DATE_LITERAL.search(sentence)
        if worthy and sentence not in seen:
            seen.add(sentence)
            candidates.append(sentence)
    return candidates


def build_claims_table(conn: sqlite3.Connection, signal_id: str) -> int:
    """Populates claims (status='pending', claim_type='verify') from every
    raw_item linked to the signal. Idempotent per source: an item whose URL
    already has claims is skipped, so a primary document that joins the signal
    on a later run still gets its claims extracted. Returns the number inserted."""
    seen_urls = {r["source_url"] for r in conn.execute("SELECT source_url FROM claims WHERE signal_id = ?", (signal_id,))}

    rows = conn.execute(
        """
        SELECT ri.title, ri.body_text, ri.url, ri.canonical_url, ri.published_at, s.kind
        FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ?
        """,
        (signal_id,),
    ).fetchall()

    inserted = 0
    for row in rows:
        if (row["canonical_url"] or row["url"]) in seen_urls:
            continue
        for claim_text in extract_candidate_claims(f"{row['title']}. {row['body_text'] or ''}"):
            conn.execute(
                """
                INSERT INTO claims (signal_id, claim_text, claim_type, source_url, source_title,
                                     source_type, source_date, status)
                VALUES (?, ?, 'verify', ?, ?, ?, ?, 'pending')
                """,
                (signal_id, claim_text, row["canonical_url"] or row["url"], row["title"], row["kind"], row["published_at"]),
            )
            inserted += 1
    conn.commit()
    return inserted


def _check_quote(conn: sqlite3.Connection, signal_id: str, source_url: str | None, quote: str) -> str:
    """Raises if a stored copy of the source exists and the quote isn't in it.
    Returns the provenance note to store with the claim."""
    from radar.pipeline.evidence import quote_found

    found = quote_found(conn, signal_id, source_url, quote) if source_url else None
    if found is False:
        raise ValueError(
            f"Quote not found in the stored copy of {source_url}. Quote the document verbatim "
            f"(`python -m radar fetch {signal_id}` shows the stored text path)."
        )
    if found is None:
        return "Quote NOT machine-checked: no stored copy of this source (PDF, aggregator link, or not fetched)."
    return "Quote machine-checked against the stored copy of the source."


FACT_CLAIM_TYPES = {"fact", "verify"}


def _collected_source_evidence(conn: sqlite3.Connection, signal_id: str, source_url: str | None) -> str | None:
    """Evidence level of the collected item this URL came from, if it is one of
    the signal's raw_items; None for a URL found during research."""
    from radar.pipeline.source_roles import profile_for

    if not source_url:
        return None
    row = conn.execute(
        """
        SELECT ri.source_id, s.kind FROM raw_items ri JOIN sources s ON s.id = ri.source_id
        WHERE ri.signal_id = ? AND (ri.url = ? OR ri.canonical_url = ?)
        """,
        (signal_id, source_url, source_url),
    ).fetchone()
    return profile_for(row["source_id"], row["kind"])["evidence"] if row else None


def _check_evidence_authority(
    conn: sqlite3.Connection, signal_id: str, source_url: str | None, source_type: str, claim_type: str
) -> None:
    """A secondary source can start an investigation; it cannot verify a fact.
    Fact claims verify only against a primary source; lead-only sources (social,
    reposts, field notes) verify nothing. A collected secondary URL can't be
    relabelled primary to get past this."""
    collected = _collected_source_evidence(conn, signal_id, source_url)
    if source_type == "primary" and collected not in (None, "primary"):
        raise ValueError(f"{source_url} was collected from a {collected} source; it can't be recorded as primary evidence.")
    level = collected or source_type
    if level == "lead_only":
        raise ValueError(
            "Lead-only source (social post, repost, field note): it can start an investigation but verifies "
            "nothing. Find the primary document and record the claim against it (claim-add --source-type primary)."
        )
    if claim_type in FACT_CLAIM_TYPES and level != "primary":
        raise ValueError(
            "A fact claim can only be verified against a primary source. This source is secondary — record the "
            "claim against the primary document (claim-add --source-type primary), or classify it as an "
            "attributed interpretation/opinion."
        )


def add_claim(
    conn: sqlite3.Connection,
    signal_id: str,
    claim_text: str,
    claim_type: str,
    source_url: str,
    source_type: str,
    now_iso: str,
    source_title: str | None = None,
    source_date: str | None = None,
    source_quote: str | None = None,
    verified_by: str | None = None,
) -> int:
    """For claims found during research rather than extracted from a collected
    item — e.g. the TradeStat figure behind an exposure line. Inserted as
    'verified' only when a quote is supplied; otherwise 'pending'."""
    if claim_type not in VALID_CLAIM_TYPES:
        raise ValueError(f"Invalid claim type: {claim_type}")
    if source_type not in ("primary", "secondary"):
        raise ValueError(f"Invalid source type: {source_type}")
    status = "verified" if source_quote else "pending"
    if source_quote:
        _check_evidence_authority(conn, signal_id, source_url, source_type, claim_type)
    elif source_type == "primary":
        _check_evidence_authority(conn, signal_id, source_url, source_type, "opinion")
    note = _check_quote(conn, signal_id, source_url, source_quote) if source_quote else None
    cur = conn.execute(
        """
        INSERT INTO claims (signal_id, claim_text, claim_type, source_url, source_title, source_type,
                             source_date, source_quote, status, verified_by, verified_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id, claim_text, claim_type, source_url, source_title, source_type, source_date,
            source_quote, status, verified_by if source_quote else None, now_iso if source_quote else None, note,
        ),
    )
    conn.commit()
    return cur.lastrowid


def mark_claim(
    conn: sqlite3.Connection,
    claim_id: int,
    status: str,
    now_iso: str,
    claim_type: str | None = None,
    source_quote: str | None = None,
    verified_by: str | None = None,
    notes: str | None = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid claim status: {status}")
    if claim_type is not None and claim_type not in VALID_CLAIM_TYPES:
        raise ValueError(f"Invalid claim type: {claim_type}")
    if status == "verified" and not source_quote:
        raise ValueError("Cannot mark a claim verified without a source_quote (no claim without a quote).")
    if status == "verified":
        claim = conn.execute(
            "SELECT signal_id, source_url, source_type, claim_type FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise ValueError(f"No such claim: {claim_id}")
        _check_evidence_authority(
            conn, claim["signal_id"], claim["source_url"], claim["source_type"], claim_type or claim["claim_type"]
        )
        provenance = _check_quote(conn, claim["signal_id"], claim["source_url"], source_quote)
        notes = f"{notes} | {provenance}" if notes else provenance

    fields = ["status = ?"]
    params: list = [status]
    if claim_type is not None:
        fields.append("claim_type = ?")
        params.append(claim_type)
    if source_quote is not None:
        fields.append("source_quote = ?")
        params.append(source_quote)
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if status in ("verified", "failed"):
        fields += ["verified_by = ?", "verified_at = ?"]
        params += [verified_by, now_iso]

    params.append(claim_id)
    conn.execute(f"UPDATE claims SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def verification_status_summary(conn: sqlite3.Connection, signal_id: str) -> dict:
    rows = conn.execute("SELECT status FROM claims WHERE signal_id = ?", (signal_id,)).fetchall()
    total = len(rows)
    by_status = {status: 0 for status in VALID_STATUSES}
    for row in rows:
        by_status[row["status"]] += 1
    return {
        "total": total,
        **by_status,
        "fully_verified": total > 0 and by_status["pending"] == 0 and by_status["failed"] == 0,
    }


def render_verification_worksheet(conn: sqlite3.Connection, signal_id: str) -> str:
    """The structured task file for the hybrid interactive step: open each
    primary source, confirm or refute each candidate claim, and record the
    exact quoted passage."""
    signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    claims = conn.execute("SELECT * FROM claims WHERE signal_id = ? ORDER BY id", (signal_id,)).fetchall()

    lines = [
        f"# Verification worksheet — {signal_id}",
        "",
        f"**Title:** {signal['title']}",
        f"**Primary source URL:** {signal['primary_source_url'] or '(none recorded — check raw_items)'}",
        "",
        "For each claim below: open the source, confirm effective vs. notified date,",
        "record the exact quoted passage, and tag fact/interpretation/forecast/opinion.",
        "",
    ]
    docs = conn.execute("SELECT * FROM evidence_docs WHERE signal_id = ? ORDER BY id", (signal_id,)).fetchall()
    lines.append("## Stored primary documents")
    if docs:
        for d in docs:
            if d["status"] == "stored":
                lines.append(f"- STORED {d['chars']:,} chars · `{d['path']}` · {d['url']}")
            else:
                lines.append(f"- {d['status'].upper()}: {d['url']} — {d['note']}")
    else:
        lines.append(f"- _None yet — run `python -m radar fetch {signal_id}`._")
    lines.append("")

    for claim in claims:
        lines += [
            f"## Claim #{claim['id']} — status: {claim['status']}",
            f"- Text: {claim['claim_text']}",
            f"- Source ({claim['source_type']}): {claim['source_url']}",
            f"- Source date: {claim['source_date'] or 'unknown'}",
            f"- Quoted passage: {claim['source_quote'] or '_(fill in)_'}",
        ]
        if claim["notes"]:
            lines.append(f"- Provenance: {claim['notes']}")
        lines.append("")
    if not claims:
        lines.append("_No candidate claims extracted — verify manually against the primary source._")
    return "\n".join(lines)
