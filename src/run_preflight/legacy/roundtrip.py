"""Round-trip comparison utilities for tests and dev scripts.

Round-tripping a legacy sheet (load → write → byte-compare) is not a
production workflow; it exists only to verify that the SQLite representation
preserves the original. These helpers run a sheet through the public
load/write API and normalize the original to match the reconstructor's
formatting choices so that an exact text comparison is meaningful.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..constants import FORMAT_TABULAR
from ..db import (
    get_format_file_shape,
    get_run_legacy_format,
    get_section_formats,
    get_single_run_idx,
)
from ..file_io import load_db_file, save_db_file
from .api import load_legacy_csv, save_legacy_csv
from .parser import (
    extract_section_name,
    is_section_header,
    parse_omnibus_text,
    strip_entries,
)


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


def _reorder_row(ref_cols: list[str], col_map: dict[str, int], row: list[str]) -> list[str]:
    """Rewrite one row into *ref_cols* order, padding back to its own width."""
    reordered = [row[col_map[c]] for c in ref_cols]
    while len(reordered) < len(row):
        reordered.append("")
    return reordered


def _reorder_flat_table(text: str, reference: str, delimiter: str) -> str:
    """Reorder a section-less sheet's single table to the reference order.

    Returns *text* unchanged unless both sheets carry the same column set in
    a different order, which is the only case reordering can be meaningful.
    """
    rows = [line.split(delimiter) for line in text.split("\n") if line != ""]
    ref_rows = [line.split(delimiter) for line in reference.split("\n") if line != ""]
    if not rows or not ref_rows:
        return text

    orig_cols, ref_cols = rows[0], ref_rows[0]
    if set(orig_cols) != set(ref_cols) or orig_cols == ref_cols:
        return text

    col_map = {name: i for i, name in enumerate(orig_cols)}
    reordered = [_reorder_row(ref_cols, col_map, row) for row in rows]
    return "\n".join(delimiter.join(row) for row in reordered) + "\n"


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
            reordered = _reorder_row(reorders[current_section], col_map, row)
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

            reordered = _reorder_row(reorders[current_section], col_map, row)
            writer.writerow(reordered)
            continue

        writer.writerow(row)

    return output.getvalue()


_WHOLE_NUMBER_DECIMAL = re.compile(r"^(\d+)\.0$")


def _strip_whole_number_decimals(text: str, delimiter: str) -> str:
    """Drop the trailing ".0" from any cell that is entirely a whole number."""
    lines = []
    for line in text.split("\n"):
        cells = line.split(delimiter)
        lines.append(
            delimiter.join(_WHOLE_NUMBER_DECIMAL.sub(r"\1", cell) for cell in cells)
        )
    return "\n".join(lines)


def normalize_csv(
    text: str,
    reference: str,
    section_formats: dict[str, str],
    delimiter: str = ",",
    has_section_labels: bool = True,
) -> str:
    """Normalize an original sheet to match reconstruction output.

    Applies four normalizations so an original legacy sheet can be
    byte-compared against the reconstructor's output:

      - Boolean case: FALSE → False, TRUE → True
      - Whole-number floats: e.g. 1.0 → 1, 110.0 → 110
      - Column reordering: tabular sections reordered to match reference
      - Trailing newline: ensured present

    The numeric rule applies to a cell that is entirely such a number, not to
    any digits found in the text: sample names embed their own values, and
    "katharo.ADAPT.21.E11.18000.0" is a name rather than a measurement.
    """
    # Normalize boolean case
    text = text.replace("FALSE", "False").replace("TRUE", "True")

    text = _strip_whole_number_decimals(text, delimiter)

    # Reorder columns to match reconstruction order
    if has_section_labels:
        text = _reorder_columns(text, reference, section_formats)
    else:
        text = _reorder_flat_table(text, reference, delimiter)

    # Ensure trailing newline (some legacy files omit it)
    if not text.endswith("\n"):
        text += "\n"

    return text


def roundtrip_via_api(csv_path: Path, tmp_dir: Path) -> tuple[str, str]:
    """Round-trip a legacy CSV through the public load/write API.

    Loads *csv_path* into a fresh SQLite DB inside *tmp_dir*, writes
    that DB back out as a CSV, and normalizes the original so it can be
    byte-compared against the reconstructed output.

    Args:
        csv_path: The legacy CSV to round-trip.
        tmp_dir: Scratch directory for the intermediate DB and
            reconstructed CSV. Caller is responsible for cleanup.

    Returns:
        (normalized_original, reconstructed) — equal iff the round-trip
        was lossless.
    """
    # Materialize the intermediate DB and reconstructed CSV inside tmp_dir
    db_path = tmp_dir / f"{csv_path.stem}.db"
    out_path = tmp_dir / f"{csv_path.stem}.out.csv"

    # Load into memory then persist to disk to exercise the full
    # in-memory → file → loaded-back persistence cycle
    conn = load_legacy_csv(str(csv_path))
    try:
        save_db_file(conn, str(db_path))
    finally:
        conn.close()

    # Load the on-disk DB back and reconstruct the CSV from it; pull
    # section_formats so the original can be normalized using the same
    # registry the reconstructor used
    conn = load_db_file(str(db_path))
    try:
        section_formats = get_section_formats(conn)
        run_idx = get_single_run_idx(conn)
        legacy_format_idx = get_run_legacy_format(conn.cursor(), run_idx)[0]
        delimiter, has_section_labels = get_format_file_shape(
            conn.cursor(), legacy_format_idx
        )
        save_legacy_csv(conn, str(out_path))
    finally:
        conn.close()

    # Normalize the original to reconstruction conventions
    original = csv_path.read_text()
    reconstructed = out_path.read_text()
    normalized = normalize_csv(
        original, reconstructed, section_formats, delimiter, has_section_labels
    )
    return normalized, reconstructed
