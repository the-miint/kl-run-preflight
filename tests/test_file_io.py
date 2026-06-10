"""Tests for the format-detecting open_file entry point and the
bcl-convert v1 sample sheet writer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path

from run_preflight import (
    load_legacy_csv,
    migrate_legacy_csv_to_db_file,
    open_file,
    save_bclconvert_v1_csv,
)
from run_preflight.db import create_db
from run_preflight.legacy import LegacyExtraColumnWarning

from . import _helpers

DATA_DIR = Path(__file__).parent / "data"
GOOD_CSV = DATA_DIR / "good_standard_metagv90.csv"


# ---------------------------------------------------------------------------
# Fixture helpers — minimal in-memory DB shaped for the bcl-convert v1 writer
# ---------------------------------------------------------------------------


def _seed_illumina_run(
    conn: sqlite3.Connection,
    *,
    mask_short_reads: str | None = None,
    override_cycles: str | None = None,
) -> int:
    """Insert a processing_run (Illumina platform) and matching illumina_run row.

    Returns the run_idx.
    """
    run_idx = _helpers.seed_processing_run(conn)
    _helpers.seed_illumina_run_config(
        conn,
        run_idx,
        mask_short_reads=mask_short_reads,
        override_cycles=override_cycles,
    )
    return run_idx


def _seed_pacbio_run(conn: sqlite3.Connection) -> int:
    """Insert a processing_run on the PacBio platform with no illumina_run row."""
    return _helpers.seed_processing_run(
        conn,
        experiment_name="exp_pb",
        instrument_type="Revio",
        platform_idx=2,
    )


def _seed_prepped_sample(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int,
    run_idx: int,
    sample_name: str,
    well: str,
) -> int:
    """Insert input_sample + compression_sample + prepped_sample; return prs_idx."""
    _ins_idx, _cs_idx, prs_idx = _helpers.seed_sample_chain(
        conn,
        plate_idx,
        project_idx,
        run_idx,
        sample_name=sample_name,
        well=well,
    )
    return prs_idx


# ---------------------------------------------------------------------------
# TestOpenFile
# ---------------------------------------------------------------------------


class TestOpenFile(unittest.TestCase):
    def setUp(self):
        # Per-test scratch dir for any intermediate DB files
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_file_csv(self):
        # A legacy omnibus CSV must dispatch through load_legacy_csv and
        # return a populated in-memory connection
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            conn = open_file(str(GOOD_CSV))
        try:
            run_idxs = [
                row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
            ]
        finally:
            conn.close()
        self.assertEqual(run_idxs, [1])

    def test_open_file_db(self):
        # A SQLite DB file must be detected via the magic header and
        # dispatch through open_db_file
        db_path = self.tmp_dir / "loaded.db"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            migrate_legacy_csv_to_db_file(str(GOOD_CSV), str(db_path))

        # Re-opening the DB via open_file should yield the same single run row
        conn = open_file(str(db_path))
        try:
            run_idxs = [
                row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
            ]
        finally:
            conn.close()
        self.assertEqual(run_idxs, [1])

    def test_open_file_missing_path(self):
        # A nonexistent path must raise FileNotFoundError with a clear message
        missing = self.tmp_dir / "does_not_exist.csv"
        with self.assertRaisesRegex(FileNotFoundError, r"No such file"):
            open_file(str(missing))

    def test_open_file_empty_file(self):
        # An empty file cannot be SQLite (no magic header) so it falls
        # through to the legacy parser, which must reject it via ValueError
        empty = self.tmp_dir / "empty.csv"
        empty.write_text("")
        with self.assertRaises(ValueError):
            open_file(str(empty))


# ---------------------------------------------------------------------------
# TestSaveBclconvertV1Csv
# ---------------------------------------------------------------------------


class TestSaveBclconvertV1Csv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.out_path = self.tmp_dir / "out.csv"
        self.conn = create_db(":memory:")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_save_bclconvert_v1_csv(self):
        # Single-lane run with both [Settings] keys populated produces the
        # full four-section file
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(
            self.conn, mask_short_reads="22", override_cycles="Y151;I8;I8;Y151"
        )
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        prs2 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S2", "A2"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        _helpers.seed_illumina_sample(
            self.conn, prs2, i7_seq="TTAGGCAT", i5_seq="GGCTACTG", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Settings]\n"
            "MaskShortReads,22\n"
            "OverrideCycles,Y151;I8;I8;Y151\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "1,1,ATCACGAT,CGATGTAC,proj1\n"
            "1,2,TTAGGCAT,GGCTACTG,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_no_lane(self):
        # When illumina_sample.lane is uniformly NULL, the Lane column is
        # omitted from [Data]. Settings stay populated to isolate the no-lane case.
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(
            self.conn, mask_short_reads="22", override_cycles="Y151;I8;I8;Y151"
        )
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=None
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Settings]\n"
            "MaskShortReads,22\n"
            "OverrideCycles,Y151;I8;I8;Y151\n"
            "\n"
            "[Data]\n"
            "Sample_ID,index,index2,Sample_Project\n"
            "1,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_no_settings(self):
        # When both setting columns are NULL, [Settings] is omitted entirely
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn)
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "1,1,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_only_mask_short_reads(self):
        # When only mask_short_reads is non-null, [Settings] contains
        # only that line
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn, mask_short_reads="22")
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Settings]\n"
            "MaskShortReads,22\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "1,1,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_only_override_cycles(self):
        # When only override_cycles is non-null, [Settings] contains
        # only that line
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn, override_cycles="Y151;I8;I8;Y151")
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Settings]\n"
            "OverrideCycles,Y151;I8;I8;Y151\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "1,1,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_include_sample_name(self):
        # Sample_Names deliberately do not track sample_id ordering, and
        # one sample has NULL sample_name (identified by biosample only)
        # so its Sample_Name cell renders as empty string.
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(
            self.conn, mask_short_reads="22", override_cycles="Y151;I8;I8;Y151"
        )
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "zeta_001", "A1"
        )
        prs2 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "alpha_042", "A2"
        )
        prs3 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "to_be_nulled", "A3"
        )

        # Promote sample 3 to biosample-only identity (sample_name = NULL)
        self.conn.execute(
            "UPDATE input_sample SET sample_name = NULL, "
            "biosample_accession = 'SAMN00000003' "
            "WHERE input_sample_idx = ("
            "    SELECT cs.input_sample_idx FROM prepped_sample prs "
            "    JOIN compression_sample cs "
            "      ON prs.compression_sample_idx = cs.compression_sample_idx "
            "    WHERE prs.prepped_sample_idx = ?)",
            (prs3,),
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        _helpers.seed_illumina_sample(
            self.conn, prs2, i7_seq="TTAGGCAT", i5_seq="GGCTACTG", lane=1
        )
        _helpers.seed_illumina_sample(
            self.conn, prs3, i7_seq="GCAATAAG", i5_seq="AATGCAGT", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path), include_sample_name=True)

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Settings]\n"
            "MaskShortReads,22\n"
            "OverrideCycles,Y151;I8;I8;Y151\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,Sample_Name,index,index2,Sample_Project\n"
            "1,1,zeta_001,ATCACGAT,CGATGTAC,proj1\n"
            "1,2,alpha_042,TTAGGCAT,GGCTACTG,proj1\n"
            "1,3,,GCAATAAG,AATGCAGT,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_include_sample_name_no_lane(self):
        # Lane omission and Sample_Name inclusion must compose: header is
        # Sample_ID,Sample_Name,index,index2,Sample_Project (no Lane).
        # Sample_Name deliberately does not track sample_id ordering.
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn)
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "delta_777", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=None
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path), include_sample_name=True)

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Data]\n"
            "Sample_ID,Sample_Name,index,index2,Sample_Project\n"
            "1,delta_777,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_multi_lane(self):
        # Two lanes for one prepped_sample produce two rows ordered by illumina_sample_idx.
        # Lanes 3 and 7 differ from the illumina_sample_idx values to catch a column swap.
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn)
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=3
        )
        _helpers.seed_illumina_sample(
            self.conn, prs1, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=7
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "3,1,ATCACGAT,CGATGTAC,proj1\n"
            "7,2,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_deterministic_ordering_from_legacy(self):
        # A legacy-loaded run must emit Sample_ID values matching the source CSV's
        # data-row order (monotonic 1..N matching illumina_sample_idx).
        self.conn.close()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            self.conn = load_legacy_csv(str(GOOD_CSV))

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        # Sample_IDs in the [Data] section should be 1, 2, 3, ... in
        # the same order as illumina_sample rows were inserted
        text = self.out_path.read_text()
        data_marker = "[Data]\n"
        data_section = text.split(data_marker, 1)[1]
        data_lines = [
            line
            for line in data_section.splitlines()
            if line and not line.startswith("[")
        ]
        # First line is the header row; remaining lines are data rows
        header_cols = data_lines[0].split(",")
        sample_id_pos = header_cols.index("Sample_ID")
        sample_ids = [int(line.split(",")[sample_id_pos]) for line in data_lines[1:]]

        # Compare against the illumina_sample_idx values from the DB
        cur = self.conn.cursor()
        cur.execute(
            "SELECT ils.illumina_sample_idx "
            "FROM illumina_sample ils "
            "JOIN prepped_sample prs "
            "  ON ils.prepped_sample_idx = prs.prepped_sample_idx "
            "JOIN compression_sample cs "
            "  ON prs.compression_sample_idx = cs.compression_sample_idx "
            "ORDER BY ils.illumina_sample_idx"
        )
        expected_ids = [r[0] for r in cur.fetchall()]
        self.assertEqual(sample_ids, expected_ids)
        # Determinism: ids must form a contiguous 1..N sequence
        self.assertEqual(sample_ids, list(range(1, len(sample_ids) + 1)))

    def test_save_bclconvert_v1_csv_control_uses_plate_primary_project(self):
        # A control row (input_sample.project_idx IS NULL) must resolve
        # Sample_Project to the plate's primary project name
        _project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn)
        _ins_idx, _cs_idx, prs_ctl = _helpers.seed_sample_chain(
            self.conn,
            plate_idx,
            project_idx=None,
            run_idx=run_idx,
            sample_name="blank1",
            sample_type_name="extraction_blank",
            well="A1",
        )
        _helpers.seed_illumina_sample(
            self.conn, prs_ctl, i7_seq="ATCACGAT", i5_seq="CGATGTAC", lane=1
        )
        self.conn.commit()

        save_bclconvert_v1_csv(self.conn, str(self.out_path))

        expected = (
            "[Header]\n"
            "FileFormatVersion,1\n"
            "\n"
            "[Data]\n"
            "Lane,Sample_ID,index,index2,Sample_Project\n"
            "1,1,ATCACGAT,CGATGTAC,proj1\n"
        )
        self.assertEqual(self.out_path.read_text(), expected)

    def test_save_bclconvert_v1_csv_zero_runs_raise_err(self):
        # Zero-run case raises naming the run-count problem; the >1 case is
        # unreachable because the schema's "at most one run" trigger blocks it.
        with self.assertRaisesRegex(ValueError, r"exactly one processing run"):
            save_bclconvert_v1_csv(self.conn, str(self.out_path))

    def test_save_bclconvert_v1_csv_pacbio_raise_err(self):
        # A PacBio run (no illumina_run, no illumina_sample) must be rejected with
        # a ValueError naming the missing illumina_sample rows, not a generic failure.
        _helpers.seed_project_and_plate(self.conn)
        _seed_pacbio_run(self.conn)
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, r"no illumina_sample rows"):
            save_bclconvert_v1_csv(self.conn, str(self.out_path))

    def test_save_bclconvert_v1_csv_tellseq_raise_err(self):
        # A TellSeq run has illumina_run but no illumina_sample rows, so the writer
        # must reject it with the same no-illumina_sample-rows error as PacBio.
        project_idx, plate_idx = _helpers.seed_project_and_plate(self.conn)
        run_idx = _seed_illumina_run(self.conn)
        prs1 = _seed_prepped_sample(
            self.conn, plate_idx, project_idx, run_idx, "S1", "A1"
        )
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO tellseq_sample "
            "(prepped_sample_idx, barcode_id, lane) "
            "VALUES (?, 'BX001', 1)",
            (prs1,),
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, r"no illumina_sample rows"):
            save_bclconvert_v1_csv(self.conn, str(self.out_path))


if __name__ == "__main__":
    unittest.main()
