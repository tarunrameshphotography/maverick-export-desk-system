-- Maverick Export Signal Radar — schema v1 (SQLite now, Postgres-portable later).
-- Table names follow Section 21 of the master prompt; fields draw on
-- INTELLIGENCE SYSTEM/database_design.md. Timestamps are ISO 8601 TEXT.
-- Booleans are INTEGER 0/1 (portable to Postgres BOOLEAN with a cast).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    url                 TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('primary', 'secondary')),
    signal_type         TEXT NOT NULL CHECK (signal_type IN ('early', 'confirm', 'enforced', 'context')),
    cadence             TEXT,
    method              TEXT NOT NULL CHECK (method IN ('rss', 'api', 'pagewatch', 'email', 'manual')),
    country             TEXT,
    category            TEXT,
    reliability_1to5    INTEGER NOT NULL CHECK (reliability_1to5 BETWEEN 1 AND 5),
    active              INTEGER NOT NULL DEFAULT 1,
    query               TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS raw_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           TEXT NOT NULL REFERENCES sources(id),
    url                 TEXT,
    canonical_url       TEXT,
    title               TEXT,
    body_text           TEXT,
    published_at        TEXT,
    collected_at        TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    language            TEXT DEFAULT 'en',
    signal_id           TEXT REFERENCES signals(id),
    status              TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'CLUSTERED', 'DISCARDED')),
    discard_reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_items_hash ON raw_items(content_hash);
CREATE INDEX IF NOT EXISTS idx_raw_items_canonical_url ON raw_items(canonical_url);
CREATE INDEX IF NOT EXISTS idx_raw_items_status ON raw_items(status);

CREATE TABLE IF NOT EXISTS signals (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    source_published_at TEXT,
    primary_source_url  TEXT,
    primary_doc_ref     TEXT,
    topic_category      TEXT NOT NULL DEFAULT 'uncategorised',
    lifecycle_stage     TEXT CHECK (lifecycle_stage IN ('draft', 'notified', 'effective', 'enforced', 'reported')),
    announcement_date   TEXT,
    effective_date      TEXT,
    deadline_date       TEXT,
    exposure_json       TEXT,
    score_base          REAL,
    confidence_mult     REAL,
    penalties           REAL,
    score_final         REAL,
    score_breakdown_json TEXT,
    saturation_count_72h INTEGER,
    saturation_label    TEXT,
    status              TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN (
        'NEW', 'TRIAGED', 'SCORED', 'NEEDS_RESEARCH', 'VERIFIED',
        'READY_FOR_ANGLE', 'READY_FOR_REVIEW', 'APPROVED', 'PUBLISHED',
        'REJECTED', 'WATCHLIST', 'NEEDS_CORRECTION', 'ARCHIVED'
    )),
    decision            TEXT CHECK (decision IN ('lead', 'secondary', 'brief', 'watchlist', 'discard')),
    decision_reason     TEXT,
    previously_covered  INTEGER NOT NULL DEFAULT 0,
    similarity_max      REAL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score_final);

CREATE TABLE IF NOT EXISTS signal_related (
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    related_signal_id   TEXT NOT NULL REFERENCES signals(id),
    PRIMARY KEY (signal_id, related_signal_id)
);

CREATE TABLE IF NOT EXISTS signal_entities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    entity_type         TEXT NOT NULL CHECK (entity_type IN (
        'country', 'origin_country', 'destination_country', 'product', 'hs_code',
        'industry', 'sector', 'regulation', 'scheme', 'authority', 'company', 'port',
        'trade_agreement', 'tariff_rate', 'date', 'effective_date', 'announcement_date',
        'deadline', 'affected_type', 'cluster'
    )),
    raw_value           TEXT NOT NULL,
    normalized_value    TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_signal_entities_signal ON signal_entities(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_entities_type_value ON signal_entities(entity_type, normalized_value);

CREATE TABLE IF NOT EXISTS claims (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    claim_text          TEXT NOT NULL,
    claim_type          TEXT NOT NULL CHECK (claim_type IN ('fact', 'interpretation', 'forecast', 'opinion', 'verify')),
    source_url          TEXT,
    source_title        TEXT,
    source_type         TEXT CHECK (source_type IN ('primary', 'secondary')),
    source_date         TEXT,
    source_quote        TEXT,
    confidence          TEXT,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('verified', 'pending', 'failed', 'superseded')),
    verified_by         TEXT,
    verified_at         TEXT,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_signal ON claims(signal_id);

CREATE TABLE IF NOT EXISTS analyses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    franchise           TEXT,
    angle_pattern       TEXT,
    angle_label         TEXT NOT NULL,
    angle_text          TEXT NOT NULL,
    selected            INTEGER NOT NULL DEFAULT 0,
    created_by          TEXT NOT NULL CHECK (created_by IN ('human', 'claude_code')),
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_signal ON analyses(signal_id);

CREATE TABLE IF NOT EXISTS content_drafts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    analysis_id         INTEGER REFERENCES analyses(id),
    channel             TEXT NOT NULL CHECK (channel IN ('linkedin', 'instagram')),
    version             INTEGER NOT NULL DEFAULT 1,
    headline            TEXT,
    body                TEXT,
    format              TEXT,
    visual_brief        TEXT,
    exposure_line       TEXT,
    source_reference    TEXT,
    suggested_first_comment TEXT,
    disclosure_required INTEGER NOT NULL DEFAULT 0,
    disclosure_line     TEXT,
    risk_tier           TEXT CHECK (risk_tier IN ('green', 'amber', 'red')),
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'edited', 'approved', 'rejected')),
    created_at          TEXT NOT NULL,
    edited_by           TEXT,
    edit_reason_codes   TEXT
);
CREATE INDEX IF NOT EXISTS idx_content_drafts_signal ON content_drafts(signal_id);

