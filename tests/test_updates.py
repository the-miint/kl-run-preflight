"""Tests for updates.py: set_biosample_accession, set_bioproject_accession,
update_lane, set_illumina_run_setting."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import pytest

from run_preflight.db import create_db
from run_preflight.updates import (
    _set_illumina_run_column,
    set_bioproject_accession,
    set_biosample_accession,
    set_illumina_run_setting,
    set_input_sample_do_not_use,
    set_prepped_sample_do_not_use,
    update_lane,
)

from . import _helpers
from ._helpers import open_db


def _setup_run(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert a project, plate, and run; return (project_idx, plate_idx, run_idx)."""
    project_idx, plate_idx = _helpers.seed_project_and_plate(conn)
    run_idx = _helpers.seed_processing_run(conn)
    conn.commit()
    return project_idx, plate_idx, run_idx


def _add_sample(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int,
    run_idx: int,
    sample_name: str,
    well: str,
    prs_name: str | None = None,
) -> tuple[int, int]:
    """Insert input + compression + prepped sample; return (ins_idx, prs_idx)."""
    ins_idx, _cs_idx, prs_idx = _helpers.seed_sample_chain(
        conn,
        plate_idx,
        project_idx,
        run_idx,
        sample_name=sample_name,
        well=well,
        prs_name=prs_name,
    )
    conn.commit()
    return ins_idx, prs_idx


def _add_illumina_row(
    conn: sqlite3.Connection,
    prs_idx: int,
    lane: int | None = None,
) -> int:
    """Insert one illumina_sample row; return its surrogate id."""
    ils_idx = _helpers.seed_illumina_sample(conn, prs_idx, lane=lane)
    conn.commit()
    return ils_idx


def _add_illumina_run(
    conn: sqlite3.Connection,
    run_idx: int,
    *,
    mask_short_reads: str | None = None,
    override_cycles: str | None = None,
) -> None:
    """Insert the matching illumina_run config row and commit."""
    _helpers.seed_illumina_run_config(
        conn,
        run_idx,
        mask_short_reads=mask_short_reads,
        override_cycles=override_cycles,
    )
    conn.commit()


