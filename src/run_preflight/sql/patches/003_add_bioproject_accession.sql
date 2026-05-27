-- Patch 003: Add nullable bioproject_accession column to project.
--
-- The migration runner sets PRAGMA user_version after this script
-- runs — do not set user_version here.

ALTER TABLE project ADD COLUMN bioproject_accession TEXT;
