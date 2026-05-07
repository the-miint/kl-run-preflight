-- Patch 001: Add post-fill update support.
--
-- Adds the biosample_accession column to input_sample and the
-- change_log audit table.  These support the post-fill update
-- operations defined in updates.py: setting NCBI BioSample accessions
-- and bulk lane reassignments.
--
-- The migration runner sets PRAGMA user_version after this script
-- runs — do not set user_version here.

ALTER TABLE input_sample ADD COLUMN biosample_accession TEXT;

CREATE TABLE change_log (
    change_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at      TEXT NOT NULL DEFAULT (datetime('now')),
    table_name      TEXT NOT NULL,
    row_id          INTEGER NOT NULL,
    column_name     TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT
);