class _UpdatesTestBase(unittest.TestCase):
    """Shared tempfile-backed DB setup for update-function tests."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        # Seed the run and close the connection so each test reopens
        # a fresh one against a DB at the latest schema version.
        conn = create_db(self.db_path)
        try:
            self.project_idx, self.plate_idx, self.run_idx = _setup_run(conn)
        finally:
            conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()


class TestSetBiosampleAccession(_UpdatesTestBase):
    def test_set_biosample_accession_non_replicate(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with open_db(self.db_path) as conn:
            set_biosample_accession(conn, "S1", "SAMN001", reason="initial")

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN001",))

    def test_set_biosample_accession_replicate_alias(self):
        # Replicate alias resolves through prs.sample_name to the
        # underlying input_sample
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with open_db(self.db_path) as conn:
            set_biosample_accession(conn, "S1.A1", "SAMN002")

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN002",))

    def test_set_biosample_accession_replicates_share_one_accession(self):
        # Two prepped_samples (replicates) of one input_sample share
        # the input_sample's accession
        with open_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO input_sample "
                "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
                "VALUES ('S1', ?, ?, 1)",
                (self.plate_idx, self.project_idx),
            )
            ins_idx = cur.lastrowid
            cur.execute(
                "INSERT INTO compression_sample "
                "(run_idx, input_sample_idx, compression_well) "
                "VALUES (?, ?, 'A1')",
                (self.run_idx, ins_idx),
            )
            cs_idx = cur.lastrowid
            cur.execute(
                "INSERT INTO prepped_sample "
                "(compression_sample_idx, prepped_well, sample_name) "
                "VALUES (?, 'A1', 'S1.A1')",
                (cs_idx,),
            )
            cur.execute(
                "INSERT INTO prepped_sample "
                "(compression_sample_idx, prepped_well, sample_name) "
                "VALUES (?, 'B2', 'S1.B2')",
                (cs_idx,),
            )
            conn.commit()

        # Update via one alias, the other alias shows the same accession
        with open_db(self.db_path) as conn:
            set_biosample_accession(conn, "S1.B2", "SAMN003")

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN003",))

    def test_set_biosample_accession_ambiguous(self):
        # Two distinct input_samples produce the same effective Sample_Name
        with open_db(self.db_path) as conn:
            _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S2",
                "B2",
                prs_name="S1",
            )

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="ambiguous"),
        ):
            set_biosample_accession(conn, "S1", "SAMN004")

    def test_set_biosample_accession_missing(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No input_sample matches"),
        ):
            set_biosample_accession(conn, "nonexistent", "SAMN005")

    def test_set_biosample_accession_audit(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        # Two successive sets so the second log entry captures the
        # first call's value as old_value
        with open_db(self.db_path) as conn:
            set_biosample_accession(conn, "S1", "SAMN006", reason="initial")
            set_biosample_accession(conn, "S1", "SAMN007", reason="correction")

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT table_name, row_idx, column_name, "
                " old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            )
            expected = [
                (
                    "input_sample",
                    ins_idx,
                    "biosample_accession",
                    None,
                    "SAMN006",
                    "initial",
                ),
                (
                    "input_sample",
                    ins_idx,
                    "biosample_accession",
                    "SAMN006",
                    "SAMN007",
                    "correction",
                ),
            ]
            self.assertEqual(cur.fetchall(), expected)


class TestSetBioprojectAccession(_UpdatesTestBase):
    # The shared setUp seeds proj1 with external_project_id='1';
    # tests below use that default project unless noted otherwise.

    def _read_bioproject_accession(self) -> str | None:
        """Return project.bioproject_accession for the default test project."""
        with open_db(self.db_path) as conn:
            (val,) = conn.execute(
                "SELECT bioproject_accession FROM project WHERE project_idx = ?",
                (self.project_idx,),
            ).fetchone()
            return val

    def test_set_bioproject_accession_by_project_name(self):
        with open_db(self.db_path) as conn:
            set_bioproject_accession(
                conn, "PRJNA001", project_name="proj1", reason="initial"
            )
        self.assertEqual(self._read_bioproject_accession(), "PRJNA001")

    def test_set_bioproject_accession_by_external_project_id(self):
        with open_db(self.db_path) as conn:
            set_bioproject_accession(conn, "PRJNA002", external_project_id="1")
        self.assertEqual(self._read_bioproject_accession(), "PRJNA002")

    def test_set_bioproject_accession_clear_when_external_present(self):
        # external_project_id='1' keeps an identifier on the project; clearing is allowed
        with open_db(self.db_path) as conn:
            set_bioproject_accession(conn, "PRJNA003", project_name="proj1")
            set_bioproject_accession(conn, None, project_name="proj1")
        self.assertIsNone(self._read_bioproject_accession())

    def test_set_bioproject_accession_clear_rejected_without_other_identifier(self):
        # Drop external_project_id so clearing bioproject_accession would leave no identifier
        with open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE project SET bioproject_accession = 'PRJNA004', "
                "external_project_id = NULL WHERE project_idx = ?",
                (self.project_idx,),
            )
            conn.commit()

        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="CHECK"),
        ):
            set_bioproject_accession(conn, None, project_name="proj1")

    def test_set_bioproject_accession_no_key(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one"),
        ):
            set_bioproject_accession(conn, "PRJNA005")

    def test_set_bioproject_accession_both_keys(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one"),
        ):
            set_bioproject_accession(
                conn,
                "PRJNA006",
                project_name="proj1",
                external_project_id="1",
            )

    def test_set_bioproject_accession_missing(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No project matches"),
        ):
            set_bioproject_accession(conn, "PRJNA007", project_name="nonexistent")

    def test_set_bioproject_accession_ambiguous_external_id(self):
        # Insert a second project sharing external_project_id with proj1
        with open_db(self.db_path) as conn:
            _helpers.seed_project(conn, project_name="proj2", external_project_id="1")
            conn.commit()

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="ambiguous"),
        ):
            set_bioproject_accession(conn, "PRJNA008", external_project_id="1")

    def test_set_bioproject_accession_audit(self):
        # Two successive sets so the second log entry captures the
        # first call's value as old_value
        with open_db(self.db_path) as conn:
            set_bioproject_accession(
                conn, "PRJNA009", project_name="proj1", reason="initial"
            )
            set_bioproject_accession(
                conn, "PRJNA010", project_name="proj1", reason="correction"
            )

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT table_name, row_idx, column_name, "
                " old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            )
            expected = [
                (
                    "project",
                    self.project_idx,
                    "bioproject_accession",
                    None,
                    "PRJNA009",
                    "initial",
                ),
                (
                    "project",
                    self.project_idx,
                    "bioproject_accession",
                    "PRJNA009",
                    "PRJNA010",
                    "correction",
                ),
            ]
            self.assertEqual(cur.fetchall(), expected)


class TestUpdateLane(_UpdatesTestBase):
    def test_update_lane_null_to_value(self):
        # All rows uniformly NULL, then assigned a lane
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=None)
            _add_illumina_row(conn, prs2, lane=None)

        with open_db(self.db_path) as conn:
            n = update_lane(conn, "illumina", from_lane=None, to_lane=2)
        self.assertEqual(n, 2)
        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT lane FROM illumina_sample ORDER BY illumina_sample_idx"
            )
            self.assertEqual([r[0] for r in cur.fetchall()], [2, 2])

    def test_update_lane_value_to_value(self):
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs2, lane=1)

        with open_db(self.db_path) as conn:
            n = update_lane(conn, "illumina", from_lane=1, to_lane=3)
        self.assertEqual(n, 2)
        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT lane FROM illumina_sample ORDER BY illumina_sample_idx"
            )
            self.assertEqual([r[0] for r in cur.fetchall()], [3, 3])

    def test_update_lane_collision(self):
        # Multi-lane fan-out: one prepped_sample at both lane 1 and lane 2
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs1, lane=2)

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="already have a row at"),
        ):
            update_lane(conn, "illumina", from_lane=1, to_lane=2)

    def test_update_lane_uniformity_violation(self):
        # Setting some rows to NULL while others remain non-NULL is mixed
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs2, lane=2)

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="uniformity violation"),
        ):
            update_lane(conn, "illumina", from_lane=1, to_lane=None)

    def test_update_lane_unsupported_platform(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Unsupported platform"),
        ):
            update_lane(conn, "pacbio", from_lane=1, to_lane=2)

    def test_update_lane_audit(self):
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            i1 = _add_illumina_row(conn, prs1, lane=1)
            i2 = _add_illumina_row(conn, prs2, lane=1)

        with open_db(self.db_path) as conn:
            update_lane(conn, "illumina", from_lane=1, to_lane=4, reason="reload")

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT table_name, row_idx, column_name, "
                " old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            )
            expected = [
                ("illumina_sample", i1, "lane", "1", "4", "reload"),
                ("illumina_sample", i2, "lane", "1", "4", "reload"),
            ]
            self.assertEqual(cur.fetchall(), expected)

    def test_update_lane_tellseq(self):
        with open_db(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tellseq_sample (prepped_sample_idx, barcode_id, lane) "
                "VALUES (?, 'BC1', NULL)",
                (prs1,),
            )
            conn.commit()

        with open_db(self.db_path) as conn:
            n = update_lane(conn, "tellseq", from_lane=None, to_lane=1)
        self.assertEqual(n, 1)
        with open_db(self.db_path) as conn:
            cur = conn.execute("SELECT lane FROM tellseq_sample")
            self.assertEqual(cur.fetchone(), (1,))


class TestSetIlluminaSettings(_UpdatesTestBase):
    """set_illumina_run_setting on illumina_run."""

    # mask_short_reads is the chosen probe column for the behavior-matrix
    # tests below; the per-setting tests then prove dispatch is correct
    # for each Literal member (and that the other column is untouched).

    def _read_column(self, column: str) -> str | None:
        """Return illumina_run.<column> for the test's processing_run."""
        with open_db(self.db_path) as conn:
            (val,) = conn.execute(
                f"SELECT {column} FROM illumina_run WHERE run_idx = ?",
                (self.run_idx,),
            ).fetchone()
            return val

    def test__set_illumina_run_column_from_null(self):
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx)

        with open_db(self.db_path) as conn:
            _set_illumina_run_column(conn, "mask_short_reads", "R1:Y*N,R2:Y*N", None)
        self.assertEqual(self._read_column("mask_short_reads"), "R1:Y*N,R2:Y*N")

    def test__set_illumina_run_column_overwrite(self):
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx, mask_short_reads="R1:Y*N,R2:Y*N")

        with open_db(self.db_path) as conn:
            _set_illumina_run_column(conn, "mask_short_reads", "R1:N*,R2:N*", None)
        self.assertEqual(self._read_column("mask_short_reads"), "R1:N*,R2:N*")

    def test__set_illumina_run_column_clear(self):
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx, mask_short_reads="R1:Y*N,R2:Y*N")

        with open_db(self.db_path) as conn:
            _set_illumina_run_column(conn, "mask_short_reads", None, None)
        self.assertIsNone(self._read_column("mask_short_reads"))

    def test__set_illumina_run_column_audit(self):
        # Two successive sets so the second captures the first as old_value
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx)

        with open_db(self.db_path) as conn:
            _set_illumina_run_column(
                conn, "mask_short_reads", "R1:Y*N,R2:Y*N", "initial"
            )
            _set_illumina_run_column(
                conn, "mask_short_reads", "R1:N*,R2:N*", "correction"
            )

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT table_name, row_idx, column_name, "
                " old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            )
            expected = [
                (
                    "illumina_run",
                    self.run_idx,
                    "mask_short_reads",
                    None,
                    "R1:Y*N,R2:Y*N",
                    "initial",
                ),
                (
                    "illumina_run",
                    self.run_idx,
                    "mask_short_reads",
                    "R1:Y*N,R2:Y*N",
                    "R1:N*,R2:N*",
                    "correction",
                ),
            ]
            self.assertEqual(cur.fetchall(), expected)

    def test_set_illumina_run_setting_mask_short_reads(
        self,
    ):  # same-pattern-ok: per-setting smoke test
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx)

        with open_db(self.db_path) as conn:
            set_illumina_run_setting(conn, "mask_short_reads", "R1:Y*N,R2:Y*N")
        self.assertEqual(
            (
                self._read_column("mask_short_reads"),
                self._read_column("override_cycles"),
            ),
            ("R1:Y*N,R2:Y*N", None),
        )

    def test_set_illumina_run_setting_override_cycles(
        self,
    ):  # same-pattern-ok: per-setting smoke test
        with open_db(self.db_path) as conn:
            _add_illumina_run(conn, self.run_idx)

        with open_db(self.db_path) as conn:
            set_illumina_run_setting(conn, "override_cycles", "Y151;I8;I8;Y151")
        self.assertEqual(
            (
                self._read_column("mask_short_reads"),
                self._read_column("override_cycles"),
            ),
            (None, "Y151;I8;I8;Y151"),
        )

    def test_set_illumina_run_setting_rejects_unknown_setting(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Unsupported illumina_run setting"),
        ):
            set_illumina_run_setting(conn, "no_such_setting", "x")  # type: ignore[arg-type]


