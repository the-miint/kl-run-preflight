"""Tests for db.get_illumina_sample_info."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import unittest

from run_preflight.db import create_db, get_illumina_sample_info
from run_preflight.updates import set_biosample_accession

from . import _helpers


@contextlib.contextmanager
def _open(db_path: str):
    """Open a raw connection to *db_path* with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _seed_run_skeleton(
    conn: sqlite3.Connection,
    *,
    primary_bioproject: str | None = "PRJNA001",
) -> tuple[int, int, int]:
    """Insert one project + plate + run; return (project_idx, plate_idx, run_idx)."""
    project_idx = _helpers.seed_project(
        conn,
        project_name="proj1",
        external_project_id="1",
        bioproject_accession=primary_bioproject,
    )
    plate_idx = _helpers.seed_plate(conn, project_idx)
    run_idx = _helpers.seed_processing_run(conn)
    conn.commit()
    return project_idx, plate_idx, run_idx


def _seed_illumina(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int | None,
    run_idx: int,
    *,
    sample_name: str,
    well: str,
    sample_type_name: str = "standard",
) -> tuple[int, int]:
    """Seed sample chain + illumina_sample; return (input_sample_idx, ils_idx)."""
    ins_idx, _cs_idx, prs_idx = _helpers.seed_sample_chain(
        conn,
        plate_idx,
        project_idx,
        run_idx,
        sample_name=sample_name,
        sample_type_name=sample_type_name,
        well=well,
    )
    ils_idx = _helpers.seed_illumina_sample(
        conn,
        prs_idx,
        i7_index_id=f"i7_{sample_name}",
        i5_index_id=f"i5_{sample_name}",
    )
    conn.commit()
    return ins_idx, ils_idx


class TestGetIlluminaSampleInfo(unittest.TestCase):
    """End-to-end tests for get_illumina_sample_info."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        # Fresh DB per test; each test seeds its own projects/plates/samples.
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_illumina_sample_info_non_control_single_project(self):
        # Non-control on a single-project plate: primary = own; secondary = []
        with _open(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with _open(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN001", "PRJNA001", [])])

    def test_get_illumina_sample_info_non_control_diff_project_from_plate_primary(self):
        # Non-control whose own project is not the plate primary: primary
        # bioproject = sample's own (not the plate primary's)
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            other_proj = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession="PRJNA002",
            )
            _, ils_idx = _seed_illumina(
                conn, plate, other_proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with _open(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN001", "PRJNA002", [])])

    def test_get_illumina_sample_info_control_single_project(self):
        # Control on a single-project plate: primary = plate primary; secondary = []
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                None,
                run,
                sample_name="blank1",
                well="A1",
                sample_type_name="extraction_blank",
            )
            set_biosample_accession(conn, "blank1", "SAMN_BLK")

        with _open(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN_BLK", "PRJNA001", [])])

    def test_get_illumina_sample_info_control_multi_project(self):
        # Control on a multi-project plate: secondary lists every non-primary
        # plate project's bioproject_accession sorted by the accession value.
        # The two secondary projects are seeded so that project_idx order
        # (proj2 then proj3) does NOT match accession order (PRJNA111 then
        # PRJNA999), proving the function sorts by accession not project_idx.
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession="PRJNA999",
            )
            proj3 = _helpers.seed_project(
                conn,
                project_name="proj3",
                external_project_id="3",
                bioproject_accession="PRJNA111",
            )
            # Non-control samples from proj2 and proj3 land on the same plate
            # so input_plate_projects picks them up as secondaries
            _helpers.seed_input_sample(conn, plate, proj2, sample_name="S2")
            _helpers.seed_input_sample(conn, plate, proj3, sample_name="S3")
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                None,
                run,
                sample_name="blank1",
                well="A1",
                sample_type_name="extraction_blank",
            )
            set_biosample_accession(conn, "blank1", "SAMN_BLK")

        with _open(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(
            result,
            [(ils_idx, "SAMN_BLK", "PRJNA001", ["PRJNA111", "PRJNA999"])],
        )

    def test_get_illumina_sample_info_missing_biosample_accession(self):
        # Skipping set_biosample_accession leaves biosample NULL → raises
        with _open(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("missing required accession", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("biosample_accession", msg)

    def test_get_illumina_sample_info_missing_multiple_biosample_accessions(self):
        # Two rows missing biosample_accession both appear in the summary
        with _open(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils1 = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            _, ils2 = _seed_illumina(
                conn, plate, proj, run, sample_name="S2", well="A2"
            )

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("missing required accession", msg)
        self.assertIn("biosample_accession", msg)
        self.assertIn(f"illumina_sample_idx={ils1}", msg)
        self.assertIn(f"illumina_sample_idx={ils2}", msg)

    def test_get_illumina_sample_info_missing_own_bioproject_accession(self):
        # Non-control whose own project has NULL bioproject_accession
        with _open(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn, primary_bioproject=None)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("missing required accession", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("primary_bioproject_accession", msg)

    def test_get_illumina_sample_info_missing_primary_bioproject_for_control(self):
        # Control inherits via plate primary; missing primary bioproject errors
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn, primary_bioproject=None)
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                None,
                run,
                sample_name="blank1",
                well="A1",
                sample_type_name="extraction_blank",
            )
            set_biosample_accession(conn, "blank1", "SAMN_BLK")

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("missing required accession", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("primary_bioproject_accession", msg)

    def test_get_illumina_sample_info_missing_secondary_bioproject_for_control(self):
        # Control on multi-project plate where one secondary has NULL bioproject
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession=None,
            )
            _helpers.seed_input_sample(conn, plate, proj2, sample_name="S2")
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                None,
                run,
                sample_name="blank1",
                well="A1",
                sample_type_name="extraction_blank",
            )
            set_biosample_accession(conn, "blank1", "SAMN_BLK")

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("missing required accession", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("secondary_bioproject_accessions", msg)

    def test_get_illumina_sample_info_invariant_standard_null_project(self):
        # Standard sample_type with NULL project_idx violates the pairing
        with _open(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                None,
                run,
                sample_name="bad1",
                well="A1",
                sample_type_name="standard",
            )

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("invariant violation", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("standard sample_type with NULL project_idx", msg)

    def test_get_illumina_sample_info_invariant_control_with_project(self):
        # Control sample_type with non-NULL project_idx violates the pairing
        with _open(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn,
                plate,
                proj,
                run,
                sample_name="bad1",
                well="A1",
                sample_type_name="extraction_blank",
            )

        with _open(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn("invariant violation", msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("non-standard sample_type with non-NULL project_idx", msg)


if __name__ == "__main__":
    unittest.main()
