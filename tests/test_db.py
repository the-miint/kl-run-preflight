"""Tests for db.get_illumina_sample_info."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from pathlib import Path

from run_preflight.db import (
    ERR_CATEGORY_INVARIANT,
    ERR_CATEGORY_MISSING_ACCESSION,
    LABEL_NONSTANDARD_WITH_PROJECT,
    LABEL_STANDARD_NO_PROJECT,
    _has_do_not_use_token,
    create_db,
    get_illumina_sample_info,
    get_illumina_sample_rows,
    get_input_sample_project_info,
    get_projects_missing_external_id,
    get_run_projects,
)
from run_preflight.legacy.api import load_legacy_csv
from run_preflight.updates import (
    set_biosample_accession,
    set_input_sample_do_not_use,
)

from . import _helpers
from ._helpers import open_db

DATA_DIR = Path(__file__).parent / "data"
DO_NOT_USE_CSV = DATA_DIR / "good_standard_metagv101_donotuse_synthetic_not_roundtrippable.csv"


def _seed_run_skeleton(
    conn: sqlite3.Connection,
    *,
    primary_bioproject_accession: str | None = "PRJNA001",
) -> tuple[int, int, int]:
    """Insert one project + plate + run; return (project_idx, plate_idx, run_idx)."""
    project_idx = _helpers.seed_project(
        conn,
        project_name="proj1",
        external_project_id="1",
        bioproject_accession=primary_bioproject_accession,
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

        self.assertEqual(result, [(ils_idx, "SAMN001", "PRJNA001", [])])

    def test_get_illumina_sample_info_excludes_do_not_use_by_default(self):
        # Two non-controls; one flagged do-not-use is dropped by default
        # and returned only when include_do_not_use is True.
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            ins1, ils1 = _seed_illumina(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            _, ils2 = _seed_illumina(
                conn, plate, proj, run, sample_name="S2", well="A2"
            )
            set_biosample_accession(conn, "S1", "SAMN001")
            set_biosample_accession(conn, "S2", "SAMN002")
            set_input_sample_do_not_use(conn, input_sample_idx=ins1)

        with open_db(self.db_path) as conn:
            default_result = get_illumina_sample_info(conn)
            full_result = get_illumina_sample_info(conn, include_do_not_use=True)

        self.assertEqual(default_result, [(ils2, "SAMN002", "PRJNA001", [])])
        self.assertEqual(
            full_result,
            [
                (ils1, "SAMN001", "PRJNA001", []),
                (ils2, "SAMN002", "PRJNA001", []),
            ],
        )

    def test_get_illumina_sample_info_non_control_diff_project_from_plate_primary(self):
        # Non-control whose own project is not the plate primary: primary
        # bioproject accession = sample's own (not the plate primary's)
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(result, [(ils_idx, "SAMN001", "PRJNA002", [])])

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

        self.assertEqual(result, [(ils_idx, "SAMN_BLK", "PRJNA001", [])])

    def test_get_illumina_sample_info_control_multi_project(self):
        # Control on a multi-project plate: secondary lists every non-primary
        # plate project's bioproject_accession sorted by the accession value.
        # The two secondary projects are seeded so that project_idx order
        # (proj2 then proj3) does NOT match accession order (PRJNA111 then
        # PRJNA999), proving the function sorts by accession not project_idx.
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            result = get_illumina_sample_info(conn)

        self.assertEqual(
            result,
            [(ils_idx, "SAMN_BLK", "PRJNA001", ["PRJNA111", "PRJNA999"])],
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

    def test_get_illumina_sample_info_missing_own_bioproject_accession(self):
        # Non-control whose own project has NULL bioproject_accession
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(
                conn, primary_bioproject_accession=None
            )
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
        self.assertIn("primary_bioproject_accession", msg)

    def test_get_illumina_sample_info_missing_primary_bioproject_accession_for_control(
        self,
    ):
        # Control inherits via plate primary; missing primary bioproject accession errors
        with open_db(self.db_path) as conn:
            _, plate, run = _seed_run_skeleton(conn, primary_bioproject_accession=None)
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
        self.assertIn("primary_bioproject_accession", msg)

    def test_get_illumina_sample_info_missing_secondary_bioproject_accession_for_control(
        self,
    ):
        # Control on multi-project plate where one secondary has NULL bioproject accession
        with open_db(self.db_path) as conn:
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

        with open_db(self.db_path) as conn:
            with self.assertRaises(ValueError) as ctx:
                get_illumina_sample_info(conn)

        msg = str(ctx.exception)
        self.assertIn(ERR_CATEGORY_MISSING_ACCESSION, msg)
        self.assertIn(f"illumina_sample_idx={ils_idx}", msg)
        self.assertIn("secondary_bioproject_accessions", msg)

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
        # valid only because bioproject_accession is non-null)
        with open_db(self.db_path) as conn:
            project_idx = _helpers.seed_project(
                conn,
                project_name="proj_no_qid",
                external_project_id=None,
                bioproject_accession="PRJNA001",
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
                bioproject_accession="PRJNA999",
            )
            _helpers.seed_sample_chain(
                conn, plate, secondary_idx, run, sample_name="S1"
            )
            conn.commit()
            missing = get_projects_missing_external_id(conn, run)
        self.assertEqual(missing, ["proj_secondary_no_qid"])


class TestGetInputSampleProjectInfo(unittest.TestCase):
    """Tests for db.get_input_sample_project_info."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_input_sample_project_info(self):
        # One plate (primary proj1) carrying: a standard sample on its own
        # project, a standard sample on a secondary project, a control
        # (NULL project, inherits the plate primary's QiitaID), and a
        # replicated sample (two prepped rows) that must collapse to one row.
        with open_db(self.db_path) as conn:
            proj1, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession="PRJNA002",
            )
            _helpers.seed_sample_chain(
                conn, plate, proj1, run, sample_name="S1", well="A1"
            )
            _helpers.seed_sample_chain(
                conn, plate, proj2, run, sample_name="S2", well="A2"
            )
            _helpers.seed_sample_chain(
                conn,
                plate,
                None,
                run,
                sample_name="blank1",
                sample_type_name="extraction_blank",
                well="A3",
            )
            _, rep_cs, _ = _helpers.seed_sample_chain(
                conn, plate, proj1, run, sample_name="R1", well="A4"
            )
            # Second prepped row makes R1 a replicate; it must not duplicate R1
            _helpers.seed_prepped_sample(conn, rep_cs, well="A5", sample_name="R1.A5")
            conn.commit()

        with open_db(self.db_path) as conn:
            result = get_input_sample_project_info(conn)

        self.assertEqual(
            result,
            [
                ("R1", "1", False),
                ("S1", "1", False),
                ("S2", "2", False),
                ("blank1", "1", True),
            ],
        )