class TestInputSampleCheck(_UpdatesTestBase):
    """input_sample requires sample_name OR biosample_accession non-null."""

    # SQL prefix shared by all four cases; varies only in identity values
    _INSERT_SQL = (
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, "
        " sample_type_idx, biosample_accession) "
        "VALUES (?, ?, ?, 1, ?)"
    )

    def _insert(self, conn, sample_name, biosample_accession):
        """Insert an input_sample with the given identity values."""
        cur = conn.execute(
            self._INSERT_SQL,
            (sample_name, self.plate_idx, self.project_idx, biosample_accession),
        )
        conn.commit()
        return cur.lastrowid

    def _read_identity(self, conn, ins_idx):
        """Return (sample_name, biosample_accession) for *ins_idx*."""
        cur = conn.execute(
            "SELECT sample_name, biosample_accession FROM input_sample "
            "WHERE input_sample_idx = ?",
            (ins_idx,),
        )
        return cur.fetchone()

    def test_input_sample_check_rejects_both_null(self):
        # Both identity columns NULL must violate the CHECK
        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="CHECK"),
        ):
            self._insert(conn, None, None)

    def test_input_sample_check_accepts_sample_name_only(self):
        # sample_name alone satisfies the CHECK
        with open_db(self.db_path) as conn:
            ins_idx = self._insert(conn, "S1", None)
            self.assertEqual(self._read_identity(conn, ins_idx), ("S1", None))

    def test_input_sample_check_accepts_biosample_accession_only(self):
        # biosample_accession alone satisfies the CHECK
        with open_db(self.db_path) as conn:
            ins_idx = self._insert(conn, None, "SAMN100")
            self.assertEqual(self._read_identity(conn, ins_idx), (None, "SAMN100"))

    def test_input_sample_check_accepts_both_non_null(self):
        # Both non-null satisfies the CHECK
        with open_db(self.db_path) as conn:
            ins_idx = self._insert(conn, "S1", "SAMN101")
            self.assertEqual(self._read_identity(conn, ins_idx), ("S1", "SAMN101"))


