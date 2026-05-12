-- Patch 002: Add nullable run_name column to sequencing_run.
--
-- The migration runner sets PRAGMA user_version after this script
-- runs — do not set user_version here.

ALTER TABLE sequencing_run ADD COLUMN run_name TEXT;
