-- Phase 3: platform-agnostic publishing queue. One row per (approved draft,
-- platform, target account) that is meant to go out. Rows are never deleted;
-- terminal states (published / cancelled / superseded) are kept as history.
-- All queue timestamps are UTC ISO 8601 ('YYYY-MM-DDTHH:MM:SS+00:00') so that
-- string comparison in SQL is chronological.
CREATE TABLE IF NOT EXISTS publish_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            INTEGER NOT NULL REFERENCES content_drafts(id),
    caption_draft_id    INTEGER REFERENCES content_drafts(id),
    signal_id           TEXT NOT NULL REFERENCES signals(id),
    asset_slot          TEXT NOT NULL,
    platform            TEXT NOT NULL CHECK (platform IN ('linkedin', 'instagram')),
    post_format         TEXT NOT NULL,
    target_account      TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN (
        'awaiting_media', 'queued', 'scheduled', 'publishing', 'published',
        'retry_pending', 'needs_reconciliation', 'held', 'failed', 'cancelled', 'superseded'
    )),
    publish_text        TEXT NOT NULL,
    first_comment       TEXT,
    first_comment_state TEXT NOT NULL DEFAULT 'not_required'
                        CHECK (first_comment_state IN ('not_required', 'pending', 'posted', 'failed')),
    text_fingerprint    TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL,
    risk_tier           TEXT NOT NULL CHECK (risk_tier IN ('green', 'amber', 'red')),
    commercial_line     TEXT NOT NULL DEFAULT 'none' CHECK (commercial_line IN ('none', 'leap', 'advisory', 'incentive')),
    scheduled_at        TEXT,
    scheduled_by        TEXT,
    next_attempt_at     TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL,
    lease_expires_at    TEXT,
    external_post_id    TEXT,
    external_url        TEXT,
    published_at        TEXT,
    published_content_id INTEGER REFERENCES published_content(id),
    last_error_code     TEXT,
    last_error_message  TEXT,
    enqueued_by         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_queue_state ON publish_queue(state);
CREATE INDEX IF NOT EXISTS idx_publish_queue_due ON publish_queue(state, scheduled_at, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_publish_queue_draft ON publish_queue(draft_id);
CREATE INDEX IF NOT EXISTS idx_publish_queue_signal ON publish_queue(signal_id, platform, target_account);

-- Idempotent enqueue: the same draft + caption + platform + account + text can
-- exist only once among items that are live or already published. Cancelled,
-- superseded and failed items drop out so a fixed item can be enqueued again.
CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_queue_idempotency
    ON publish_queue(idempotency_key)
    WHERE state NOT IN ('cancelled', 'superseded', 'failed');

CREATE TABLE IF NOT EXISTS queue_media (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id       INTEGER NOT NULL REFERENCES publish_queue(id),
    media_kind          TEXT NOT NULL CHECK (media_kind IN ('image', 'video', 'document', 'cover_image')),
    position            INTEGER NOT NULL DEFAULT 1,
    local_path          TEXT,
    public_url          TEXT,
    mime_type           TEXT NOT NULL,
    byte_size           INTEGER,
    sha256              TEXT,
    width               INTEGER,
    height              INTEGER,
    duration_seconds    REAL,
    page_count          INTEGER,
    alt_text            TEXT,
    title               TEXT,
    attached_by         TEXT NOT NULL,
    attached_at         TEXT NOT NULL,
    UNIQUE (queue_item_id, media_kind, position)
);

-- One row per dispatch attempt. Live attempts are numbered and unique per
-- item, and the row is committed *before* the platform call, so a crash
-- mid-call leaves durable evidence that a post may have gone out.
CREATE TABLE IF NOT EXISTS publish_attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id       INTEGER NOT NULL REFERENCES publish_queue(id),
    attempt_number      INTEGER,
    mode                TEXT NOT NULL CHECK (mode IN ('live', 'dry_run')),
    publisher           TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    outcome             TEXT CHECK (outcome IN (
        'published', 'retryable_error', 'permanent_error', 'unknown', 'dry_run', 'preflight_blocked'
    )),
    external_post_id    TEXT,
    external_url        TEXT,
    error_code          TEXT,
    error_message       TEXT,
    request_json        TEXT,
    response_json       TEXT,
    UNIQUE (queue_item_id, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_publish_attempts_item ON publish_attempts(queue_item_id);

-- Audit trail: every queue state change (and every override) with who, why, when.
CREATE TABLE IF NOT EXISTS queue_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id       INTEGER NOT NULL REFERENCES publish_queue(id),
    from_state          TEXT,
    to_state            TEXT NOT NULL,
    actor               TEXT NOT NULL,
    reason              TEXT,
    detail_json         TEXT,
    occurred_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_events_item ON queue_events(queue_item_id);

-- published_content stays the single record of what went out (performance
-- import, repeat detection and the Desk Sheet all read it); it now also
-- carries the platform's own post ID and the queue item it came from.
ALTER TABLE published_content ADD COLUMN platform_post_id TEXT;
ALTER TABLE published_content ADD COLUMN queue_item_id INTEGER REFERENCES publish_queue(id);