class TestEmptyStringRejection(_UpdatesTestBase):
    """Every update function rejects an empty-string argument.

    None remains the explicit "clear" path; empty strings (a common
    silent-no-match hazard from upstream CSV parsing or shell expansion)
    are rejected with a ValueError naming the offending parameter so
    callers can distinguish "no value supplied" from "value is empty."
    """

    def _seed_sample(self, conn):
        """Insert one input_sample with sample_name 'S1'."""
        ins_idx, _ = _add_sample(
            conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
        )
        return ins_idx

    def _seed_illumina_run(self, conn):
        """Insert the matching illumina_run config row."""
        _add_illumina_run(conn, self.run_idx)

    def test_set_biosample_accession_rejects_empty_sample_name(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="sample_name must be a non-empty string"),
        ):
            self._seed_sample(conn)
            set_biosample_accession(conn, "", "SAMN001")

    def test_set_biosample_accession_rejects_empty_accession(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="accession must not be empty"),
        ):
            self._seed_sample(conn)
            set_biosample_accession(conn, "S1", "")

    def test_set_bioproject_accession_rejects_empty_project_name(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one of"),
        ):
            set_bioproject_accession(conn, "PRJNA001", project_name="")

    def test_set_bioproject_accession_rejects_empty_external_project_id(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one of"),
        ):
            set_bioproject_accession(conn, "PRJNA001", external_project_id="")

    def test_set_bioproject_accession_rejects_empty_accession(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="accession must not be empty"),
        ):
            set_bioproject_accession(conn, "", project_name="proj1")

    def test_set_illumina_run_setting_rejects_empty_value_mask_short_reads(
        self,
    ):  # same-pattern-ok: per-setting empty-value rejection
        with open_db(self.db_path) as conn:
            self._seed_illumina_run(conn)

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="value must not be empty"),
        ):
            set_illumina_run_setting(conn, "mask_short_reads", "")

    def test_set_illumina_run_setting_rejects_empty_value_override_cycles(
        self,
    ):  # same-pattern-ok: per-setting empty-value rejection
        with open_db(self.db_path) as conn:
            self._seed_illumina_run(conn)

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="value must not be empty"),
        ):
            set_illumina_run_setting(conn, "override_cycles", "")

    def test_set_input_sample_do_not_use_rejects_empty_biosample_accession(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="biosample_accession must be a non-empty"),
        ):
            set_input_sample_do_not_use(conn, biosample_accession="")


