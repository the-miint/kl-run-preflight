"""Tests for the slim amplicon surface: the amplicon_sample table (one Golay
barcode per prepped_sample), input_sample.matrix_tube_id, and the KatharoSeq
control helpers. The flat prep-template round-trip and barcode roster are covered
in test_amplicon_flat.py.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from run_preflight import (
    KatharoseqSampleInfo,
    add_katharoseq_sample,
    create_db,
    get_katharoseq_sample_info,
)

from ._helpers import (
    open_db,
    seed_amplicon_sample,
    seed_input_sample,
    seed_plate,
    seed_processing_run,
    seed_project,
    seed_sample_chain,
)


class TestAmpliconSample(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        create_db(self.db_path).close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_one_amplicon_sample_per_prepped_sample(self):
        """prepped_sample_idx is amplicon_sample's primary key: a prepped_sample
        carries exactly one Golay barcode."""
        with open_db(self.db_path) as conn:
            proj = seed_project(conn, bioproject_accession="PRJNA1")
            plate = seed_plate(conn, proj)
            run = seed_processing_run(conn, platform_idx=1)
            _, _, prs = seed_sample_chain(conn, plate, proj, run, sample_name="S1")
            seed_amplicon_sample(conn, prs, barcode="AGCACGAGCCTA")
            with self.assertRaises(sqlite3.IntegrityError):
                seed_amplicon_sample(conn, prs, barcode="TTTTGGGGCCCC")

    def test_matrix_tube_id_is_nullable_on_input_sample(self):
        """matrix_tube_id lives on input_sample and is nullable."""
        with open_db(self.db_path) as conn:
            proj = seed_project(conn, bioproject_accession="PRJNA1")
            plate = seed_plate(conn, proj)
            ins = seed_input_sample(conn, plate, proj, sample_name="S1")
            # nullable: an input_sample without a tube id is fine
            self.assertIsNone(
                conn.execute(
                    "SELECT matrix_tube_id FROM input_sample WHERE input_sample_idx = ?",
                    (ins,),
                ).fetchone()[0]
            )
            conn.execute(
                "UPDATE input_sample SET matrix_tube_id = ? WHERE input_sample_idx = ?",
                ("TUBE0001", ins),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT matrix_tube_id FROM input_sample WHERE input_sample_idx = ?",
                    (ins,),
                ).fetchone()[0],
                "TUBE0001",
            )


class TestKatharoseq(unittest.TestCase):
    """KatharoSeq is explicit and workable: a positive control records its cell
    count and reads back. The tube barcode is not here (it moved to
    input_sample.matrix_tube_id)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        create_db(self.db_path).close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _seed_katharoseq_controls(self, conn, sample_names: list[str]) -> list[int]:
        """Seed one KatharoSeq control per name on one plate; return their idxs."""
        proj = seed_project(conn, bioproject_accession="PRJNA1")
        plate = seed_plate(conn, proj)
        control_idxs = [
            seed_input_sample(
                conn,
                plate,
                None,
                sample_name=name,
                sample_type_name="katharoseq_cells_positive_control",
            )
            for name in sample_names
        ]
        return control_idxs

    def test_add_and_get_katharoseq_sample(self):
        with open_db(self.db_path) as conn:
            (ins,) = self._seed_katharoseq_controls(conn, ["kath1"])
            add_katharoseq_sample(conn, ins, number_of_cells=7000, rack_id="R1")
            conn.commit()

        with open_db(self.db_path) as conn:
            self.assertEqual(
                get_katharoseq_sample_info(conn),
                [KatharoseqSampleInfo(ins, "kath1", 7000, "R1")],
            )

    def test_add_katharoseq_sample_raise_rack_id_type_err(self):
        with open_db(self.db_path) as conn:
            (ins,) = self._seed_katharoseq_controls(conn, ["kath1"])
            with self.assertRaisesRegex(TypeError, "rack_id must be a str, not int"):
                add_katharoseq_sample(conn, ins, number_of_cells=10, rack_id=5)

    def test_add_katharoseq_sample_raise_no_metric_err(self):
        # A row with neither metric asserts nothing, so it is refused
        with open_db(self.db_path) as conn:
            (ins,) = self._seed_katharoseq_controls(conn, ["kath1"])
            for rack_id in (None, " "):
                with (
                    self.subTest(rack_id=rack_id),
                    self.assertRaisesRegex(ValueError, "number_of_cells or rack_id"),
                ):
                    add_katharoseq_sample(
                        conn, ins, number_of_cells=None, rack_id=rack_id
                    )

    def test_add_katharoseq_sample_strips_rack_id(self):
        # A padded rack_id is stripped; a blank one is stored as None
        with open_db(self.db_path) as conn:
            kath_idxs = self._seed_katharoseq_controls(conn, ["kath1", "kath2"])
            add_katharoseq_sample(
                conn, kath_idxs[0], number_of_cells=10, rack_id=" R1\u200b"
            )
            add_katharoseq_sample(conn, kath_idxs[1], number_of_cells=20, rack_id=" ")
            conn.commit()
            infos = get_katharoseq_sample_info(conn)
        expected = [
            KatharoseqSampleInfo(kath_idxs[0], "kath1", 10, "R1"),
            KatharoseqSampleInfo(kath_idxs[1], "kath2", 20, None),
        ]
        self.assertEqual(infos, expected)


if __name__ == "__main__":
    unittest.main()
