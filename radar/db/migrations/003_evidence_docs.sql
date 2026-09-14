-- Preserved copies of primary documents (Section 26: "fetch the underlying
-- primary source whenever possible; preserve source evidence").
CREATE TABLE IF NOT EXISTS evidence_docs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL REFERENCES signals(id),
    url             TEXT NOT NULL,
    canonical_url   TEXT NOT NULL,
    fetched_url     TEXT,
    fetched_at      TEXT NOT NULL,
    path            TEXT,
    sha256          TEXT,
    chars           INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('stored', 'not_machine_readable', 'failed')),
    note            TEXT,
    UNIQUE (signal_id, canonical_url)
);
