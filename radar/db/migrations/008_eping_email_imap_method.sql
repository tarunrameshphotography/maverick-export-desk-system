-- wto_eping now collects via IMAP polling of a dedicated inbox
-- (radar/collectors/eping.py), not the placeholder 'email' method sources.yaml
-- used while it was registered-but-not-automated. SQLite can't ALTER a CHECK
-- constraint, so rebuild the table, exactly as 004_cbic_api_method.sql does.
PRAGMA foreign_keys = OFF;

CREATE TABLE sources_new (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    url                 TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('primary', 'secondary')),
    signal_type         TEXT NOT NULL CHECK (signal_type IN ('early', 'confirm', 'enforced', 'context')),
    cadence             TEXT,
    method              TEXT NOT NULL CHECK (method IN ('rss', 'api', 'pagewatch', 'cbic_api', 'email_imap', 'email', 'manual')),
    country             TEXT,
    category            TEXT,
    reliability_1to5    INTEGER NOT NULL CHECK (reliability_1to5 BETWEEN 1 AND 5),
    active              INTEGER NOT NULL DEFAULT 1,
    query               TEXT,
    notes               TEXT
);

INSERT INTO sources_new (id, name, url, kind, signal_type, cadence, method, country, category,
                          reliability_1to5, active, query, notes)
SELECT id, name, url, kind, signal_type, cadence, method, country, category,
       reliability_1to5, active, query, notes
FROM sources;

DROP TABLE sources;
ALTER TABLE sources_new RENAME TO sources;

PRAGMA foreign_keys = ON;
