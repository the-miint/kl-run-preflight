"""Tests for multi-lane support: surrogate PKs, integrity triggers, and
populate-side lane-split deduplication."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from run_preflight.db import create_db, get_section_formats, populate_db
from run_preflight.legacy.parser import parse_omnibus

DATA_DIR = Path(__file__).parent / "data"


def _setup_run_and_prs(conn: sqlite3.Connection) -> tuple[int, int]:
    """Insert minimal prerequisite rows and return (run_idx, prs_idx)."""
    cur = conn.cursor()

    # Project, plate, input_sample, run, compression_sample, prepped_sample
    cur.execute(
        "INSERT INTO project "
        "(project_name, external_project_id, human_filtering, "
        " library_construction_protocol, experiment_design_description) "
        "VALUES ('proj1', '1', 1, 'proto', 'desc')"
    )
    project_idx = cur.lastrowid

    cur.execute(
        "INSERT INTO input_plate (plate_name, primary_project_idx) VALUES ('plate1', ?)",
        (project_idx,),
    )
    plate_idx = cur.lastrowid

    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
        "VALUES ('sample1', ?, ?, 1)",
        (plate_idx, project_idx),
    )
    input_sample_idx = cur.lastrowid

    cur.execute(
        "INSERT INTO processing_run "
        "(experiment_name, run_date, instrument_type, "
        " assay_type_idx, platform_idx) "
        "VALUES ('exp1', '2025-01-01', 'Unknown', 1, 1)"
    )
    run_idx = cur.lastrowid

    cur.execute(
        "INSERT INTO compression_sample "
        "(run_idx, input_sample_idx, compression_well) "
        "VALUES (?, ?, 'A1')",
        (run_idx, input_sample_idx),
    )
    cs_idx = cur.lastrowid

    cur.execute(
        "INSERT INTO prepped_sample "
        "(compression_sample_idx, prepped_well) "
        "VALUES (?, 'A1')",
        (cs_idx,),
    )
    prs_idx = cur.lastrowid

    conn.commit()
    assert run_idx is not None and prs_idx is not None
    return run_idx, prs_idx


class TestMultiLaneSchemaIntegrity(unittest.TestCase):
    """Direct-SQL tests of the new schema constraints and triggers."""

    def setUp(self):
        self.conn = create_db(":memory:")
        self.run_idx, self.prs_idx = _setup_run_and_prs(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert_illumina(self, lane, *, i7_seq="GATTACA", i5_seq="TGCATGC"):
        # Helper that inserts one illumina_sample row with controllable lane and indexes
        self.conn.execute(
            "INSERT INTO illumina_sample "
            "(prepped_sample_idx, i7_index_id, i7_sequence, "
            " i5_index_id, i5_sequence, lane) "
            "VALUES (?, 'I7A', ?, 'I5A', ?, ?)",
            (self.prs_idx, i7_seq, i5_seq, lane),
        )

    def _insert_tellseq(self, lane, *, barcode_id="C501"):
        self.conn.execute(
            "INSERT INTO tellseq_sample "
            "(prepped_sample_idx, barcode_id, lane) "
            "VALUES (?, ?, ?)",
            (self.prs_idx, barcode_id, lane),
        )

    def test_multi_lane_insert_succeeds(self):
        # Two illumina_sample rows with same prs, different lanes, identical i7/i5
        self._insert_illumina(1)
        self._insert_illumina(2)
        self.conn.commit()

        cur = self.conn.execute(
            "SELECT illumina_sample_idx, lane FROM illumina_sample "
            "WHERE prepped_sample_idx = ? ORDER BY lane",
            (self.prs_idx,),
        )
        rows = cur.fetchall()
        # Distinct surrogate PKs, two distinct non-NULL lanes
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertEqual([r[1] for r in rows], [1, 2])

    def test_illumina_index_invariance_rejects_mismatch(self):
        self._insert_illumina(1, i7_seq="GATTACA")
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self._insert_illumina(2, i7_seq="DIFFERENT")
        self.assertIn("i5/i7", str(ctx.exception))

    def test_tellseq_barcode_invariance_rejects_mismatch(self):
        self._insert_tellseq(1, barcode_id="C501")
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self._insert_tellseq(2, barcode_id="C999")
        self.assertIn("barcode_id", str(ctx.exception))

    def test_illumina_lane_uniformity_rejects_null_then_value(self):
        self._insert_illumina(None)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self._insert_illumina(1)
        self.assertIn("uniformly", str(ctx.exception))

    def test_illumina_lane_uniformity_rejects_value_then_null(self):
        self._insert_illumina(1)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self._insert_illumina(None)
        self.assertIn("uniformly", str(ctx.exception))

    def test_tellseq_lane_uniformity_rejects_null_then_value(self):
        self._insert_tellseq(None)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self._insert_tellseq(1)
        self.assertIn("uniformly", str(ctx.exception))

    def test_unique_index_rejects_duplicate_null_lane(self):
        # Two NULL-lane rows for the same prs collide under COALESCE(lane, -1)
        self._insert_illumina(None)
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_illumina(None)

    def test_unique_index_rejects_duplicate_lane_value(self):
        # Two rows with the same lane number for the same prs collide
        self._insert_illumina(1)
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_illumina(1)

    def test_pacbio_unique_prepped_sample_idx(self):
        # PacBio: only one row per prepped_sample (no lane concept)
        self.conn.execute(
            "INSERT INTO pacbio_sample "
            "(prepped_sample_idx, barcode_id) VALUES (?, 'BC1')",
            (self.prs_idx,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO pacbio_sample "
                "(prepped_sample_idx, barcode_id) VALUES (?, 'BC2')",
                (self.prs_idx,),
            )

    def test_one_run_per_db_rejects_second_run(self):
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.conn.execute(
                "INSERT INTO processing_run "
                "(experiment_name, run_date, instrument_type, "
                " assay_type_idx, platform_idx) "
                "VALUES ('exp2', '2025-01-02', 'Unknown', 1, 1)"
            )
        self.assertIn("at most one processing_run", str(ctx.exception))


class TestMultiLanePopulate(unittest.TestCase):
    """Populate-path tests via parse_omnibus + populate_db."""

    def _parse_and_populate(self, csv_name):
        # Run the legacy parse → populate pipeline against an in-memory DB
        conn = create_db(":memory:")
        section_formats = get_section_formats(conn)
        sections = parse_omnibus(str(DATA_DIR / csv_name), section_formats)
        populate_db(conn, sections)
        return conn

    def test_populate_collapses_lane_splits(self):
        conn = self._parse_and_populate("good_multilane_synthetic.csv")

        # 5 CSV rows; samples 1 and 2 are split across lanes 1 and 2;
        # sample 3 is on lane 1 only.  Expect 3 prepped_samples and
        # 5 illumina_sample rows.
        prs_count = conn.execute("SELECT COUNT(*) FROM prepped_sample").fetchone()[0]
        ils_count = conn.execute("SELECT COUNT(*) FROM illumina_sample").fetchone()[0]
        self.assertEqual(prs_count, 3)
        self.assertEqual(ils_count, 5)

        # Lane-split rows share prepped_sample_idx; samples 1 and 2 each
        # have two ils rows on distinct lanes
        rows = conn.execute(
            "SELECT prepped_sample_idx, lane FROM illumina_sample "
            "ORDER BY prepped_sample_idx, lane"
        ).fetchall()
        # prs 1 → lanes 1, 2; prs 2 → lanes 1, 2; prs 3 → lane 1
        self.assertEqual(rows, [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1)])

        conn.close()

    def test_populate_rejects_pertube_conflict(self):
        with self.assertRaises(ValueError) as ctx:
            self._parse_and_populate("bad_multilane_pertube_conflict.csv")
        # Error names the offending column (mass_syndna_input_ng) and
        # identifies the lane-split group
        msg = str(ctx.exception)
        self.assertIn("mass_syndna_input_ng", msg)
        self.assertIn("disagree", msg)


if __name__ == "__main__":
    unittest.main()
