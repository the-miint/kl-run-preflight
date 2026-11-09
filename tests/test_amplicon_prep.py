"""Round-trip and typed-projection tests for the amplicon prep template.

The prep template is a flat, TAB-delimited sheet with no [Section] labels; it
loads through the same parse → validate → populate path as the sectioned
omnibus sheets, so these tests exercise that shared path end to end. Tests
query tables directly, which production consumers must not — that is fine here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from run_preflight.legacy.api import load_file, load_legacy_csv, save_legacy_csv
from run_preflight.legacy.parser import _split_prep_template

from . import _helpers

DATA_DIR = Path(__file__).parent / "data" / "legacy"

# Real prep templates spanning the three registered layouts. The template is
# free-form (studies vary the column set/order); study identifiers are scrubbed.
# Named by what each exercises; one per distinct scenario across the three formats.
AMPLICON_FIXTURES = (
    "good_amplicon_v1.txt",  # v1 wide + control_description
    "good_amplicon_v1_katharoseq.txt",  # v1 + katharoseq controls
    "good_amplicon_v2.txt",  # v2 narrow (well_id)
    "good_amplicon_v2_katharoseq.txt",  # v2 + katharoseq controls
    "good_amplicon_v2_multi_project.txt",  # v2 spanning several projects
    "good_amplicon_v3.txt",  # v3 wide without control_description
    "good_amplicon_v3_extra_columns.txt",  # v3 + unrecognized columns (verbatim)
)


def _content(path: Path) -> tuple[frozenset[str], list]:
    """A sheet's column set + the multiset of per-row {column: value}, both
    order-independent — the shape of a data-exact comparison."""
    lines = [ln for ln in path.read_text().split("\n") if ln != ""]
    header = lines[0].split("\t")
    rows = sorted(tuple(sorted(zip(header, ln.split("\t")))) for ln in lines[1:])
    return frozenset(header), rows


class TestAmpliconPrepRoundTrip(unittest.TestCase):
    """Every registered prep-template layout round-trips data-exact.

    A flat sheet's reconstruction emits a canonical column order and does not
    preserve the source's order, so the round-trip guarantee is on CONTENT — the
    same column set and the same per-row values — not on byte layout.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_layouts_roundtrip_data_exact(self):
        for name in AMPLICON_FIXTURES:
            with self.subTest(sheet=name):
                conn = load_legacy_csv(str(DATA_DIR / name))
                out = self.tmp_dir / "out.txt"
                try:
                    save_legacy_csv(conn, str(out))
                finally:
                    conn.close()
                self.assertEqual(_content(DATA_DIR / name), _content(out))

    def test_load_file_accepts_a_prep_template(self):
        # load_file dispatches on content, so a prep template reaches the same
        # loader as an omnibus CSV without the caller naming a format. Column
        # order is normalized on comparison, so the check is on the column set
        # and the row count the write produced.
        sheet = DATA_DIR / "good_amplicon_v1_katharoseq.txt"
        out_path = self.tmp_dir / "out.txt"
        conn = load_file(str(sheet))
        try:
            save_legacy_csv(conn, str(out_path))
        finally:
            conn.close()
        source_lines = sheet.read_text().splitlines()
        written_lines = out_path.read_text().splitlines()
        self.assertEqual(len(written_lines), len(source_lines))
        self.assertEqual(
            set(written_lines[0].split("\t")), set(source_lines[0].split("\t"))
        )


