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
    save_legacy_sample_id_map_csv,
)
from run_preflight.legacy import LegacyExtraColumnWarning
from run_preflight.legacy.roundtrip import roundtrip_via_api
from run_preflight.legacy.validate import validate_omnibus

from . import _helpers
from ._helpers import open_db

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

    def test_absent_reverse_complement_stays_null(self):
        # An Illumina sheet whose [Settings] lacks ReverseComplement must
        # store NULL in illumina_run (not the False default), so that
        # reconstruction NULL-skips and round-trips byte-equal. The
        # previous BOOLEAN NOT NULL DEFAULT 0 column silently substituted
        # 0 and forced a spurious ReverseComplement,0 into the output.
        csv_path = DATA_DIR / "good_standard_metagv90_no_reverse_complement.csv"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            conn = load_legacy_csv(str(csv_path))
        try:
            rc = conn.execute("SELECT reverse_complement FROM illumina_run").fetchone()[
                0
            ]
        finally:
            conn.close()
        self.assertIsNone(rc)

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

        self.assertEqual(errors, ["[Header] missing required field: SheetType"])

    def test_validate_omnibus_errors_on_missing_sheet_version(self):
        # SheetVersion is also required for format dispatch; absence must
        # produce a clear field-specific error rather than a silent v0 default
        conn = create_db(":memory:")
        try:
            sections = {"Header": {"SheetType": "standard_metag"}}
            errors = validate_omnibus(conn, sections)
        finally:
            conn.close()

        self.assertEqual(errors, ["[Header] missing required field: SheetVersion"])

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

    def test_save_legacy_csv_rejects_project_with_null_external_project_id(self):
        # Seed a project with NULL external_project_id; legacy CSV's
        # QiitaID column cannot represent NULL, so save must reject.
        db_path = self.tmp_dir / "no_qid.db"
        conn = create_db(str(db_path))
        try:
            project_idx = _helpers.seed_project(
                conn,
                project_name="proj_no_qid",
                external_project_id=None,
                ena_study_accession="ERP001",
            )
            plate_idx = _helpers.seed_plate(conn, project_idx)
            run_idx = _helpers.seed_processing_run(conn)
            _helpers.seed_sample_chain(
                conn, plate_idx, project_idx, run_idx, sample_name="S1"
            )
            conn.commit()
        finally:
            conn.close()

        # The error names every offending project so the caller can
        # fix the missing identifier(s) in one pass.
        out_path = self.tmp_dir / "out.csv"
        with open_db(str(db_path)) as conn:
            with self.assertRaisesRegex(
                ValueError, r"proj_no_qid.*NULL external_project_id"
            ):
                save_legacy_csv(conn, str(out_path))
        self.assertFalse(out_path.exists())

    def test_load_legacy_csv_rejects_empty_qiita_id_cell(self):
        # An empty QiitaID would silently become external_project_id=''
        # in the DB; validation rejects at load time instead.
        src = (DATA_DIR / "Test1_Skin_replicates_15459_novaseq.csv").read_text()
        # Replace the (only) Bioinformatics QiitaID value with an empty
        # field; the surrounding row layout is preserved verbatim.
        corrupted = src.replace(
            "Test1_Skin_Round_2_15459,15459,False,",
            "Test1_Skin_Round_2_15459,,False,",
        )
        bad_csv = self.tmp_dir / "empty_qid.csv"
        bad_csv.write_text(corrupted)

        # Validation must name the column and section so the user can
        # locate the offending cell in a multi-section file.
        db_path = self.tmp_dir / "empty_qid.db"
        with self.assertRaisesRegex(ValueError, r"Bioinformatics.*QiitaID.*empty"):
            migrate_legacy_csv_to_db_file(str(bad_csv), str(db_path))
        self.assertFalse(db_path.exists())

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


class TestSaveLegacySampleIdMapCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.out_path = self.tmp_dir / "out.csv"
        self.conn = create_db(":memory:")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _seed_illumina_run(self) -> tuple[int, int, int]:
        """Return (project_idx, plate_idx, run_idx) with illumina_run config."""
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _helpers.seed_processing_run(self.conn)
        _helpers.seed_illumina_run_config(self.conn, run_idx)
        return project_idx, plate_idx, run_idx

    def test_save_legacy_sample_id_map_csv(self):
        # Non-replicates: prepped_sample.sample_name is NULL so Sample_Name
        # falls through to input_sample.sample_name
        project_idx, plate_idx, run_idx = self._seed_illumina_run()
        _, _, prs1 = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="S1",
            well="A1",
        )
        _, _, prs2 = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="S2",
            well="A2",
        )
        _helpers.seed_illumina_sample(self.conn, prs1)
        _helpers.seed_illumina_sample(self.conn, prs2)
        self.conn.commit()

        save_legacy_sample_id_map_csv(self.conn, str(self.out_path))

        expected = "illumina_sample_idx,Sample_Name\n1,S1\n2,S2\n"
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_legacy_sample_id_map_csv_replicates(self):
        # Replicates: prepped_sample.sample_name populated, so the per-
        # replicate alias appears as Sample_Name instead of orig_name
        project_idx, plate_idx, run_idx = self._seed_illumina_run()
        _, _, prs1 = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="origA",
            well="A1",
            prs_name="origA.A1",
        )
        _, _, prs2 = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="origB",
            well="A2",
            prs_name="origB.A2",
        )
        _helpers.seed_illumina_sample(self.conn, prs1)
        _helpers.seed_illumina_sample(self.conn, prs2)
        self.conn.commit()

        save_legacy_sample_id_map_csv(self.conn, str(self.out_path))

        expected = "illumina_sample_idx,Sample_Name\n1,origA.A1\n2,origB.A2\n"
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_legacy_sample_id_map_csv_ordering(self):
        # Output rows are ordered by illumina_sample_idx independent of
        # the underlying prepped_sample insertion order
        project_idx, plate_idx, run_idx = self._seed_illumina_run()
        _, _, prs_alpha = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="alpha",
            well="A1",
        )
        _, _, prs_beta = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="beta",
            well="A2",
        )
        # Insert illumina_sample for beta first so idx=1 is beta, idx=2 is alpha
        ils_first = _helpers.seed_illumina_sample(self.conn, prs_beta)
        ils_second = _helpers.seed_illumina_sample(self.conn, prs_alpha)
        self.conn.commit()
        self.assertEqual((ils_first, ils_second), (1, 2))

        save_legacy_sample_id_map_csv(self.conn, str(self.out_path))

        expected = "illumina_sample_idx,Sample_Name\n1,beta\n2,alpha\n"
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_legacy_sample_id_map_csv_no_illumina_samples(self):
        # A run with no illumina_sample rows must raise ValueError
        project_idx, plate_idx, run_idx = self._seed_illumina_run()
        _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx,
            run_idx,
            sample_name="S1",
            well="A1",
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, r"no illumina_sample rows"):
            save_legacy_sample_id_map_csv(self.conn, str(self.out_path))


if __name__ == "__main__":
    unittest.main()