CREATE TABLE IF NOT EXISTS published_content (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            INTEGER NOT NULL REFERENCES content_drafts(id),
    channel             TEXT NOT NULL,
    published_at        TEXT,
    url                 TEXT,
    final_text          TEXT,
    status              TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'published', 'corrected', 'withdrawn')),
    commercial_line     TEXT NOT NULL DEFAULT 'none' CHECK (commercial_line IN ('none', 'leap', 'advisory', 'incentive'))
);

CREATE TABLE IF NOT EXISTS competitor_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account             TEXT,
    archetype           TEXT,
    posted_at           TEXT,
    topic               TEXT,
    format              TEXT,
    hook_type           TEXT,
    reactions           INTEGER DEFAULT 0,
    comments            INTEGER DEFAULT 0,
    reposts             INTEGER DEFAULT 0,
    related_signal_id   TEXT REFERENCES signals(id),
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS calls_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT REFERENCES signals(id),
    post_id             INTEGER REFERENCES published_content(id),
    call_text           TEXT NOT NULL,
    reasoning           TEXT,
    source              TEXT,
    confidence_word     TEXT CHECK (confidence_word IN ('likely', 'possible')),
    made_on             TEXT NOT NULL,
    review_on           TEXT NOT NULL,
    expected_outcome    TEXT,
    actual_outcome      TEXT,
    grade               TEXT CHECK (grade IN ('right', 'partly_right', 'wrong', 'too_early_unresolved')),
    graded_on           TEXT,
    graded_by           TEXT,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_ledger_review_on ON calls_ledger(review_on);

CREATE TABLE IF NOT EXISTS approvals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('signal', 'draft')),
    entity_id           TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT NOT NULL,
    reviewer            TEXT,
    decision            TEXT,
    reason_code         TEXT,
    decided_at          TEXT NOT NULL,
    time_to_decide_min  REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_entity ON approvals(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS performance (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    published_content_id     INTEGER NOT NULL REFERENCES published_content(id),
    captured_at              TEXT NOT NULL,
    impressions              INTEGER DEFAULT 0,
    reactions                INTEGER DEFAULT 0,
    comments                 INTEGER DEFAULT 0,
    reposts                  INTEGER DEFAULT 0,
    saves                    INTEGER DEFAULT 0,
    sends                    INTEGER DEFAULT 0,
    profile_visits           INTEGER DEFAULT 0,
    link_clicks              INTEGER DEFAULT 0,
    follows                  INTEGER DEFAULT 0,
    qualified_comments       INTEGER DEFAULT 0,
    qualified_engagers_sampled INTEGER DEFAULT 0,
    qualified_share          REAL
);

CREATE TABLE IF NOT EXISTS watchlist (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    added_on            TEXT NOT NULL,
    watch_trigger       TEXT,
    revisit_on          TEXT,
    resolved            INTEGER NOT NULL DEFAULT 0,
    resolved_on         TEXT,
    resolution_note     TEXT
);
CREATE INDEX IF NOT EXISTS idx_watchlist_resolved ON watchlist(resolved);

CREATE TABLE IF NOT EXISTS learnings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    period              TEXT,
    finding             TEXT,
    evidence_json       TEXT,
    action              TEXT,
    weight_change_json  TEXT,
    accepted            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS system_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type            TEXT NOT NULL CHECK (run_type IN ('overnight_collect', 'morning_pipeline', 'evening_sweep', 'manual')),
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    sources_checked     INTEGER NOT NULL DEFAULT 0,
    items_collected     INTEGER NOT NULL DEFAULT 0,
    items_discarded     INTEGER NOT NULL DEFAULT 0,
    duplicates_found    INTEGER NOT NULL DEFAULT 0,
    high_score_signals  INTEGER NOT NULL DEFAULT 0,
    verification_failures INTEGER NOT NULL DEFAULT 0,
    errors_json         TEXT,
    model_calls         INTEGER NOT NULL DEFAULT 0,
    desk_sheet_id       TEXT,
    status              TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS source_failures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           TEXT NOT NULL REFERENCES sources(id),
    run_id              INTEGER REFERENCES system_runs(id),
    occurred_at         TEXT NOT NULL,
    error_text          TEXT NOT NULL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    resolved            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_source_failures_source ON source_failures(source_id);

-- Simple named counters, used to mint human-readable sequential IDs
-- (e.g. signals.id = 'SIG-2026-0001') without relying on SQLite-specific tricks.
CREATE TABLE IF NOT EXISTS id_sequences (
    name                TEXT PRIMARY KEY,
    value                INTEGER NOT NULL DEFAULT 0
);