class TestAmpliconPrepProjection(unittest.TestCase):
    """The prep template's facts land in their typed homes, not only verbatim."""

    def _load(self, sheet_name: str):
        return load_legacy_csv(str(DATA_DIR / sheet_name))

    def test_katharoseq_controls_are_typed_by_name(self):
        # Controls are typed from their "KATHARO." / "BLANK." name prefix. This
        # layout carries no per-control cell count, so the katharoseq_sample
        # detail table stays empty (Kathseq_RackID / number_of_cells are the
        # format's optional group, absent from this sheet).
        conn = self._load("good_amplicon_v1_katharoseq.txt")
        try:
            counts = dict(
                conn.execute(
                    "SELECT st.name, COUNT(*) FROM input_sample i "
                    "JOIN sample_type st ON i.sample_type_idx = st.sample_type_idx "
                    "GROUP BY st.name"
                ).fetchall()
            )
            katharoseq_rows = conn.execute(
                "SELECT COUNT(*) FROM katharoseq_sample"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            counts,
            {
                "standard": 225,
                "extraction_blank": 44,
                "katharoseq_cells_positive_control": 48,
            },
        )
        self.assertEqual(katharoseq_rows, 0)

    def test_run_and_plate_facts_are_typed_once(self):
        # Facts constant across a run or a plate are stored once at that grain
        # rather than repeated on every sample row.
        conn = self._load("good_amplicon_v1_katharoseq.txt")
        try:
            run = conn.execute(
                "SELECT primer, target_gene, target_subfragment, barcodes_are_rc "
                "FROM amplicon_run"
            ).fetchall()
            plates = conn.execute(
                "SELECT plate_name, primer_plate, extraction_robot "
                "FROM input_plate ORDER BY plate_name"
            ).fetchall()
        finally:
            conn.close()
        # barcodes_are_rc is inferred from the EMP 515f primer and stored once.
        self.assertEqual(run, [("GTGYCAGCMGCCGCGGTAA", "16S rRNA", "V4", 1)])
        self.assertEqual(len(plates), 4)
        self.assertTrue(all(primer_plate for _, primer_plate, _ in plates))

    def test_no_illumina_sample_rows_for_an_amplicon_run(self):
        # An amplicon run carries a single in-line Golay barcode, not an
        # i5/i7 pair, so it has no platform-specific sample rows.
        conn = self._load("good_amplicon_v1_katharoseq.txt")
        try:
            illumina_rows = conn.execute(
                "SELECT COUNT(*) FROM illumina_sample"
            ).fetchone()[0]
            illumina_run_rows = conn.execute(
                "SELECT COUNT(*) FROM illumina_run"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(illumina_rows, 0)
        self.assertEqual(illumina_run_rows, 1)

    def test_unrecognized_columns_pass_through_verbatim(self):
        # This layout carries LocationCell/Column/Row, which no format types; they
        # round-trip through legacy_extra_column, one value per sample.
        conn = self._load("good_amplicon_v3_extra_columns.txt")
        try:
            n = conn.execute("SELECT COUNT(*) FROM amplicon_sample").fetchone()[0]
            loc = dict(
                conn.execute(
                    "SELECT column_name, COUNT(*) FROM legacy_extra_column "
                    "WHERE column_name LIKE 'Location%' GROUP BY column_name"
                ).fetchall()
            )
        finally:
            conn.close()
        self.assertEqual(
            loc, {"LocationCell": n, "LocationColumn": n, "LocationRow": n}
        )


class TestAmpliconPrepWhitespace(unittest.TestCase):
    def test_load_legacy_csv_strips_prep_template_cells(self):
        # Padding around a cell is stripped, as on the sectioned-CSV path,
        # so a padded project name does not split one project into two
        src = (DATA_DIR / "good_amplicon_v1.txt").read_text()
        padded = src.replace(
            "\tS4_20006\tsample.00001\t", "\tS4_20006 \t sample.00001\u200b\t", 1
        )
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "padded.txt"
            sheet.write_text(padded)
            conn = load_legacy_csv(str(sheet))
        try:
            projects = conn.execute("SELECT project_name FROM project").fetchall()
            sample_rows = conn.execute(
                "SELECT sample_name FROM input_sample"
            ).fetchall()
        finally:
            conn.close()
        padded_names = [
            name for (name,) in sample_rows if name != name.strip(" \u200b")
        ]
        self.assertEqual((projects, padded_names), ([("S4_20006",)], []))


class TestAmpliconPrepBlankLines(unittest.TestCase):
    def test_load_legacy_csv_skips_whitespace_only_prep_template_lines(self):
        # Lines of only whitespace or invisible characters are skipped, as on
        # the sectioned-CSV path
        src = (DATA_DIR / "good_amplicon_v1.txt").read_text()
        blank_lines = "  " + chr(0x200B) + "\n\t\n"
        counts = []
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (("plain", src), ("padded", src + blank_lines)):
                sheet = Path(tmp) / f"{name}.txt"
                sheet.write_text(text)
                conn = load_legacy_csv(str(sheet))
                try:
                    count = _helpers.count_input_samples(conn)
                finally:
                    conn.close()
                counts.append(count)
        self.assertEqual(counts[1], counts[0])

    def test__split_prep_template_skip_all_blank_row(self):
        # A row whose every cell is blank is skipped whatever the delimiter
        results = {}
        for delimiter in (",", "\t"):
            text = f"a{delimiter}b\n1{delimiter}2\n {delimiter}{chr(0x200B)}\n"
            results[delimiter] = _split_prep_template(text, delimiter)
        expected_rows = [["a", "b"], ["1", "2"]]
        self.assertEqual(results, {",": expected_rows, "\t": expected_rows})


class TestAmpliconPrepRejection(unittest.TestCase):
    """A malformed or unrecognised prep template fails at load, not later."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text: str) -> str:
        path = self.tmp_dir / "sheet.txt"
        path.write_text(text)
        return str(path)

    def test_ragged_row_is_rejected(self):
        sheet = self._write("sample_name\tbarcode\nonly_one_cell\n")
        with self.assertRaises(ValueError) as ctx:
            load_legacy_csv(sheet)
        self.assertIn("line 2", str(ctx.exception))

    def test_header_only_sheet_is_rejected(self):
        sheet = self._write("sample_name\tbarcode\n")
        with self.assertRaises(ValueError) as ctx:
            load_legacy_csv(sheet)
        self.assertIn("no sample rows", str(ctx.exception))

    def test_unrecognised_layout_names_its_header(self):
        sheet = self._write("sample_name\tnot_a_known_column\na\tb\n")
        with self.assertRaises(ValueError) as ctx:
            load_legacy_csv(sheet)
        self.assertIn("No registered amplicon format", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
