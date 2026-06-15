"""Consumer-facing wrappers for legacy omnibus CSV operations."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from ..constants import COL_SAMPLE_NAME, DB_COL_ILLUMINA_SAMPLE_IDX
from ..db import (
    create_db,
    get_illumina_sample_rows,
    get_projects_missing_external_id,
    get_section_formats,
    get_single_run_idx,
    populate_db,
)
from ..file_io import open_db_file, save_db_file
from .parser import parse_omnibus
from .reconstruct import reconstruct_omnibus
from .validate import validate_omnibus

# SQLite database files begin with this 16-byte magic header (see https://sqlite.org/fileformat.html)
_SQLITE_MAGIC = b"SQLite format 3\x00"


def open_file(path: str) -> sqlite3.Connection:
    """Open a run preflight from either a legacy omnibus CSV or a SQLite DB file.

    Detects the format from the file's first 16 bytes (SQLite magic
    header). Caller owns and must close the returned connection.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file is detected as legacy CSV but fails
            parsing or validation.
    """
    # Confirm the file exists before any read attempt so the error is unambiguous
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {path}")

    # Read just enough bytes to identify the SQLite magic header
    with p.open("rb") as fh:
        head = fh.read(len(_SQLITE_MAGIC))

    # Dispatch on detected format
    if head == _SQLITE_MAGIC:
        return open_db_file(path)
    return load_legacy_csv(path)


def load_legacy_csv(csv_path: str) -> sqlite3.Connection:
    """Parse a legacy omnibus CSV into a fresh in-memory SQLite connection.

    The returned connection is at the latest schema version with
    foreign-key enforcement enabled. Caller owns and must close it.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    # Build a fresh in-memory DB and tear it down on any downstream error
    conn = create_db(":memory:")
    try:
        # Pull section format definitions from the freshly-created DB
        section_formats = get_section_formats(conn)

        # Parse and validate against the registry before any writes
        sections = parse_omnibus(csv_path, section_formats)
        errors = validate_omnibus(conn, sections)
        if errors:
            raise ValueError("Validation errors:\n  " + "\n  ".join(errors))

        # populate_db commits internally; no explicit commit needed here
        populate_db(conn, sections)
    except Exception:
        conn.close()
        raise
    return conn


def save_legacy_csv(conn: sqlite3.Connection, csv_path: str) -> None:
    """Write a live SQLite connection out as a legacy omnibus CSV.

    *conn* must describe exactly one processing run (legacy omnibus
    files describe exactly one run). Caller retains ownership of *conn*.
    Samples flagged do_not_use are included in the output unchanged; the
    flag has no effect on the written CSV.

    Raises:
        ValueError: If *conn* contains zero or multiple processing runs,
            or if any project reachable from the run has NULL
            external_project_id (legacy CSVs require a QiitaID column
            value for every project, so such a DB cannot be losslessly
            reconstructed).
    """
    # Confirm exactly one processing run before reconstructing
    run_idx = get_single_run_idx(conn)

    # Legacy CSV's QiitaID column has no NULL representation; a NULL
    # external_project_id would silently round-trip as a blank cell.
    missing = get_projects_missing_external_id(conn, run_idx)
    if missing:
        raise ValueError(
            "Cannot reconstruct legacy CSV: project(s) "
            f"{missing} have NULL external_project_id "
            "(legacy CSVs require a QiitaID for every project)"
        )

    csv_text = reconstruct_omnibus(conn, run_idx)

    # Write reconstructed text to the requested path
    Path(csv_path).write_text(csv_text)


def save_legacy_sample_id_map_csv(
    conn: sqlite3.Connection, csv_path: str, *, include_do_not_use: bool = False
) -> None:
    """Write a CSV mapping illumina_sample_idx to legacy Sample_Name.

    *conn* must describe exactly one processing run with at least one
    illumina_sample row. Sample_Name follows the legacy CSV rule:
    prepped_sample.sample_name when populated (replicates), else
    input_sample.sample_name. Rows are ordered by illumina_sample_idx.

    Samples flagged do_not_use are excluded unless *include_do_not_use*
    is True.

    Raises:
        ValueError: If *conn* lacks exactly one processing run, or has
            no illumina_sample rows.
    """
    # Pull (illumina_sample_idx, sample_name) pairs from the run;
    # do_not_use-flagged samples are omitted unless the caller opts in.
    rows = [
        (r[0], r[5])
        for r in get_illumina_sample_rows(conn, include_do_not_use=include_do_not_use)
    ]
    if not rows:
        raise ValueError("run has no illumina_sample rows; cannot write sample id map")

    # Format the CSV text in a DB-free path, then write it out
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([DB_COL_ILLUMINA_SAMPLE_IDX, COL_SAMPLE_NAME])
    writer.writerows(rows)
    Path(csv_path).write_text(output.getvalue())


def migrate_legacy_csv_to_db_file(csv_path: str, db_path: str) -> None:
    """Load a legacy omnibus CSV and save it as a SQLite database file.

    The file at *db_path* is removed if any step fails so callers
    never see a partially-populated database.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    # Track success so the file can be cleaned up if any step raises
    success = False
    try:
        conn = load_legacy_csv(csv_path)
        try:
            save_db_file(conn, db_path)
            success = True
        finally:
            conn.close()
    finally:
        if not success:
            Path(db_path).unlink(missing_ok=True)