class TestGetRunProjects(unittest.TestCase):
    """Tests for db.get_run_projects."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_run_projects(self):
        # Primary (proj1) plus a per-sample secondary (proj2) on the plate
        with open_db(self.db_path) as conn:
            proj1, plate, run = _seed_run_skeleton(conn)
            proj2 = _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession="PRJNA002",
            )
            _helpers.seed_sample_chain(conn, plate, proj1, run, sample_name="S1")
            _helpers.seed_sample_chain(
                conn, plate, proj2, run, sample_name="S2", well="A2"
            )
            conn.commit()
            result = get_run_projects(conn, run)

        self.assertEqual(result, [("proj1", "1"), ("proj2", "2")])

    def test_get_run_projects_null_external_id(self):
        # A reachable project with no QiitaID surfaces with None
        with open_db(self.db_path) as conn:
            project_idx = _helpers.seed_project(
                conn,
                project_name="proj_no_qid",
                external_project_id=None,
                bioproject_accession="PRJNA001",
            )
            plate_idx = _helpers.seed_plate(conn, project_idx)
            run_idx = _helpers.seed_processing_run(conn)
            _helpers.seed_sample_chain(
                conn, plate_idx, project_idx, run_idx, sample_name="S1"
            )
            conn.commit()
            result = get_run_projects(conn, run_idx)

        self.assertEqual(result, [("proj_no_qid", None)])


class TestHasDoNotUseToken(unittest.TestCase):
    """Unit tests for db._has_do_not_use_token."""

    def test__has_do_not_use_token_mid_segment(self):
        self.assertTrue(_has_do_not_use_token("15902.donotuse.DBS0715.FE.E10"))

    def test__has_do_not_use_token_leading_and_trailing(self):
        self.assertTrue(_has_do_not_use_token("donotuse.sample"))
        self.assertTrue(_has_do_not_use_token("sample.donotuse"))

    def test__has_do_not_use_token_case_insensitive(self):
        self.assertTrue(_has_do_not_use_token("Foo.DoNotUse.Bar"))

    def test__has_do_not_use_token_substring_not_delimited(self):
        # Token must be a whole dot-delimited segment, not a substring
        self.assertFalse(_has_do_not_use_token("donotusenow.sample"))
        self.assertFalse(_has_do_not_use_token("x.predonotuse.y"))

    def test__has_do_not_use_token_absent(self):
        self.assertFalse(_has_do_not_use_token("15902.DBS0715.FE.E10"))

    def test__has_do_not_use_token_empty_or_none(self):
        self.assertFalse(_has_do_not_use_token(""))
        self.assertFalse(_has_do_not_use_token(None))


class TestDoNotUseIngest(unittest.TestCase):
    """End-to-end do-not-use detection from a legacy v101 replicate CSV.

    The fixture covers: an input-level flag (orig_name has the token), a
    prep-level flag on one replicate only, and a clean sample.
    """

    def setUp(self):
        self.conn = load_legacy_csv(str(DO_NOT_USE_CSV))

    def tearDown(self):
        self.conn.close()

    def test_populate_db_sets_input_sample_do_not_use(self):
        result = self.conn.execute(
            "SELECT sample_name, do_not_use FROM input_sample ORDER BY sample_name"
        ).fetchall()
        self.assertEqual(
            result,
            [("SX.donotuse.A", 1), ("SY.B", 0), ("SZ.C", 0)],
        )

    def test_populate_db_sets_prepped_sample_do_not_use(self):
        result = self.conn.execute(
            "SELECT COALESCE(prs.sample_name, ins.sample_name), prs.do_not_use "
            "FROM prepped_sample prs "
            "JOIN compression_sample cs "
            "  ON prs.compression_sample_idx = cs.compression_sample_idx "
            "JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx "
            "ORDER BY 1"
        ).fetchall()
        self.assertEqual(
            result,
            [
                ("SX.A.A1", None),
                ("SY.B.A5", None),
                ("SY.donotuse.B.A3", 1),
                ("SZ.C.A7", None),
            ],
        )

    def test_populate_db_effective_do_not_use_hard_floor(self):
        # SX.A.A1's prep flag is NULL (inherit) but its input flag is 1,
        # so the effective flag is 1 (the input flag is a hard floor).
        result = self.conn.execute(
            "SELECT sample_name, do_not_use FROM run_illumina_sample ORDER BY sample_name"
        ).fetchall()
        self.assertEqual(
            result,
            [
                ("SX.A.A1", 1),
                ("SY.B.A5", 0),
                ("SY.donotuse.B.A3", 1),
                ("SZ.C.A7", 0),
            ],
        )

    def test_get_illumina_sample_rows_excludes_do_not_use_by_default(self):
        names = [row[5] for row in get_illumina_sample_rows(self.conn)]
        self.assertEqual(names, ["SY.B.A5", "SZ.C.A7"])

    def test_get_illumina_sample_rows_includes_do_not_use_when_requested(self):
        names = [
            row[5]
            for row in get_illumina_sample_rows(self.conn, include_do_not_use=True)
        ]
        self.assertEqual(names, ["SX.A.A1", "SY.donotuse.B.A3", "SY.B.A5", "SZ.C.A7"])

    def test_get_input_sample_project_info_excludes_only_fully_flagged_samples(self):
        # SX is dropped (its sole prep is flagged); SY survives because one
        # of its two replicates is not flagged.
        result = get_input_sample_project_info(self.conn)
        self.assertEqual(
            result,
            [("SY.B", "12345", False), ("SZ.C", "12345", False)],
        )

    def test_get_input_sample_project_info_includes_do_not_use_when_requested(self):
        result = get_input_sample_project_info(self.conn, include_do_not_use=True)
        self.assertEqual(
            result,
            [
                ("SX.donotuse.A", "12345", False),
                ("SY.B", "12345", False),
                ("SZ.C", "12345", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
