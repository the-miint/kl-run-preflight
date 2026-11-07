"""Patch 004: reject blank or padded identity values.

Fails, naming the offending values (the first 20, then a count), if any
identity value is empty or has surrounding whitespace or invisible format
characters. Otherwise rebuilds project and input_sample with a CHECK holding
each identity column to that rule. The character set is written out here,
not imported, so this patch behaves the same however the package's constants
later change.
"""

import sqlite3

# Code points stripped from a value's ends; NUL is excluded because SQLite's
# trim() cannot take it, and is checked separately through hex()
_TRIM_CODES = (
    9,
    10,
    11,
    12,
    13,
    28,
    29,
    30,
    31,
    32,
    133,
    160,
    5760,
    6158,
    8192,
    8193,
    8194,
    8195,
    8196,
    8197,
    8198,
    8199,
    8200,
    8201,
    8202,
    8203,
    8232,
    8233,
    8239,
    8287,
    8288,
    12288,
    65279,
)

# Most offending values the error lists before counting the rest
_MAX_LISTED_VALUES = 20

# Identity columns of each rebuilt table, with the table's primary key
_IDENTITY_COLUMNS = {  # same-pattern-ok: applied patches
    # are frozen, so this copy must not depend on code that can change
    ("project", "project_idx"): (
        "external_project_id",
        "bioproject_accession",
        "study_export_entity_id",
    ),
    ("input_sample", "input_sample_idx"): (
        "sample_name",
        "biosample_accession",
        "biosample_export_entity_id",
        "matrix_tube_id",
    ),
}

# Columns copied into each rebuilt table
_PROJECT_COLUMNS = (
    "project_idx, project_name, external_project_id, contact_email, "
    "human_filtering, library_construction_protocol, "
    "experiment_design_description, bioproject_accession, study_export_entity_id"
)
_INPUT_SAMPLE_COLUMNS = (
    "input_sample_idx, sample_name, input_plate_idx, well, project_idx, "
    "sample_type_idx, biosample_accession, do_not_use, matrix_tube_id, "
    "biosample_export_entity_id"
)


def _clean_value_sql(column: str) -> str:
    """Return the SQL condition that non-NULL *column* is clean."""
    trim_set = "char(" + ", ".join(str(code) for code in _TRIM_CODES) + ")"
    # hex(char(0)) is NUL's hex form in the DB's text encoding, so the start
    # and end tests each match one whole code unit
    clean_value = (
        f"{column} = trim({column}, {trim_set}) AND {column} <> '' "
        f"AND hex({column}) NOT LIKE hex(char(0)) || '%' "
        f"AND hex({column}) NOT LIKE '%' || hex(char(0))"
    )
    return clean_value


def _identity_checks_sql(table: str) -> str:
    """Return the CHECK clauses for *table*'s identity columns."""
    columns = next(
        cols for (name, _), cols in _IDENTITY_COLUMNS.items() if name == table
    )
    checks = ", ".join(
        f"CHECK ({column} IS NULL OR ({_clean_value_sql(column)}))"
        for column in columns
    )
    return checks


def _raise_if_unclean(conn: sqlite3.Connection) -> None:
    """Raise ValueError naming identity values the new CHECKs would reject.

    The message lists at most _MAX_LISTED_VALUES of them and counts the rest.
    """
    offending = []
    for (table, pk_col), columns in _IDENTITY_COLUMNS.items():
        for column in columns:
            cur = conn.execute(
                f"SELECT {pk_col}, {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND NOT ({_clean_value_sql(column)}) "
                f"ORDER BY {pk_col}"
            )
            rows = cur.fetchall()
            offending.extend(
                f"{table}.{column} row {row_idx}: {value!r}" for row_idx, value in rows
            )
    if offending:
        # List the first offending values, then count any beyond the cap
        listed = "; ".join(offending[:_MAX_LISTED_VALUES])
        unlisted_count = len(offending) - _MAX_LISTED_VALUES
        more = f"; and {unlisted_count} more" if unlisted_count > 0 else ""
        raise ValueError(
            "Identity values must not be empty or have surrounding whitespace "
            "or invisible format characters; fix these before upgrading: "
            + listed
            + more
        )


def _rebuild_statements() -> list[str]:
    """Return the statements rebuilding project and input_sample with the CHECKs."""
    project_ddl = (
        "CREATE TABLE project_new ("
        "project_idx INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_name TEXT NOT NULL UNIQUE, "
        "external_project_id TEXT UNIQUE, "
        "contact_email TEXT, "
        "human_filtering BOOLEAN NOT NULL DEFAULT 1, "
        "library_construction_protocol TEXT NOT NULL, "
        "experiment_design_description TEXT NOT NULL, "
        "bioproject_accession TEXT UNIQUE, "
        "study_export_entity_id TEXT UNIQUE, "
        "CHECK (external_project_id IS NOT NULL OR bioproject_accession IS NOT NULL "
        "OR study_export_entity_id IS NOT NULL), "
        + _identity_checks_sql("project")
        + ")"
    )
    input_sample_ddl = (
        "CREATE TABLE input_sample_new ("
        "input_sample_idx INTEGER PRIMARY KEY AUTOINCREMENT, "
        "sample_name TEXT, "
        "input_plate_idx INTEGER NOT NULL REFERENCES input_plate(input_plate_idx), "
        "well TEXT, "
        "project_idx INTEGER REFERENCES project(project_idx), "
        "sample_type_idx INTEGER NOT NULL REFERENCES sample_type(sample_type_idx), "
        "biosample_accession TEXT, "
        "do_not_use BOOLEAN NOT NULL DEFAULT 0, "
        "matrix_tube_id TEXT, "
        "biosample_export_entity_id TEXT, "
        "CHECK (sample_name IS NOT NULL OR biosample_accession IS NOT NULL "
        "OR biosample_export_entity_id IS NOT NULL OR matrix_tube_id IS NOT NULL), "
        + _identity_checks_sql("input_sample")
        + ")"
    )
    statements = []
    for table, ddl, columns in (
        ("project", project_ddl, _PROJECT_COLUMNS),
        ("input_sample", input_sample_ddl, _INPUT_SAMPLE_COLUMNS),
    ):
        # Copy rows and the AUTOINCREMENT high-water mark, then swap tables
        statements += [
            ddl,
            f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}",
            f"DELETE FROM sqlite_sequence WHERE name = '{table}_new'",
            f"INSERT INTO sqlite_sequence (name, seq) "
            f"SELECT '{table}_new', seq FROM sqlite_sequence WHERE name = '{table}'",
            f"DROP TABLE {table}",
            f"ALTER TABLE {table}_new RENAME TO {table}",
        ]
    return statements


def apply(conn: sqlite3.Connection) -> None:
    """Check identity values, then rebuild project and input_sample."""
    _raise_if_unclean(conn)

    # legacy_alter_table keeps the rename from re-validating the views that
    # reference the dropped tables
    conn.execute("PRAGMA legacy_alter_table = ON")
    for statement in _rebuild_statements():
        conn.execute(statement)
    conn.execute("PRAGMA legacy_alter_table = OFF")
