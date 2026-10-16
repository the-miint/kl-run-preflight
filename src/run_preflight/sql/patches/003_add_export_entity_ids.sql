-- Add the Qiita-MIINT export entity ID to project and input_sample, and widen
-- each table's identity CHECK so the export ID (and, for input_sample, the
-- matrix tube ID) alone identifies a row. Every project identifier becomes
-- unique.
--
-- SQLite cannot alter a CHECK in place, so both tables are rebuilt. The
-- migration runner suspends foreign-key enforcement around each patch, which
-- lets these referenced tables be dropped. legacy_alter_table stops the rename
-- from re-validating the views that reference the dropped table, so the views
-- need not be dropped and recreated.

PRAGMA legacy_alter_table = ON;

CREATE TABLE project_new (
    project_idx                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name                    TEXT NOT NULL UNIQUE,
    external_project_id             TEXT UNIQUE,
    contact_email                   TEXT,
    human_filtering                 BOOLEAN NOT NULL DEFAULT 1,
    library_construction_protocol   TEXT NOT NULL,
    experiment_design_description   TEXT NOT NULL,
    bioproject_accession             TEXT UNIQUE,
    study_export_entity_id          TEXT UNIQUE,
    CHECK (external_project_id IS NOT NULL OR bioproject_accession IS NOT NULL
           OR study_export_entity_id IS NOT NULL)
);
INSERT INTO project_new (
    project_idx, project_name, external_project_id, contact_email,
    human_filtering, library_construction_protocol,
    experiment_design_description, bioproject_accession
)
SELECT
    project_idx, project_name, external_project_id, contact_email,
    human_filtering, library_construction_protocol,
    experiment_design_description, bioproject_accession
FROM project;
-- Carry over the AUTOINCREMENT high-water mark, which DROP TABLE discards
DELETE FROM sqlite_sequence WHERE name = 'project_new';
INSERT INTO sqlite_sequence (name, seq)
SELECT 'project_new', seq FROM sqlite_sequence WHERE name = 'project';
DROP TABLE project;
ALTER TABLE project_new RENAME TO project;

CREATE TABLE input_sample_new (
    input_sample_idx     INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_name         TEXT,
    input_plate_idx      INTEGER NOT NULL REFERENCES input_plate(input_plate_idx),
    well                TEXT,
    project_idx          INTEGER REFERENCES project(project_idx),
    sample_type_idx      INTEGER NOT NULL REFERENCES sample_type(sample_type_idx),
    biosample_accession TEXT,
    do_not_use          BOOLEAN NOT NULL DEFAULT 0,
    matrix_tube_id      TEXT,
    biosample_export_entity_id TEXT,
    CHECK (sample_name IS NOT NULL OR biosample_accession IS NOT NULL
           OR biosample_export_entity_id IS NOT NULL OR matrix_tube_id IS NOT NULL)
);
INSERT INTO input_sample_new (
    input_sample_idx, sample_name, input_plate_idx, well, project_idx,
    sample_type_idx, biosample_accession, do_not_use, matrix_tube_id
)
SELECT
    input_sample_idx, sample_name, input_plate_idx, well, project_idx,
    sample_type_idx, biosample_accession, do_not_use, matrix_tube_id
FROM input_sample;
-- Carry over the AUTOINCREMENT high-water mark, which DROP TABLE discards
DELETE FROM sqlite_sequence WHERE name = 'input_sample_new';
INSERT INTO sqlite_sequence (name, seq)
SELECT 'input_sample_new', seq FROM sqlite_sequence WHERE name = 'input_sample';
DROP TABLE input_sample;
ALTER TABLE input_sample_new RENAME TO input_sample;

PRAGMA legacy_alter_table = OFF;
