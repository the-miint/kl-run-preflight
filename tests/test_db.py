"""Tests for db.get_illumina_sample_info."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from run_preflight.db import (
    ERR_CATEGORY_INVARIANT,
    ERR_CATEGORY_MISSING_ACCESSION,
    LABEL_NONSTANDARD_WITH_PROJECT,
    LABEL_STANDARD_NO_PROJECT,
    create_db,
    get_illumina_sample_info,
    get_projects_missing_external_id,
)
from run_preflight.updates import set_biosample_accession

from . import _helpers
from ._helpers import open_db


def _seed_run_skeleton(
    conn: sqlite3.Connection,
    *,
    primary_ena_study_accession: str | None = "ERP001",
) -> tuple[int, int, int]:
    """Insert one project + plate + run; return (project_idx, plate_idx, run_idx)."""
    project_idx = _helpers.seed_project(
        conn,
        project_name="proj1",
        external_project_id="1",
        ena_study_accession=primary_ena_study_accession,
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
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN001", "ERP001", [])])

    def test_get_illumina_sample_info_non_control_diff_project_from_plate_primary(self):
        # Non-control whose own project is not the plate primary: primary
        # ena study accession = sample's own (not the plate primary's)
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            other_proj = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                ena_study_accession="ERP002",
            )
            _, ils_idx = _seed_illumina(
                conn, plate, other_proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN001", "ERP002", [])])

    def test_get_illumina_sample_info_control_single_project(self):
        # Control on a single-project plate: primary = plate primary; secondary = []
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN_BLK", "ERP001", [])])

    def test_get_illumina_sample_info_control_multi_project(self):
        # Control on a multi-project plate: secondary lists every non-primary
        # plate project's ena_study_accession sorted by the accession value.
        # The two secondary projects are seeded so that project_idx order
        # (proj2 then proj3) does NOT match accession order (ERP111 then
        # ERP999), proving the function sorts by accession not project_idx.
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                ena_study_accession="ERP999",
            )
            proj3 = _helpers.seed_project(
                conn,
                project_name="proj3",
                external_project_id="3",
                ena_study_accession="ERP111",
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

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(
            result,
            [(ils_idx, "SAMN_BLK", "ERP001", ["ERP111", "ERP999"])],
        )

    def test_get_illumina_sample_info_missing_biosample_accession(self):
        # Skipping set_biosample_accession leaves biosample NULL → raises
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("biosample_accession", msg)

    def test_get_illumina_sample_info_missing_multiple_biosample_accessions(self):
        # Two rows missing biosample_accession both appear in the summary
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ils1 = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            _, ils2 = _seed_illumina(
                conn, plate, proj, run, sample_name="S2", well="A2"
            )

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn("biosample_accession", msg)
        self.assertIn(f"illumina_sample_idx={ils1}", msg)
        self.assertIn(f"illumina_sample_idx={ils2}", msg)

    def test_get_illumina_sample_info_missing_own_ena_study_accession(self):
        # Non-control whose own project has NULL ena_study_accession
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn, primary_ena_study_accession=None)
            _, ils_idx = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("primary_ena_study_accession", msg)

    def test_get_illumina_sample_info_missing_primary_ena_study_accession_for_control(self):
        # Control inherits via plate primary; missing primary ena study accession errors
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn, primary_ena_study_accession=None)
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

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("primary_ena_study_accession", msg)

    def test_get_illumina_sample_info_missing_secondary_ena_study_accession_for_control(self):
        # Control on multi-project plate where one secondary has NULL ena study accession
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                ena_study_accession=None,
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

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("secondary_ena_study_accessions", msg)

    def test_get_illumina_sample_info_invariant_standard_null_project(self):
        # Standard sample_type with NULL project_idx violates the pairing
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_INVARIANT, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn(LABEL_STANDARD_NO_PROJECT, msg)

    def test_get_illumina_sample_info_invariant_control_with_project(self):
        # Control sample_type with non-NULL project_idx violates the pairing
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_INVARIANT, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn(LABEL_NONSTANDARD_WITH_PROJECT, msg)


class TestGetProjectsMissingExternalId(unittest.TestCase):
    """Tests for db.get_projects_missing_external_id."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_projects_missing_external_id_none_missing(self):
        # Every reachable project has external_project_id set
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            _helpers.seed_sample_chain(conn, plate, 1, run, sample_name="S1")
            conn.commit()
            missing = get_projects_missing_external_id(conn, run)
        self.assertEqual(missing, [])

    def test_get_projects_missing_external_id_primary_missing(self):
        # Primary plate project lacks external_project_id (row is
        # valid only because ena_study_accession is non-null)
        with open_db(self.db_path) as conn:
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
            missing = get_projects_missing_external_id(conn, run_idx)
        self.assertEqual(missing, ["proj_no_qid"])

    def test_get_projects_missing_external_id_secondary_missing(self):
        # Plate primary is fine, but a per-sample project on the same
        # plate lacks external_project_id
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn)
            secondary_idx = _helpers.seed_project(
                conn,
                project_name="proj_secondary_no_qid",
                external_project_id=None,
                ena_study_accession="ERP999",
            )
            _helpers.seed_sample_chain(
                conn, plate, secondary_idx, run, sample_name="S1"
            )
            conn.commit()
            missing = get_projects_missing_external_id(conn, run)
        self.assertEqual(missing, ["proj_secondary_no_qid"])


if __name__ == "__main__":
    unittest.main()
