"""Patch 003: add the Qiita-MIINT export entity ID to project and input_sample.

Widens each table's identity CHECK so the export ID (and, for input_sample,
the matrix tube ID) alone identifies a row, and makes every project
identifier unique. Fails, naming each duplicated value and its rows, if two
projects already share an identifier.

SQLite cannot alter a CHECK in place, so both tables are rebuilt; dropping
these referenced tables relies on foreign-key enforcement being off while
patches run.
"""

import sqlite3
from itertools import groupby
from operator import itemgetter

# Project identifiers that were not unique before this patch
_NEWLY_UNIQUE_PROJECT_COLUMNS = ("external_project_id", "bioproject_accession")

_REBUILD_STATEMENTS = (
    """
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
    )
    """,
    """
    INSERT INTO project_new (
        project_idx, project_name, external_project_id, contact_email,
        human_filtering, library_construction_protocol,
        experiment_design_description, bioproject_accession
    )
    SELECT
        project_idx, project_name, external_project_id, contact_email,
        human_filtering, library_construction_protocol,
        experiment_design_description, bioproject_accession
    FROM project
    """,
    # Carry over the AUTOINCREMENT high-water mark, which DROP TABLE discards
    "DELETE FROM sqlite_sequence WHERE name = 'project_new'",
    """
    INSERT INTO sqlite_sequence (name, seq)
    SELECT 'project_new', seq FROM sqlite_sequence WHERE name = 'project'
    """,
    "DROP TABLE project",
    "ALTER TABLE project_new RENAME TO project",
    """
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
    )
    """,
    """
    INSERT INTO input_sample_new (
        input_sample_idx, sample_name, input_plate_idx, well, project_idx,
        sample_type_idx, biosample_accession, do_not_use, matrix_tube_id
    )
    SELECT
        input_sample_idx, sample_name, input_plate_idx, well, project_idx,
        sample_type_idx, biosample_accession, do_not_use, matrix_tube_id
    FROM input_sample
    """,
    # Carry over the AUTOINCREMENT high-water mark, which DROP TABLE discards
    "DELETE FROM sqlite_sequence WHERE name = 'input_sample_new'",
    """
    INSERT INTO sqlite_sequence (name, seq)
    SELECT 'input_sample_new', seq FROM sqlite_sequence WHERE name = 'input_sample'
    """,
    "DROP TABLE input_sample",
    "ALTER TABLE input_sample_new RENAME TO input_sample",
)


def _raise_if_duplicate(conn: sqlite3.Connection) -> None:
    """Raise ValueError naming each project identifier value held by several rows."""
    duplicates = []
    for column in _NEWLY_UNIQUE_PROJECT_COLUMNS:
        # Fetch every row holding a shared value, adjacent by value for groupby
        cur = conn.execute(
            f"SELECT {column}, project_idx FROM project WHERE {column} IN "
            f"(SELECT {column} FROM project GROUP BY {column} HAVING count(*) > 1) "
            f"ORDER BY {column}, project_idx"
        )
        rows = cur.fetchall()
        for value, group_rows in groupby(rows, key=itemgetter(0)):
            row_idxs = ", ".join(str(row_idx) for _, row_idx in group_rows)
            duplicates.append(f"project.{column} {value!r} in rows {row_idxs}")
    if duplicates:
        raise ValueError(
            "Project identifiers must be unique; fix these before upgrading: "
            + "; ".join(duplicates)
        )


def apply(conn: sqlite3.Connection) -> None:
    """Check project identifiers are unique, then rebuild project and input_sample."""
    _raise_if_duplicate(conn)

    # legacy_alter_table keeps the rename from re-validating the views that
    # reference the dropped tables
    conn.execute("PRAGMA legacy_alter_table = ON")
    for statement in _REBUILD_STATEMENTS:
        conn.execute(statement)
    conn.execute("PRAGMA legacy_alter_table = OFF")
