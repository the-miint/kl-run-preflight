"""Consumer-facing wrappers for legacy omnibus CSV ↔ SQLite operations.

These are the single-call entry points downstream code should use. Internally
they orchestrate the parse → validate → populate and open → reconstruct →
write pipelines so callers do not need to assemble the steps themselves.
"""

from __future__ import annotations

from pathlib import Path

from ..db import create_db, get_section_formats, populate_db
from ..migrate import open_db
from .parser import parse_omnibus
from .reconstruct import reconstruct_omnibus
from .validate import validate_omnibus


def load_legacy_csv(csv_path: str, db_path: str) -> None:
    """Load a legacy omnibus CSV into a new SQLite database.

    Creates a fresh DB at *db_path*, parses *csv_path*, validates it
    against the format registry, populates the DB, and closes. The DB
    file is removed if any step fails so callers never see a
    partially-populated database.

    Args:
        csv_path: Path to the legacy omnibus CSV file.
        db_path: Path at which the new SQLite database will be created.

    Raises:
        ValueError: If the CSV fails validation against the format registry.
    """
    # Create a fresh DB and track success so the file can be cleaned up
    # if any downstream step raises
    conn = create_db(db_path)
    success = False
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
        success = True
    finally:
        conn.close()
        if not success:
            Path(db_path).unlink(missing_ok=True)


def write_legacy_csv(db_path: str, csv_path: str) -> None:
    """Write a SQLite database out as a legacy omnibus CSV.

    Opens the DB at *db_path* (applying any pending schema patches),
    locates the single processing run, reconstructs the omnibus CSV
    text, and writes it to *csv_path*.

    Args:
        db_path: Path to the existing SQLite database.
        csv_path: Path at which the omnibus CSV will be written.

    Raises:
        ValueError: If the database contains zero or multiple processing
            runs (legacy omnibus files describe exactly one run).
    """
    # Open with patching so callers can write from any compatible DB version
    conn = open_db(db_path)
    try:
        # Confirm exactly one processing run before reconstructing
        run_idxs = [
            row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
        ]
        if len(run_idxs) != 1:
            raise ValueError(
                f"Expected exactly one processing run, found {len(run_idxs)}"
            )

        csv_text = reconstruct_omnibus(conn, run_idxs[0])
    finally:
        conn.close()

    # Write reconstructed text to the requested path
    Path(csv_path).write_text(csv_text)
