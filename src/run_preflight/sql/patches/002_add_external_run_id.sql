-- Patch 002: Add nullable external_run_id column to processing_run.
--
-- The migration runner sets PRAGMA user_version after this script
-- runs — do not set user_version here.

ALTER TABLE processing_run ADD COLUMN external_run_id TEXT;
