"""Tests for the consumer-facing wrappers in run_preflight.legacy.api."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from run_preflight import (
    create_db,
    load_legacy_csv,
    migrate_legacy_csv_to_db_file,
    open_db_file,
    save_legacy_csv,
)
from run_preflight.legacy import LegacyExtraColumnWarning
from run_preflight.legacy.roundtrip import roundtrip_via_api
from run_preflight.legacy.validate import validate_omnibus

DATA_DIR = Path(__file__).parent / "data"
GOOD_CSV = DATA_DIR / "good_pacbio_metagv11.csv"


class TestLegacyApi(unittest.TestCase):
    def setUp(self):
        # Per-test scratch dir for the intermediate DB and reconstructed CSV
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_migrate_legacy_csv_to_db_file(self):
        # Loading a known-good CSV should produce exactly one run row
        db_path = self.tmp_dir / "loaded.db"
        migrate_legacy_csv_to_db_file(str(GOOD_CSV), str(db_path))

        # Inspect the populated DB via the public open_db_file entry point
        conn = open_db_file(str(db_path))
        try:
            run_idxs = [
                row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
            ]
        finally:
            conn.close()
        self.assertEqual(run_idxs, [1])

    def test_migrate_legacy_csv_to_db_file_validation_failure(self):
        # Build a CSV that parses but advertises an unknown sheet type so it
        # fails at the validation step rather than the parse step
        bad_csv = self.tmp_dir / "bad.csv"
        bad_csv.write_text(
            "[Header],,,\nSheetType,bogus_type,,\nSheetVersion,99,,\n,,,\n"
        )

        # Validation failure must raise a ValueError naming the unknown
        # format and must leave no DB file behind
        db_path = self.tmp_dir / "bad.db"
        with self.assertRaisesRegex(ValueError, r"Unknown format.*bogus_type"):
            migrate_legacy_csv_to_db_file(str(bad_csv), str(db_path))
        self.assertFalse(db_path.exists())

    def test_save_legacy_csv(self):
        # The written CSV should byte-equal the (normalized) original — a
        # weaker check (e.g. "starts with [Header]") would not catch
        # corruption inside the file
        normalized, reconstructed = roundtrip_via_api(GOOD_CSV, self.tmp_dir)
        self.assertEqual(reconstructed, normalized)

    def test_save_legacy_csv_no_runs(self):
        # Empty DB has the schema but no processing_run rows; the error
        # must report the actual found count (0)
        conn = create_db(":memory:")
        try:
            out_path = self.tmp_dir / "out.csv"
            with self.assertRaisesRegex(
                ValueError, r"Expected exactly one processing run, found 0"
            ):
                save_legacy_csv(conn, str(out_path))
        finally:
            conn.close()

    def test_load_legacy_csv_warns_on_extras(self):
        # PacBio CSVs carry a Lane column that is not part of any PacBio
        # view, so it must be flagged via LegacyExtraColumnWarning
        with self.assertWarnsRegex(LegacyExtraColumnWarning, r"\bLane\b"):
            conn = load_legacy_csv(str(GOOD_CSV))
        conn.close()

    def test_save_legacy_csv_warns_on_extras(self):
        # Load with warnings suppressed so only the save-side warning is
        # observed; otherwise the load warning would dominate the assertion
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            conn = load_legacy_csv(str(GOOD_CSV))

        try:
            # Writing the DB back out re-emits the carried extras and must
            # warn naming the same Lane column
            out_path = self.tmp_dir / "out.csv"
            with self.assertWarnsRegex(LegacyExtraColumnWarning, r"\bLane\b"):
                save_legacy_csv(conn, str(out_path))
        finally:
            conn.close()

    def test_migrate_legacy_csv_to_db_file_rejects_deviant_header_constant(self):
        # Corrupt the Workflow value in a known-good Illumina v101 file;
        # validation must reject so the user is told at load time rather
        # than getting silently-replaced output from a round-trip
        src = (DATA_DIR / "Test1_Skin_replicates_15459_novaseq.csv").read_text()
        corrupted = src.replace("Workflow,GenerateFASTQ", "Workflow,bcl2fastq")
        bad_csv = self.tmp_dir / "bad.csv"
        bad_csv.write_text(corrupted)

        # The error must name the field, the observed value, and the
        # expected value so the user knows exactly what cannot be preserved
        db_path = self.tmp_dir / "bad.db"
        with self.assertRaisesRegex(ValueError, r"Workflow.*bcl2fastq.*GenerateFASTQ"):
            migrate_legacy_csv_to_db_file(str(bad_csv), str(db_path))
        self.assertFalse(db_path.exists())

    def test_validate_omnibus_allows_missing_settings_keys(self):
        # Settings keys may legitimately be absent: the reconstructor's
        # _write_header_kv NULL-skips on output, so missing Settings keys
        # round-trip cleanly. Header keys, by contrast, must remain required.
        conn = create_db(":memory:")
        try:
            sections = {
                "Header": {
                    "SheetType": "standard_metag",
                    "SheetVersion": "101",
                },
                "Settings": {"ReverseComplement": "0"},
            }
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        # Settings missing MaskShortReads / OverrideCycles must NOT error
        settings_missing = [
            e for e in errors if e.startswith("[Settings]") and "missing columns" in e
        ]
        self.assertEqual(settings_missing, [])

    def test_validate_omnibus_accepts_all_settings_keys_in_v90(self):
        # After unifying the per-version Settings views into
        # omnibus_illumina_settings, all three keys (ReverseComplement,
        # MaskShortReads, OverrideCycles) are valid for v90 and v0 too,
        # not only v100/v101.
        conn = create_db(":memory:")
        try:
            sections = {
                "Header": {
                    "SheetType": "standard_metag",
                    "SheetVersion": "90",
                },
                "Settings": {
                    "ReverseComplement": "0",
                    "MaskShortReads": "1",
                    "OverrideCycles": "Y150;I8N2;I8N16;Y150",
                },
            }
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        # No Settings-related errors should be produced
        settings_errors = [e for e in errors if e.startswith("[Settings]")]
        self.assertEqual(settings_errors, [])

    def test_validate_omnibus_errors_on_missing_sheet_type(self):
        # SheetType drives format dispatch; absence must produce a clear
        # field-specific error rather than the misleading "Unknown
        # format:  v0" that the previous ("", 0) default lookup yielded.
        conn = create_db(":memory:")
        try:
            sections = {"Header": {"SheetVersion": "101"}}
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        self.assertEqual(
            errors, ["[Header] missing required field: SheetType"]
        )

    def test_validate_omnibus_errors_on_missing_sheet_version(self):
        # SheetVersion is also required for format dispatch; absence must
        # produce a clear field-specific error rather than a silent v0 default
        conn = create_db(":memory:")
        try:
            sections = {"Header": {"SheetType": "standard_metag"}}
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        self.assertEqual(
            errors, ["[Header] missing required field: SheetVersion"]
        )

    def test_validate_omnibus_errors_on_missing_header_section(self):
        # An entirely-missing [Header] section surfaces as both field-level
        # errors rather than a dedicated section-missing error
        conn = create_db(":memory:")
        try:
            errors = validate_omnibus(conn, {})
        finally:
            conn.close()

        self.assertEqual(
            errors,
            [
                "[Header] missing required field: SheetType",
                "[Header] missing required field: SheetVersion",
            ],
        )

    def test_validate_omnibus_skips_constant_check_for_pacbio(self):
        # PacBio uses a different Header view with no hardcoded literals,
        # so the constant-preservation check must not fire for PacBio
        # even when a deviant value is supplied. Other errors may appear
        # (e.g. missing sections), but not the constant-preservation one.
        conn = create_db(":memory:")
        try:
            sections = {
                "Header": {
                    "SheetType": "pacbio_metag",
                    "SheetVersion": "11",
                    "Workflow": "bcl2fastq",
                },
            }
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        # No error should mention the constant-preservation message
        constant_errors = [e for e in errors if "cannot be preserved" in e]
        self.assertEqual(constant_errors, [])


if __name__ == "__main__":
    unittest.main()
