"""Tests for db.get_illumina_sample_info."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from pathlib import Path

from typing import get_args

from run_preflight.constants import IN_MEMORY_PATH, PlatformSpecificSampleKind
from run_preflight.db import (
    ERR_CATEGORY_INVARIANT,
    ERR_CATEGORY_MISSING_ACCESSION,
    LABEL_NONSTANDARD_WITH_PROJECT,
    LABEL_STANDARD_NO_PROJECT,
    IlluminaSampleRow,
    PacbioSampleRow,
    PlatformSampleInfo,
    _has_do_not_use_token,
    create_db,
    get_illumina_sample_info,
    get_illumina_sample_rows,
    get_input_sample_project_info,
    get_pacbio_sample_info,
    get_projects_missing_external_id,
    get_run_projects,
    sample_kind_names,
)
from run_preflight.legacy.api import load_legacy_csv
from run_preflight.updates import (
    set_biosample_accession,
    set_input_sample_do_not_use,
    set_pacbio_sample_run_details,
)

from . import _helpers
from ._helpers import open_db

DATA_DIR = Path(__file__).parent / "data" / "legacy"
DO_NOT_USE_CSV = (
    DATA_DIR / "good_standard_metagv101_contains_donotuse_unsupported_roundtrip.csv"
)


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


def _seed_pacbio(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int | None,
    run_idx: int,
    *,
    sample_name: str,
    well: str,
    sample_type_name: str = "standard",
) -> tuple[int, int]:
    """Seed sample chain + pacbio_sample; return (input_sample_idx, ps_idx)."""
    ins_idx, _cs_idx, prs_idx = _helpers.seed_sample_chain(
        conn,
        plate_idx,
        project_idx,
        run_idx,
        sample_name=sample_name,
        sample_type_name=sample_type_name,
        well=well,
    )
    ps_idx = _helpers.seed_pacbio_sample(conn, prs_idx, barcode_id=f"bc_{sample_name}")
    conn.commit()
    return ins_idx, ps_idx


def _expected_illumina_row(sample_name: str) -> IlluminaSampleRow:
    """Build the IlluminaSampleRow _seed_illumina produces for *sample_name*."""
    return IlluminaSampleRow(
        f"i7_{sample_name}", "AAAA", f"i5_{sample_name}", "CCCC", None
    )


def _expected_pacbio_row(sample_name: str) -> PacbioSampleRow:
    """Build the PacbioSampleRow _seed_pacbio produces for *sample_name*."""
    return PacbioSampleRow(f"bc_{sample_name}", None, None, None, None)


class TestCreateDb(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_db_existing_file_raise_err(self):
        # The schema DDL is unguarded, so an existing file must be refused
        # by name rather than colliding partway through the script
        conn = create_db(self.db_path)
        conn.close()
        before = Path(self.db_path).read_bytes()

        with self.assertRaisesRegex(FileExistsError, r"refusing to overwrite"):
            create_db(self.db_path)
        self.assertEqual(Path(self.db_path).read_bytes(), before)

    def test_create_db_dangling_symlink_raise_err(self):
        # The guard tests the path itself, not what it resolves to: a link
        # to a not-yet-existing file would otherwise be followed and a full
        # database created through it at a place the caller never named
        link_target = Path(self.tmpdir.name) / "not_yet_there.db"
        link_path = Path(self.tmpdir.name) / "link.db"
        link_path.symlink_to(link_target)

        with self.assertRaisesRegex(FileExistsError, r"refusing to overwrite"):
            create_db(str(link_path))
        self.assertFalse(link_target.exists())


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

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ils_idx,
                    "standard",
                    "SAMN001",
                    "PRJNA001",
                    [],
                    _expected_illumina_row("S1"),
                )
            ],
        )

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

        self.assertEqual(
            default_result,
            [
                PlatformSampleInfo(
                    ils2,
                    "standard",
                    "SAMN002",
                    "PRJNA001",
                    [],
                    _expected_illumina_row("S2"),
                )
            ],
        )
        self.assertEqual(
            full_result,
            [
                PlatformSampleInfo(
                    ils1,
                    "standard",
                    "SAMN001",
                    "PRJNA001",
                    [],
                    _expected_illumina_row("S1"),
                ),
                PlatformSampleInfo(
                    ils2,
                    "standard",
                    "SAMN002",
                    "PRJNA001",
                    [],
                    _expected_illumina_row("S2"),
                ),
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

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ils_idx,
                    "standard",
                    "SAMN001",
                    "PRJNA002",
                    [],
                    _expected_illumina_row("S1"),
                )
            ],
        )

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

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ils_idx,
                    "extraction_blank",
                    "SAMN_BLK",
                    "PRJNA001",
                    [],
                    _expected_illumina_row("blank1"),
                )
            ],
        )

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
            [
                PlatformSampleInfo(
                    ils_idx,
                    "extraction_blank",
                    "SAMN_BLK",
                    "PRJNA001",
                    ["PRJNA111", "PRJNA999"],
                    _expected_illumina_row("blank1"),
                )
            ],
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


class TestGetPacbioSampleInfo(unittest.TestCase):
    """get_pacbio_sample_info wires the shared helper to pacbio_sample_idx."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_pacbio_sample_info_non_control_single_project(self):
        # Non-control on a single-project plate: primary = own; secondary = []
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ps_idx = _seed_pacbio(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")

        with open_db(self.db_path) as conn:
            result = get_pacbio_sample_info(conn)

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ps_idx,
                    "standard",
                    "SAMN001",
                    "PRJNA001",
                    [],
                    _expected_pacbio_row("S1"),
                )
            ],
        )

    def test_get_pacbio_sample_info_control_multi_project(self):
        # Control on a multi-project plate: secondary lists every non-primary
        # plate project's bioproject_accession, sorted by accession value.
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
            _helpers.seed_input_sample(conn, plate, proj2, sample_name="S2")
            _helpers.seed_input_sample(conn, plate, proj3, sample_name="S3")
            _, ps_idx = _seed_pacbio(
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
            result = get_pacbio_sample_info(conn)

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ps_idx,
                    "extraction_blank",
                    "SAMN_BLK",
                    "PRJNA001",
                    ["PRJNA111", "PRJNA999"],
                    _expected_pacbio_row("blank1"),
                )
            ],
        )

    def test_get_pacbio_sample_info_excludes_do_not_use_by_default(self):
        # One flagged do-not-use is dropped by default, returned when requested.
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            ins1, ps1 = _seed_pacbio(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            _, ps2 = _seed_pacbio(conn, plate, proj, run, sample_name="S2", well="A2")
            set_biosample_accession(conn, "S1", "SAMN001")
            set_biosample_accession(conn, "S2", "SAMN002")
            set_input_sample_do_not_use(conn, input_sample_idx=ins1)

        with open_db(self.db_path) as conn:
            default_result = get_pacbio_sample_info(conn)
            full_result = get_pacbio_sample_info(conn, include_do_not_use=True)

        self.assertEqual(
            default_result,
            [
                PlatformSampleInfo(
                    ps2,
                    "standard",
                    "SAMN002",
                    "PRJNA001",
                    [],
                    _expected_pacbio_row("S2"),
                )
            ],
        )
        self.assertEqual(
            full_result,
            [
                PlatformSampleInfo(
                    ps1,
                    "standard",
                    "SAMN001",
                    "PRJNA001",
                    [],
                    _expected_pacbio_row("S1"),
                ),
                PlatformSampleInfo(
                    ps2,
                    "standard",
                    "SAMN002",
                    "PRJNA001",
                    [],
                    _expected_pacbio_row("S2"),
                ),
            ],
        )

    def test_get_pacbio_sample_info_kind_row_carries_populated_fields(self):
        # Populated pacbio-specific columns flow through into kind_row.
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ps_idx = _seed_pacbio(
                conn, plate, proj, run, sample_name="S1", well="A1"
            )
            set_biosample_accession(conn, "S1", "SAMN001")
            set_pacbio_sample_run_details(
                conn,
                sample_name="S1",
                smrt_cell_well_sample_id="1_A01",
                movie_context_id="m84137_260702_104358_s3",
            )

        with open_db(self.db_path) as conn:
            result = get_pacbio_sample_info(conn)

        expected_row = PacbioSampleRow(
            "bc_S1", None, None, "1_A01", "m84137_260702_104358_s3"
        )
        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ps_idx, "standard", "SAMN001", "PRJNA001", [], expected_row
                )
            ],
        )

    def test_get_pacbio_sample_info_syndna_is_twisted_coerced_to_bool(self):
        # SQLite BOOLEAN stored as 1/0/NULL surfaces as Python True/False/None.
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            _, ps1 = _seed_pacbio(conn, plate, proj, run, sample_name="S1", well="A1")
            _, ps2 = _seed_pacbio(conn, plate, proj, run, sample_name="S2", well="A2")
            _, ps3 = _seed_pacbio(conn, plate, proj, run, sample_name="S3", well="A3")
            # Store the raw boolean integers directly; NULL is left on S3.
            for ps_idx, stored in ((ps1, 1), (ps2, 0)):
                conn.execute(
                    "UPDATE pacbio_sample SET syndna_is_twisted = ? "
                    "WHERE pacbio_sample_idx = ?",
                    (stored, ps_idx),
                )
            for name in ("S1", "S2", "S3"):
                set_biosample_accession(conn, name, f"SAMN_{name}")
            conn.commit()

        with open_db(self.db_path) as conn:
            result = get_pacbio_sample_info(conn)

        self.assertEqual(
            result,
            [
                PlatformSampleInfo(
                    ps1,
                    "standard",
                    "SAMN_S1",
                    "PRJNA001",
                    [],
                    PacbioSampleRow("bc_S1", None, True, None, None),
                ),
                PlatformSampleInfo(
                    ps2,
                    "standard",
                    "SAMN_S2",
                    "PRJNA001",
                    [],
                    PacbioSampleRow("bc_S2", None, False, None, None),
                ),
                PlatformSampleInfo(
                    ps3,
                    "standard",
                    "SAMN_S3",
                    "PRJNA001",
                    [],
                    PacbioSampleRow("bc_S3", None, None, None, None),
                ),
            ],
        )
        # 1 == True and 0 == False in Python, so equality above cannot prove
        # coercion; assert the concrete types of the surfaced values.
        twisted_types = [type(row.kind_row.syndna_is_twisted) for row in result]
        self.assertEqual(twisted_types, [bool, bool, type(None)])


