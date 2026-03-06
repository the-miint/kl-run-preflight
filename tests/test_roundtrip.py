"""Round-trip tests: parse CSV → populate DB → reconstruct CSV → compare."""

from __future__ import annotations

import csv
import io
import re
import tempfile
import unittest
from pathlib import Path

from sequencing_brief.constants import FORMAT_TABULAR
from sequencing_brief.db import create_db, get_section_formats, populate_db
from sequencing_brief.parser import (
    parse_omnibus,
    parse_omnibus_text,
    is_section_header,
    extract_section_name,
    strip_entries,
)
from sequencing_brief.reconstruct import reconstruct_omnibus

DATA_DIR = Path(__file__).parent / "data"

# Set to a directory path to write out the SQLite DB and reconstructed CSV
# for each test (e.g. "/tmp/roundtrip_debug").  Leave empty to disable.
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


def _roundtrip(csv_path: str, test_name: str) -> tuple[str, str]:
    """Run the full round-trip and return (normalized_original, reconstructed).

    Creates the DB first to obtain section formats, then parses, populates,
    and reconstructs.
    """
    if DEBUG_OUTPUT_DIR:
        out_dir = Path(DEBUG_OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(out_dir / f"{test_name}.db")
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()

    Path(db_path).unlink(missing_ok=True)
    try:
        # Create DB first to get section format definitions
        conn = create_db(db_path)
        section_formats = get_section_formats(conn)

        # Parse with section formats from DB
        sections = parse_omnibus(csv_path, section_formats)

        # Populate and reconstruct
        populate_db(conn, sections)
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


class TestRoundTrip(unittest.TestCase):
    def test_good_pacbio_absquantv11(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_pacbio_absquantv11.csv"),
            "good_pacbio_absquantv11",
        )
        self.assertEqual(original, reconstructed)

    def test_pacbio_v11_absquant_unpooled(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "pacbio_v11_absquant_unpooled_sample_sheet.csv"),
            "pacbio_absquant_v11_unpooled",
        )
        self.assertEqual(original, reconstructed)

    def test_skin_replicates_novaseq(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "Test1_Skin_replicates_15459_novaseq.csv"),
            "standard_metag_v101_replicates_novaseq",
        )
        self.assertEqual(original, reconstructed)

    def test_celeste_adaptation_novaseq(self):
        original, reconstructed = _roundtrip(
            str(
                DATA_DIR
                / "YYYY_MM_DD_Celeste_Adaptation_12986_16_17_18_21_matrix_samplesheet_novaseq.csv"
            ),
            "standard_metag_v101_novaseq",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_metagv11(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_pacbio_metagv11.csv"),
            "pacbio_metag_v11",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_metagv10(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_pacbio_metagv10.csv"),
            "pacbio_metag_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_absquantv10(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_pacbio_absquantv10.csv"),
            "pacbio_absquant_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv90(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_standard_metagv90.csv"),
            "standard_metag_v90",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv0_really_metat(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_standard_metagv0_really_metat.csv"),
            "standard_metag_v0_really_metat",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv100_wo_replicates(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_standard_metagv100_wo_replicates.csv"),
            "standard_metag_v100_wo_replicates",
        )
        self.assertEqual(original, reconstructed)

    def test_good_abs_quant_metagv10(self):
        original, reconstructed = _roundtrip(
            str(DATA_DIR / "good_abs_quant_metagv10.csv"),
            "abs_quant_metag_v10",
        )
        self.assertEqual(original, reconstructed)


if __name__ == "__main__":
    unittest.main()
