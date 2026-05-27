"""Consumer-facing wrappers for legacy omnibus CSV operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import create_db, get_section_formats, populate_db
from ..migrate import save_db_file
from .parser import parse_omnibus
from .reconstruct import reconstruct_omnibus
from .validate import validate_omnibus


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

    Raises:
        ValueError: If *conn* contains zero or multiple processing runs.
    """
    # Confirm exactly one processing run before reconstructing
    run_idxs = [row[0] for row in conn.execute("SELECT run_idx FROM processing_run")]
    if len(run_idxs) != 1:
        raise ValueError(
            f"Expected exactly one processing run, found {len(run_idxs)}"
        )

    csv_text = reconstruct_omnibus(conn, run_idxs[0])

    # Write reconstructed text to the requested path
    Path(csv_path).write_text(csv_text)


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
