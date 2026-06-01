-- Patch 001: rename project.qiita_id to project.external_project_id.
--
-- SQLite >= 3.25 auto-rewrites the column reference in every dependent
-- view body, so no need to do that here.

ALTER TABLE project RENAME COLUMN qiita_id TO external_project_id;
