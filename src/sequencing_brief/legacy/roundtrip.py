"""Round-trip an omnibus CSV through SQLite and back.

Parses a legacy omnibus CSV into a SQLite database, writes it to disk,
reopens from the file, reconstructs the CSV, and returns both the
normalized original and the reconstructed text for comparison.
"""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from ..constants import FORMAT_TABULAR
from ..db import create_db, get_section_formats, populate_db
from .parser import (
    extract_section_name,
    is_section_header,
    parse_omnibus,
    parse_omnibus_text,
    strip_entries,
)
from .reconstruct import reconstruct_omnibus

# Set to a directory path to write out the SQLite DB and reconstructed CSV
# for each round-trip (e.g. "/tmp/roundtrip_debug").  Leave empty to disable.
DEBUG_OUTPUT_DIR = ""


def _id_reorder_sections(
    orig_sections: dict[str, list[dict[str, str]]],
    ref_sections: dict[str, list[dict[str, str]]],
    section_formats: dict[str, str],
) -> dict[str, list[str]]:
    # Identify sections that need column reordering
    reorders: dict[str, list[str]] = {}
    for name, fmt in section_formats.items():
        if fmt != FORMAT_TABULAR:
            continue
        if name not in orig_sections or name not in ref_sections:
            continue
        orig_data = orig_sections[name]
        ref_data = ref_sections[name]
        if not orig_data or not ref_data:
            continue
        orig_cols = list(orig_data[0].keys())
        ref_cols = list(ref_data[0].keys())
        if set(orig_cols) == set(ref_cols) and orig_cols != ref_cols:
            reorders[name] = ref_cols

    return reorders


def _reorder_columns(text: str, reference: str, section_formats: dict[str, str]) -> str:
    """Reorder columns in tabular sections of text to match reference order.

    Parses both texts to identify column order differences in tabular
    sections, then rewrites the original text with columns reordered to
    match the reference.
    """
    orig_sections = parse_omnibus_text(text, section_formats)
    ref_sections = parse_omnibus_text(reference, section_formats)

    reorders = _id_reorder_sections(orig_sections, ref_sections, section_formats)
    if not reorders:
        return text

    # Process the CSV line by line, reordering columns where needed
    reader = csv.reader(io.StringIO(text))
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    current_section: str | None = None
    col_map: dict[str, int] | None = None
    expect_header = False

    def reorder_and_pad(reorders, current_section, col_map, row):
        # Reorder actual cols then pad back to orig row width
        ref_cols = reorders[current_section]
        reordered = [row[col_map[c]] for c in ref_cols]
        while len(reordered) < len(row):
            reordered.append("")
        return reordered

    for row in reader:
        first = row[0].strip() if row else ""

        # Handle section boundary (title)
        if is_section_header(first):
            col_map = None
            current_section = extract_section_name(first)
            expect_header = current_section in reorders
            writer.writerow(row)
            continue

        # Handle header row of a section that needs reordering
        if expect_header and current_section in reorders:
            # Remove trailing empty cells to find actual column names,
            # Build column index map from them
            stripped = strip_entries(row)
            while stripped and stripped[-1] == "":
                stripped.pop()
            col_map = {c: i for i, c in enumerate(stripped)}

            # Reorder actual cols then pad back to orig row width
            reordered = reorder_and_pad(reorders, current_section, col_map, row)
            writer.writerow(reordered)
            expect_header = False
            continue

        # Handle data row in a reordered section
        if col_map is not None and current_section in reorders:
            stripped = strip_entries(row)

            # Handle blank row that ends the section
            if all(c == "" for c in stripped):
                col_map = None
                current_section = None
                writer.writerow(row)
                continue

            reordered = reorder_and_pad(reorders, current_section, col_map, row)
            writer.writerow(reordered)
            continue

        writer.writerow(row)

    return output.getvalue()


def _normalize_csv(text: str, reference: str, section_formats: dict[str, str]) -> str:
    """Normalize input CSV text to match reconstruction output.

    Applies three normalizations:

      - Boolean case: FALSE → False, TRUE → True
      - Whole-number floats: e.g. 1.0 → 1, 110.0 → 110
      - Column reordering: tabular sections reordered to match reference
    """
    # Normalize boolean case
    text = text.replace("FALSE", "False").replace("TRUE", "True")

    # Strip trailing .0 from whole-number floats. The pattern matches one
    # or more digits followed by literal ".0" where the "0" is NOT followed
    # by another digit. This converts "1.0" → "1" and "110.0" → "110"
    # while leaving "1.01", "0.2", and "1.00" unchanged.
    text = re.sub(r"(\d+)\.0(?!\d)", r"\1", text)

    # Reorder columns in tabular sections to match reconstruction order
    text = _reorder_columns(text, reference, section_formats)

    # Ensure trailing newline (some legacy files omit it)
    if not text.endswith("\n"):
        text += "\n"

    return text


def roundtrip(csv_path: str, test_name: str) -> tuple[str, str]:
    """Run the full round-trip and return (normalized_original, reconstructed).

    Creates the DB first to obtain section formats, then parses, populates,
    writes to disk, reopens from the file, and reconstructs.
    """
    if DEBUG_OUTPUT_DIR:
        out_dir = Path(DEBUG_OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(out_dir / f"{test_name}.db")
    else:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    Path(db_path).unlink(missing_ok=True)
    try:
        # Create DB first to get section format definitions
        conn = create_db(db_path)
        section_formats = get_section_formats(conn)

        # Parse with section formats from DB
        sections = parse_omnibus(csv_path, section_formats)

        # Populate and close to flush to disk
        populate_db(conn, sections)
        conn.close()

        # Reopen from file to reconstruct (mirrors real usage)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        section_formats = get_section_formats(conn)
        reconstructed = reconstruct_omnibus(conn, 1)
        conn.close()
    finally:
        if not DEBUG_OUTPUT_DIR:
            Path(db_path).unlink(missing_ok=True)

    if DEBUG_OUTPUT_DIR:
        out_dir = Path(DEBUG_OUTPUT_DIR)
        (out_dir / f"{test_name}.csv").write_text(reconstructed)

    original_text = Path(csv_path).read_text()
    normalized = _normalize_csv(original_text, reconstructed, section_formats)
    return normalized, reconstructed
