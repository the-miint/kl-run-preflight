"""Consumer-facing wrappers for legacy omnibus CSV operations."""

from __future__ import annotations

import csv
import io
import sqlite3
import warnings
from pathlib import Path

from ..constants import (
    COL_SAMPLE_NAME,
    DB_COL_ILLUMINA_SAMPLE_IDX,
    IN_MEMORY_PATH,
    SQLITE_MAGIC,
)
from ..db import (
    create_db,
    get_illumina_sample_rows,
    get_projects_missing_external_id,
    get_section_formats,
    get_single_run_idx,
    populate_db,
)
from ..file_io import atomic_write, load_db_bytes, save_db_file
from .parser import parse_omnibus_text, read_omnibus_text
from .reconstruct import reconstruct_omnibus
from .validate import validate_omnibus


def load_file(path: str, patches_dir: Path | None = None) -> sqlite3.Connection:
    """Load a run preflight from either a legacy omnibus CSV or a SQLite DB file.

    Detects the format from the file's first 16 bytes (SQLite magic
    header). Either branch returns a detached in-memory connection, so
    *path* is never written to and persisting any change requires an
    explicit save_db_file call. Caller owns and must close the
    returned connection.

    NB: a SQLite input is read as raw bytes, bypassing crash recovery, so
    a hot journal left by a crashed writer is ignored.

    Args:
        path: Filesystem path to the run preflight file.
        patches_dir: Directory to scan for patches.  Defaults to the
            built-in ``sql/patches/`` directory.  Has no effect on legacy
            CSV input, which is built at the latest version.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file is detected as legacy CSV but fails
            parsing or validation.
        sqlite3.DatabaseError: If the file carries the SQLite header but
            is truncated or otherwise unreadable.
        SchemaVersionTooNewError: If the file is a SQLite database whose
            schema version exceeds the shipped patch set.
    """
    # Confirm the file exists before any read attempt so the error is unambiguous
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {path}")

    # Sniff and read a native file through one handle, so the bytes acted
    # on are the ones the header check actually saw
    with p.open("rb") as fh:
        head = fh.read(len(SQLITE_MAGIC))
        if head == SQLITE_MAGIC:
            blob = head + fh.read()
            return load_db_bytes(blob, patches_dir)

    # A legacy CSV goes to the path-taking loader, which reads it as text
    # under the same rules every other omnibus read uses
    conn = load_legacy_csv(path)
    return conn


def open_file(path: str, patches_dir: Path | None = None) -> sqlite3.Connection:
    """Deprecated alias for load_file."""
    warnings.warn(
        "open_file is deprecated; use load_file instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    conn = load_file(path, patches_dir)
    return conn


def load_legacy_csv(csv_path: str) -> sqlite3.Connection:
    """Parse a legacy omnibus CSV file into a fresh in-memory SQLite connection.

    The returned connection is at the latest schema version with
    foreign-key enforcement enabled. Caller owns and must close it.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    text = read_omnibus_text(csv_path)
    conn = load_legacy_csv_text(text)
    return conn


def load_legacy_csv_text(text: str) -> sqlite3.Connection:
    """Parse legacy omnibus CSV text into a fresh in-memory SQLite connection.

    Takes content already decoded, so the caller owns any decision about
    how bytes became text. The returned connection is at the latest
    schema version with foreign-key enforcement enabled. Caller owns and
    must close it.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    # Build a fresh in-memory DB and tear it down on any downstream error
    conn = create_db(IN_MEMORY_PATH)
    try:
        # Pull section format definitions from the freshly-created DB
        section_formats = get_section_formats(conn)

        # Parse and validate against the registry before any writes
        sections = parse_omnibus_text(text, section_formats)
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
    atomic_write(csv_path, csv_text)


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
    atomic_write(csv_path, output.getvalue())


def migrate_legacy_csv_to_db_file(csv_path: str, db_path: str) -> None:
    """Load a legacy omnibus CSV and save it as a SQLite database file.

    *db_path* is written only once the whole load succeeds, so callers
    never see a partially-populated database and any file already at that
    path survives a failure unchanged.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    conn = load_legacy_csv(csv_path)
    try:
        save_db_file(conn, db_path)
    finally:
        conn.close()
