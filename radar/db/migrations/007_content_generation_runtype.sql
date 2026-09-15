-- Phase 5: Add 'content_generation' run type for daily content orchestrator.
-- In SQLite, we cannot directly alter CHECK constraints, so we recreate the table.
-- source_failures.run_id REFERENCES system_runs(id), and connection.py turns
-- foreign_keys ON for every connection; DROP TABLE performs an implicit
-- delete of all system_runs rows, which fails the FK check on any DB that
-- already has source_failures rows referencing it. Disable FK enforcement
-- for the duration of the rebuild, exactly as 004_cbic_api_method.sql does.
PRAGMA foreign_keys = OFF;

CREATE TABLE system_runs_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type            TEXT NOT NULL CHECK (run_type IN ('overnight_collect', 'morning_pipeline', 'evening_sweep', 'content_generation', 'manual')),
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

INSERT INTO system_runs_new SELECT * FROM system_runs;
DROP TABLE system_runs;
ALTER TABLE system_runs_new RENAME TO system_runs;

PRAGMA foreign_keys = ON;