class TestSetInputSampleDoNotUse(_UpdatesTestBase):
    def _do_not_use(self, conn, ins_idx: int):
        """Return the do_not_use value stored on *ins_idx*."""
        (value,) = conn.execute(
            "SELECT do_not_use FROM input_sample WHERE input_sample_idx = ?",
            (ins_idx,),
        ).fetchone()
        return value

    def test_set_input_sample_do_not_use_by_idx(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with open_db(self.db_path) as conn:
            set_input_sample_do_not_use(conn, input_sample_idx=ins_idx)

        with open_db(self.db_path) as conn:
            self.assertEqual(self._do_not_use(conn, ins_idx), 1)

    def test_set_input_sample_do_not_use_clear(self):
        # value=False clears a previously set flag back to 0
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with open_db(self.db_path) as conn:
            set_input_sample_do_not_use(conn, input_sample_idx=ins_idx, value=True)
            set_input_sample_do_not_use(conn, input_sample_idx=ins_idx, value=False)

        with open_db(self.db_path) as conn:
            self.assertEqual(self._do_not_use(conn, ins_idx), 0)

    def test_set_input_sample_do_not_use_by_biosample_sets_all_matches(self):
        # Two distinct input_samples sharing one biosample_accession are
        # both flagged when keyed by that accession.
        with open_db(self.db_path) as conn:
            ins1, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            ins2, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "A2"
            )
            conn.execute(
                "UPDATE input_sample SET biosample_accession = 'SAMN_SHARED' "
                "WHERE input_sample_idx IN (?, ?)",
                (ins1, ins2),
            )
            conn.commit()

        with open_db(self.db_path) as conn:
            set_input_sample_do_not_use(conn, biosample_accession="SAMN_SHARED")

        with open_db(self.db_path) as conn:
            self.assertEqual(
                (self._do_not_use(conn, ins1), self._do_not_use(conn, ins2)), (1, 1)
            )

    def test_set_input_sample_do_not_use_requires_exactly_one_key(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        # Neither key supplied
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one of"),
        ):
            set_input_sample_do_not_use(conn)

        # Both keys supplied
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one of"),
        ):
            set_input_sample_do_not_use(
                conn, input_sample_idx=ins_idx, biosample_accession="SAMN001"
            )

    def test_set_input_sample_do_not_use_no_match(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No input_sample matches"),
        ):
            set_input_sample_do_not_use(conn, input_sample_idx=999)

    def test_set_input_sample_do_not_use_audit(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with open_db(self.db_path) as conn:
            set_input_sample_do_not_use(
                conn, input_sample_idx=ins_idx, value=True, reason="flagged"
            )

        with open_db(self.db_path) as conn:
            cur = conn.execute(
                "SELECT table_name, row_idx, column_name, "
                " old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            )
            self.assertEqual(
                cur.fetchall(),
                [("input_sample", ins_idx, "do_not_use", "0", "1", "flagged")],
            )


class TestSetPreppedSampleDoNotUse(_UpdatesTestBase):
    def _do_not_use(self, conn, prs_idx: int):
        """Return the do_not_use value stored on *prs_idx*."""
        (value,) = conn.execute(
            "SELECT do_not_use FROM prepped_sample WHERE prepped_sample_idx = ?",
            (prs_idx,),
        ).fetchone()
        return value

    def test_set_prepped_sample_do_not_use_override(self):
        # Input sample left unflagged; the prep-level override flags one replicate
        with open_db(self.db_path) as conn:
            _, prs_idx = _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with open_db(self.db_path) as conn:
            set_prepped_sample_do_not_use(conn, prs_idx)

        with open_db(self.db_path) as conn:
            self.assertEqual(self._do_not_use(conn, prs_idx), 1)

    def test_set_prepped_sample_do_not_use_clear(self):
        # value=None clears the override back to NULL (inherit input)
        with open_db(self.db_path) as conn:
            _, prs_idx = _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with open_db(self.db_path) as conn:
            set_prepped_sample_do_not_use(conn, prs_idx, value=True)
            set_prepped_sample_do_not_use(conn, prs_idx, value=None)

        with open_db(self.db_path) as conn:
            self.assertIsNone(self._do_not_use(conn, prs_idx))

    def test_set_prepped_sample_do_not_use_rejects_false(self):
        # False is not a valid prep-level state; only True / None are
        with open_db(self.db_path) as conn:
            _, prs_idx = _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="False is not supported"),
        ):
            set_prepped_sample_do_not_use(conn, prs_idx, value=False)

    def test_set_prepped_sample_do_not_use_no_match(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No prepped_sample matches"),
        ):
            set_prepped_sample_do_not_use(conn, 999)
