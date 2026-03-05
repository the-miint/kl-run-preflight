#!/usr/bin/env python3
"""Round-trip test:  Omnibus CSV → SQLite → Omnibus CSV → diff.

Usage:
    python -m samplesheet_db.cli <omnibus_csv>
    python -m samplesheet_db.cli                # defaults to PacBio test file

The script:
  (a) Parses the omnibus CSV into structured sections.
  (b) Creates a fresh SQLite database with the full schema.
  (c) Validates the file structure against the view registry.
  (d) Populates the database from the parsed data.
  (e) Reconstructs a new omnibus CSV from the database views.
"""

import sys
from pathlib import Path

from .parser import parse_omnibus
from .db import create_db, populate_db
from .validate import validate_omnibus
from .reconstruct import reconstruct_omnibus

# Default paths — work for the typical dev/test environment.
DEFAULT_INPUT = "/mnt/user-data/uploads/good_pacbio_absquantv11.csv"
DB_PATH = "/mnt/user-data/outputs/sequencing_run.sqlite"
RECON_PATH = "/mnt/user-data/outputs/reconstructed_omnibus.csv"


def main():
    """Run the full round-trip test: parse, validate, populate, and reconstruct.

    Reads an omnibus CSV (from sys.argv[1] or a default path), creates a
    fresh SQLite database, validates the file structure, populates the
    database, and reconstructs a new CSV from the DB views.
    """
    omnibus_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    print(f"Input:  {omnibus_path}")
    print(f"DB:     {DB_PATH}")
    print(f"Output: {RECON_PATH}\n")

    # -- (a) Parse ----------------------------------------------------------
    print("(a) Parsing omnibus file...")
    sections = parse_omnibus(omnibus_path)
    for name, content in sections.items():
        if isinstance(content, dict):
            print(f"    [{name}]: {len(content)} key-value pairs")
        elif isinstance(content, list) and content and isinstance(content[0], dict):
            print(f"    [{name}]: {len(content)} rows")
        else:
            print(f"    [{name}]: {content}")

    # -- (b) Create database ------------------------------------------------
    print("\n(b) Creating database...")
    Path(DB_PATH).unlink(missing_ok=True)
    conn = create_db(DB_PATH)

    # -- (c) Validate -------------------------------------------------------
    print("    Validating file structure...")
    errors = validate_omnibus(conn, sections)
    if errors:
        print("    Validation failed:")
        for e in errors:
            print(f"      - {e}")
        conn.close()
        sys.exit(1)
    print("    File structure valid\n")

    # -- (d) Populate -------------------------------------------------------
    print("(c) Populating database...")
    populate_db(conn, sections)
    print(f"    Saved to {DB_PATH}")

    # Print table row counts for verification.
    cur = conn.cursor()
    for table in [
        "assay_type", "sequencing_platform", "project", "input_plate",
        "sample_type", "input_sample", "sequencing_run", "compression_sample",
        "illumina_run", "illumina_sample", "pacbio_sample",
        "metagenomic_absquant_sample",
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        if count > 0:
            print(f"    {table}: {count} rows")

    # Show control → project associations.
    cur.execute("SELECT * FROM control_project_associations")
    controls = cur.fetchall()
    print(f"\n    Control associations: {len(controls)}")
    for c in controls:
        print(f"      {c}")

    # -- (e) Reconstruct ----------------------------------------------------
    print("\n(d) Reconstructing omnibus file from views...")
    reconstructed = reconstruct_omnibus(conn, run_id=1)
    with open(RECON_PATH, "w", newline="") as fh:
        fh.write(reconstructed)
    print(f"    Saved to {RECON_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