class TestPacbioSmrtCellWellSampleIdConstraint(unittest.TestCase):
    """pacbio_sample.smrt_cell_well_sample_id CHECK accepts <1|2>_<A-D>01 and rejects everything else."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        conn = create_db(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _insert_pacbio_with_smrt_cell_well_sample_id(
        self, conn, plate, proj, run, name, smrt_cell_well_sample_id
    ):
        # Each pacbio_sample needs its own prepped_sample (UNIQUE constraint).
        _ins, _cs, prs = _helpers.seed_sample_chain(
            conn, plate, proj, run, sample_name=name, well="A1"
        )
        conn.execute(
            "INSERT INTO pacbio_sample (prepped_sample_idx, barcode_id, smrt_cell_well_sample_id) "
            "VALUES (?, 'bc', ?)",
            (prs, smrt_cell_well_sample_id),
        )

    def test_pacbio_smrt_cell_well_sample_id_valid_values_accepted(self):
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            for i, value in enumerate(
                ["1_A01", "2_A01", "1_B01", "2_C01", "1_D01", None]
            ):
                self._insert_pacbio_with_smrt_cell_well_sample_id(
                    conn, plate, proj, run, f"S{i}", value
                )
            stored = conn.execute(
                "SELECT smrt_cell_well_sample_id FROM pacbio_sample ORDER BY pacbio_sample_idx"
            ).fetchall()

        self.assertEqual(
            stored,
            [("1_A01",), ("2_A01",), ("1_B01",), ("2_C01",), ("1_D01",), (None,)],
        )

    def test_pacbio_smrt_cell_well_sample_id_invalid_values_rejected(self):
        with open_db(self.db_path) as conn:
            proj, plate, run = _seed_run_skeleton(conn)
            for i, value in enumerate(
                ["A01", "3_A01", "1_E01", "1_A02", "1_a01", "0_A01"]
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    self._insert_pacbio_with_smrt_cell_well_sample_id(
                        conn, plate, proj, run, f"S{i}", value
                    )


class TestRunPacbioSampleView(unittest.TestCase):
    """run_pacbio_sample surfaces the pacbio-specific columns."""

    def test_run_pacbio_sample_exposes_pacbio_columns(self):
        conn = create_db(IN_MEMORY_PATH)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(run_pacbio_sample)")]
        finally:
            conn.close()
        for expected in ("smrt_cell_well_sample_id", "movie_context_id"):
            self.assertIn(expected, cols)


class TestSampleKindNamingConvention(unittest.TestCase):
    """Every declared sample kind resolves to real schema objects."""

    def test_sample_kind_names_match_schema(self):
        # Guards the derive-by-convention contract: adding a kind without its
        # table/idx column fails here rather than at runtime.
        conn = create_db(IN_MEMORY_PATH)
        try:
            for kind in get_args(PlatformSpecificSampleKind):
                names = sample_kind_names(kind)
                table_row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                    (names.table,),
                ).fetchone()
                self.assertIsNotNone(table_row, f"missing table {names.table}")
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({names.table})")]
                self.assertIn(names.idx_col, cols)
        finally:
            conn.close()

    def test_sample_row_sample_kind_reports_declared_kind(self):
        # Each row class reports its own kind; the info helper relies on this
        # instead of a separately-passed kind argument.
        valid_kinds = get_args(PlatformSpecificSampleKind)
        reported = {
            PacbioSampleRow.sample_kind(),
            IlluminaSampleRow.sample_kind(),
        }
        self.assertEqual(reported, {"pacbio", "illumina"})
        self.assertTrue(reported.issubset(set(valid_kinds)))


if __name__ == "__main__":
    unittest.main()
