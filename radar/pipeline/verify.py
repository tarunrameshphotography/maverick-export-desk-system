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
    sentences = _split_sentences(text)
    seen: set[str] = set()
    candidates = []
    for sentence in sentences:
        if CLAIM_WORTHY_PATTERN.search(sentence) and sentence not in seen:
            seen.add(sentence)
            candidates.append(sentence)
    return candidates


def build_claims_table(conn: sqlite3.Connection, signal_id: str) -> int:
    """Populates claims (status='pending', claim_type='verify') from every
    raw_item linked to the signal. Idempotent: skips if claims already exist
    for this signal. Returns the number of claims inserted."""
    existing = conn.execute("SELECT COUNT(*) AS n FROM claims WHERE signal_id = ?", (signal_id,)).fetchone()["n"]
    if existing:
        return 0

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
    cur = conn.execute(
        """
        INSERT INTO claims (signal_id, claim_text, claim_type, source_url, source_title, source_type,
                             source_date, source_quote, status, verified_by, verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id, claim_text, claim_type, source_url, source_title, source_type, source_date,
            source_quote, status, verified_by if source_quote else None, now_iso if source_quote else None,
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
    for claim in claims:
        lines += [
            f"## Claim #{claim['id']} — status: {claim['status']}",
            f"- Text: {claim['claim_text']}",
            f"- Source ({claim['source_type']}): {claim['source_url']}",
            f"- Source date: {claim['source_date'] or 'unknown'}",
            f"- Quoted passage: {claim['source_quote'] or '_(fill in)_'}",
            "",
        ]
    if not claims:
        lines.append("_No candidate claims extracted — verify manually against the primary source._")
    return "\n".join(lines)
