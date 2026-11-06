"""Tests for updates.py."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import unittest
from collections.abc import Callable
from typing import get_args

import pytest

from run_preflight.constants import (
    DB_COL_EXTERNAL_PROJECT_ID,
    DB_COL_INPUT_SAMPLE_IDX,
    DB_COL_PROJECT_IDX,
    DB_COL_PROJECT_NAME,
    DB_COL_SAMPLE_NAME,
    TABLE_INPUT_SAMPLE,
    TABLE_PROJECT,
    InputSampleIdentifierKind,
    ProjectIdentifierKind,
    ProjectLookupKey,
)
from run_preflight.db import create_db
from run_preflight.updates import (
    UNCHANGED,
    _require_exactly_one_key,
    _require_nonempty,
    _require_nonempty_or_none,
    _set_illumina_run_column,
    set_bioproject_accession,
    set_biosample_accession,
    set_illumina_run_setting,
    set_input_sample_do_not_use,
    set_input_sample_identifier,
    set_pacbio_sample_run_details,
    set_prepped_sample_do_not_use,
    set_project_identifier,
    update_lane,
)

from . import _helpers
from ._helpers import open_db


def _add_illumina_row(
    conn: sqlite3.Connection,
    prs_idx: int,
    lane: int | None = None,
) -> int:
    """Insert one illumina_sample row; return its surrogate id."""
    ils_idx = _helpers.seed_illumina_sample(conn, prs_idx, lane=lane)
    conn.commit()
    return ils_idx


def _add_pacbio_sample(
    conn: sqlite3.Connection,
    prs_idx: int,
    smrt_cell_well_sample_id: str | None = None,
) -> int:
    """Insert one pacbio_sample row; return pacbio_sample_idx."""
    ps_idx = _helpers.seed_pacbio_sample(
        conn, prs_idx, smrt_cell_well_sample_id=smrt_cell_well_sample_id
    )
    conn.commit()
    return ps_idx


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
            self.project_idx, self.plate_idx, self.run_idx = _helpers.seed_run_skeleton(
                conn
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()


def _read_change_log(conn: sqlite3.Connection) -> list[tuple]:
    """Return every change_log row's audited fields, in insertion order."""
    cur = conn.execute(
        "SELECT table_name, row_idx, column_name, old_value, new_value, reason "
        "FROM change_log ORDER BY change_idx"
    )
    rows = cur.fetchall()
    return rows


class _IdentifierSetterTestBase(_UpdatesTestBase):
    """Shared checks for a setter that writes one identifier column per call.

    Subclasses set the class attributes declared below; *key* arguments
    below are the setter's row-lookup keyword arguments.
    """

    _TABLE: str
    _PK_COL: str
    _SETTER: Callable[..., None]
    _IDENTIFIER_KINDS: tuple[str, ...]

    def _read_identifiers(self, row_idx: int) -> tuple:
        """Return *row_idx*'s settable identifiers, in _IDENTIFIER_KINDS order."""
        cols = ", ".join(self._IDENTIFIER_KINDS)
        with open_db(self.db_path) as conn:
            cur = conn.execute(
                f"SELECT {cols} FROM {self._TABLE} WHERE {self._PK_COL} = ?",
                (row_idx,),
            )
            identifiers = cur.fetchone()
        return identifiers

    def _assert_each_kind_lands(self, row_idx: int, key: dict[str, str]) -> None:
        """Set every identifier kind and check each lands in its own column."""
        with open_db(self.db_path) as conn:
            for kind in self._IDENTIFIER_KINDS:
                self._SETTER(conn, kind, f"val_{kind}", **key)

        expected = tuple(f"val_{kind}" for kind in self._IDENTIFIER_KINDS)
        self.assertEqual(self._read_identifiers(row_idx), expected)

    def _assert_audited(self, row_idx: int, key: dict[str, str]) -> None:
        """Set every kind twice and check each change_log row and reason."""
        # The second set of each kind captures the first one's value as old_value
        with open_db(self.db_path) as conn:
            for kind in self._IDENTIFIER_KINDS:
                for value, reason in ((f"{kind}_1", "initial"), (f"{kind}_2", "fix")):
                    self._SETTER(conn, kind, value, **key, reason=reason)
            log_rows = _read_change_log(conn)

        expected = [
            row
            for kind in self._IDENTIFIER_KINDS
            for row in (
                (self._TABLE, row_idx, kind, None, f"{kind}_1", "initial"),
                (self._TABLE, row_idx, kind, f"{kind}_1", f"{kind}_2", "fix"),
            )
        ]
        self.assertEqual(log_rows, expected)


class TestSetInputSampleIdentifier(_IdentifierSetterTestBase):
    _TABLE = TABLE_INPUT_SAMPLE
    _PK_COL = DB_COL_INPUT_SAMPLE_IDX
    _SETTER = staticmethod(set_input_sample_identifier)
    _IDENTIFIER_KINDS = get_args(InputSampleIdentifierKind)

    def _add_default_sample(self) -> int:
        """Insert sample S1 and return its input_sample_idx."""
        with open_db(self.db_path) as conn:
            ins_idx, _ = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
        return ins_idx

    def test_set_input_sample_identifier(self):
        ins_idx = self._add_default_sample()
        self._assert_each_kind_lands(ins_idx, {"sample_name": "S1"})

    def test_set_input_sample_identifier_replicate_alias(self):
        # Replicate alias resolves through prs.sample_name to the
        # underlying input_sample
        with open_db(self.db_path) as conn:
            ins_idx, _ = _helpers.seed_committed_sample_chain(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with open_db(self.db_path) as conn:
            set_input_sample_identifier(
                conn, "biosample_export_entity_id", "QB2", sample_name="S1.A1"
            )

        self.assertEqual(self._read_identifiers(ins_idx), (None, "QB2", None))

    def test_set_input_sample_identifier_replicates_share_one_value(self):
        # Two prepped_samples (replicates) of one input_sample share
        # the input_sample's identifier
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

        # Update via one alias, the other alias shows the same value
        with open_db(self.db_path) as conn:
            set_input_sample_identifier(
                conn, "biosample_accession", "SAMN003", sample_name="S1.B2"
            )

        self.assertEqual(self._read_identifiers(ins_idx), ("SAMN003", None, None))

    def test_set_input_sample_identifier_ambiguous(self):
        # Two distinct input_samples produce the same effective Sample_Name
        with open_db(self.db_path) as conn:
            _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _helpers.seed_committed_sample_chain(
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
            set_input_sample_identifier(
                conn, "biosample_accession", "SAMN004", sample_name="S1"
            )

    def test_set_input_sample_identifier_missing(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No input_sample matches"),
        ):
            set_input_sample_identifier(
                conn, "biosample_accession", "SAMN005", sample_name="nonexistent"
            )

    def test_set_input_sample_identifier_unknown_kind(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Unsupported input_sample identifier"),
        ):
            set_input_sample_identifier(
                conn,
                "sample_name",  # type: ignore[arg-type]
                "S9",
                sample_name="S1",
            )

    def test_set_input_sample_identifier_audit(self):
        ins_idx = self._add_default_sample()
        self._assert_audited(ins_idx, {"sample_name": "S1"})

    def test_set_biosample_accession_deprecated(self):
        # The deprecated wrapper warns, then forwards its key, value, and reason
        ins_idx = self._add_default_sample()
        with (
            open_db(self.db_path) as conn,
            pytest.warns(DeprecationWarning, match="set_input_sample_identifier"),
        ):
            set_biosample_accession(conn, "S1", "SAMN001", reason="initial")
            log_rows = _read_change_log(conn)

        actual = (self._read_identifiers(ins_idx), log_rows)
        expected = (
            ("SAMN001", None, None),
            [
                (
                    TABLE_INPUT_SAMPLE,
                    ins_idx,
                    "biosample_accession",
                    None,
                    "SAMN001",
                    "initial",
                )
            ],
        )
        self.assertEqual(actual, expected)


class TestSetProjectIdentifier(_IdentifierSetterTestBase):
    # The shared setUp seeds proj1 with external_project_id='1';
    # tests below use that default project unless noted otherwise.
    _TABLE = TABLE_PROJECT
    _PK_COL = DB_COL_PROJECT_IDX
    _SETTER = staticmethod(set_project_identifier)
    _IDENTIFIER_KINDS = get_args(ProjectIdentifierKind)

    def test_set_project_identifier(self):
        self._assert_each_kind_lands(self.project_idx, {"project_name": "proj1"})

    def test_set_project_identifier_by_external_project_id(self):
        with open_db(self.db_path) as conn:
            set_project_identifier(
                conn, "study_export_entity_id", "QS2", external_project_id="1"
            )
        self.assertEqual(self._read_identifiers(self.project_idx), (None, "QS2"))

    def test_set_project_identifier_clear_when_other_identifier_present(self):
        # external_project_id='1' keeps an identifier on the project; clearing is allowed
        with open_db(self.db_path) as conn:
            set_project_identifier(
                conn, "bioproject_accession", "PRJNA003", project_name="proj1"
            )
            set_project_identifier(
                conn, "bioproject_accession", None, project_name="proj1"
            )
        self.assertEqual(self._read_identifiers(self.project_idx), (None, None))

    def test_set_project_identifier_clear_rejected_without_other_identifier(self):
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
            set_project_identifier(
                conn, "bioproject_accession", None, project_name="proj1"
            )

    def test_set_project_identifier_duplicate_value(self):
        # Every project identifier is unique, so another project's value is refused
        with open_db(self.db_path) as conn:
            _helpers.seed_project(
                conn,
                project_name="proj2",
                external_project_id="2",
                bioproject_accession="PRJNA_TAKEN",
            )
            conn.commit()

        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="UNIQUE"),
        ):
            set_project_identifier(
                conn, "bioproject_accession", "PRJNA_TAKEN", project_name="proj1"
            )

    def test_set_project_identifier_no_key(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(
                ValueError,
                match="^Exactly one of project_name or external_project_id must be supplied$",  # noqa: E501
            ),
        ):
            set_project_identifier(conn, "bioproject_accession", "PRJNA005")

    def test_set_project_identifier_both_keys(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one"),
        ):
            set_project_identifier(
                conn,
                "bioproject_accession",
                "PRJNA006",
                project_name="proj1",
                external_project_id="1",
            )

    def test_set_project_identifier_missing(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No project matches"),
        ):
            set_project_identifier(
                conn, "bioproject_accession", "PRJNA007", project_name="nonexistent"
            )

    def test_set_project_identifier_unknown_kind(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Unsupported project identifier"),
        ):
            set_project_identifier(
                conn,
                "external_project_id",  # type: ignore[arg-type]
                "9",
                project_name="proj1",
            )

    def test_set_project_identifier_audit(self):
        self._assert_audited(self.project_idx, {"project_name": "proj1"})

    def test_set_bioproject_accession_deprecated(self):
        # The deprecated wrapper warns, then forwards each key, the value, and
        # the reason; one call per key exercises both
        with (
            open_db(self.db_path) as conn,
            pytest.warns(DeprecationWarning, match="set_project_identifier"),
        ):
            set_bioproject_accession(
                conn, "PRJNA001", project_name="proj1", reason="initial"
            )
            set_bioproject_accession(
                conn, "PRJNA002", external_project_id="1", reason="fix"
            )
            log_rows = _read_change_log(conn)

        actual = (self._read_identifiers(self.project_idx), log_rows)
        expected = (
            ("PRJNA002", None),
            [
                (
                    TABLE_PROJECT,
                    self.project_idx,
                    "bioproject_accession",
                    None,
                    "PRJNA001",
                    "initial",
                ),
                (
                    TABLE_PROJECT,
                    self.project_idx,
                    "bioproject_accession",
                    "PRJNA001",
                    "PRJNA002",
                    "fix",
                ),
            ],
        )
        self.assertEqual(actual, expected)


class TestUpdateLane(_UpdatesTestBase):
    def test_update_lane_null_to_value(self):
        # All rows uniformly NULL, then assigned a lane
        with open_db(self.db_path) as conn:
            _, prs1 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _helpers.seed_committed_sample_chain(
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
            _, prs1 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _helpers.seed_committed_sample_chain(
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
            _, prs1 = _helpers.seed_committed_sample_chain(
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
            _, prs1 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _helpers.seed_committed_sample_chain(
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
            pytest.raises(ValueError, match="Unsupported sample kind"),
        ):
            update_lane(conn, "pacbio", from_lane=1, to_lane=2)

    def test_update_lane_audit(self):
        with open_db(self.db_path) as conn:
            _, prs1 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _helpers.seed_committed_sample_chain(
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
            _, prs1 = _helpers.seed_committed_sample_chain(
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


class _IdentityCheckTestBase(_UpdatesTestBase):
    """Insert and read back rows of one table that vary in identity columns.

    Subclasses set the class attributes declared below and supply the
    remaining NOT NULL values a new row needs.
    """

    _TABLE: str
    _PK_COL: str
    _IDENTITY_COLS: tuple[str, ...]

    def _required_values(self) -> dict[str, object]:
        """Return the non-identity column values every new row needs."""
        raise NotImplementedError

    def _insert(self, conn, values: dict[str, object]) -> int:
        """Insert a row from the required values overlaid with *values*."""
        row = {**self._required_values(), **values}
        cols = ", ".join(row)
        placeholders = ", ".join("?" * len(row))
        cur = conn.execute(
            f"INSERT INTO {self._TABLE} ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        conn.commit()
        return cur.lastrowid

    def _read_identity(self, conn, row_idx: int) -> tuple:
        """Return the identity columns for *row_idx*, in _IDENTITY_COLS order."""
        cols = ", ".join(self._IDENTITY_COLS)
        cur = conn.execute(
            f"SELECT {cols} FROM {self._TABLE} WHERE {self._PK_COL} = ?",
            (row_idx,),
        )
        identity = cur.fetchone()
        return identity


class TestInputSampleCheck(_IdentityCheckTestBase):
    """input_sample identity CHECK, and the non-unique export ID."""

    _TABLE = TABLE_INPUT_SAMPLE
    _PK_COL = DB_COL_INPUT_SAMPLE_IDX
    _IDENTITY_COLS = (DB_COL_SAMPLE_NAME, *get_args(InputSampleIdentifierKind))

    def _required_values(self) -> dict[str, object]:
        return {
            "input_plate_idx": self.plate_idx,
            DB_COL_PROJECT_IDX: self.project_idx,
            "sample_type_idx": 1,
        }

    def test_input_sample_check_rejects_all_null(self):
        # Every identity column NULL must violate the CHECK
        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="CHECK"),
        ):
            self._insert(conn, {})

    def test_input_sample_check_accepts_any_one_identity_column(self):
        # Each identity column alone satisfies the CHECK
        for position, col in enumerate(self._IDENTITY_COLS):
            with self.subTest(col=col), open_db(self.db_path) as conn:
                ins_idx = self._insert(conn, {col: f"only_{col}"})
                expected = tuple(
                    f"only_{col}" if i == position else None
                    for i in range(len(self._IDENTITY_COLS))
                )
                self.assertEqual(self._read_identity(conn, ins_idx), expected)

    def test_input_sample_check_accepts_all_non_null(self):
        with open_db(self.db_path) as conn:
            all_values = {col: f"all_{col}" for col in self._IDENTITY_COLS}
            ins_idx = self._insert(conn, all_values)
            self.assertEqual(
                self._read_identity(conn, ins_idx), tuple(all_values.values())
            )

    def test_input_sample_biosample_export_entity_id_not_unique(self):
        # One biosample may appear as several input_samples
        with open_db(self.db_path) as conn:
            idxs = [
                self._insert(
                    conn,
                    {
                        DB_COL_SAMPLE_NAME: name,
                        "biosample_export_entity_id": "QB1",
                    },
                )
                for name in ("S1", "S2")
            ]
            identities = [self._read_identity(conn, idx) for idx in idxs]
            self.assertEqual(
                identities, [("S1", None, "QB1", None), ("S2", None, "QB1", None)]
            )


class TestProjectCheck(_IdentityCheckTestBase):
    """project identity CHECK and identifier uniqueness."""

    _TABLE = TABLE_PROJECT
    _PK_COL = DB_COL_PROJECT_IDX
    _IDENTITY_COLS = (DB_COL_EXTERNAL_PROJECT_ID, *get_args(ProjectIdentifierKind))

    def _required_values(self) -> dict[str, object]:
        return {
            "library_construction_protocol": "proto",
            "experiment_design_description": "desc",
        }

    def test_project_check_rejects_all_null(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="CHECK"),
        ):
            self._insert(conn, {DB_COL_PROJECT_NAME: "p_none"})

    def test_project_check_accepts_study_export_entity_id_only(self):
        with open_db(self.db_path) as conn:
            project_idx = self._insert(
                conn,
                {DB_COL_PROJECT_NAME: "p_qs", "study_export_entity_id": "QS1"},
            )
            self.assertEqual(
                self._read_identity(conn, project_idx), (None, None, "QS1")
            )

    def test_project_identity_columns_unique(self):
        # A second project may not reuse any one of the first's identifiers
        for col in self._IDENTITY_COLS:
            with self.subTest(col=col), open_db(self.db_path) as conn:
                shared = {col: f"dup_{col}"}
                self._insert(conn, {DB_COL_PROJECT_NAME: f"p_first_{col}", **shared})
                with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                    self._insert(
                        conn, {DB_COL_PROJECT_NAME: f"p_second_{col}", **shared}
                    )


class TestEmptyStringRejection(_UpdatesTestBase):
    """Every update function rejects an empty-string argument.

    None remains the explicit "clear" path; empty strings (a common
    silent-no-match hazard from upstream CSV parsing or shell expansion)
    are rejected with a ValueError naming the offending parameter so
    callers can distinguish "no value supplied" from "value is empty."
    """

    def _seed_sample(self, conn):
        """Insert one input_sample with sample_name 'S1'."""
        ins_idx, _ = _helpers.seed_committed_sample_chain(
            conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
        )
        return ins_idx

    def _seed_illumina_run(self, conn):
        """Insert the matching illumina_run config row."""
        _add_illumina_run(conn, self.run_idx)

    def test_set_input_sample_identifier_rejects_empty_sample_name(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="sample_name must not be empty"),
        ):
            self._seed_sample(conn)
            set_input_sample_identifier(
                conn, "biosample_accession", "SAMN001", sample_name=""
            )

    def test_set_input_sample_identifier_rejects_empty_value(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(
                ValueError, match="^value must not be empty or whitespace-only$"
            ),
        ):
            self._seed_sample(conn)
            set_input_sample_identifier(
                conn, "biosample_accession", "", sample_name="S1"
            )

    def test_set_project_identifier_rejects_empty_project_name(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="project_name must not be empty"),
        ):
            set_project_identifier(
                conn, "bioproject_accession", "PRJNA001", project_name=""
            )

    def test_set_project_identifier_rejects_empty_external_project_id(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="external_project_id must not be empty"),
        ):
            set_project_identifier(
                conn, "bioproject_accession", "PRJNA001", external_project_id=""
            )

    def test_set_project_identifier_rejects_empty_key_beside_real_key(self):
        # An empty key still counts as supplied, so it is not silently ignored
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="Exactly one of"),
        ):
            set_project_identifier(
                conn,
                "bioproject_accession",
                "PRJNA001",
                project_name="",
                external_project_id="1",
            )

    def test_set_project_identifier_rejects_empty_value(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="value must not be empty"),
        ):
            set_project_identifier(
                conn, "bioproject_accession", "", project_name="proj1"
            )

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
            pytest.raises(ValueError, match="biosample_accession must not be empty"),
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
            ins_idx, _ = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with open_db(self.db_path) as conn:
            set_input_sample_do_not_use(conn, input_sample_idx=ins_idx)

        with open_db(self.db_path) as conn:
            self.assertEqual(self._do_not_use(conn, ins_idx), 1)

    def test_set_input_sample_do_not_use_clear(self):
        # value=False clears a previously set flag back to 0
        with open_db(self.db_path) as conn:
            ins_idx, _ = _helpers.seed_committed_sample_chain(
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
            ins1, _ = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            ins2, _ = _helpers.seed_committed_sample_chain(
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
            ins_idx, _ = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        # Neither key supplied
        with (
            open_db(self.db_path) as conn,
            pytest.raises(
                ValueError,
                match="^Exactly one of input_sample_idx or biosample_accession must be supplied$",  # noqa: E501
            ),
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
            ins_idx, _ = _helpers.seed_committed_sample_chain(
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
            _, prs_idx = _helpers.seed_committed_sample_chain(
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
            _, prs_idx = _helpers.seed_committed_sample_chain(
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

    def test_set_prepped_sample_do_not_use_no_match(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No prepped_sample matches"),
        ):
            set_prepped_sample_do_not_use(conn, 999)


class TestSetPacbioSampleRunDetails(_UpdatesTestBase):
    def _seed_one(self, name="S1", well="A1", smrt_cell_well_sample_id=None):
        """Seed one sample chain + pacbio_sample; return pacbio_sample_idx."""
        with open_db(self.db_path) as conn:
            _ins, prs = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, name, well
            )
            ps_idx = _add_pacbio_sample(conn, prs, smrt_cell_well_sample_id)
        return ps_idx

    def _read(self, ps_idx):
        """Return (smrt_cell_well_sample_id, movie_context_id) for a row."""
        with open_db(self.db_path) as conn:
            return conn.execute(
                "SELECT smrt_cell_well_sample_id, movie_context_id "
                "FROM pacbio_sample WHERE pacbio_sample_idx = ?",
                (ps_idx,),
            ).fetchone()

    def test_set_pacbio_sample_run_details_by_idx_both_fields(self):
        ps_idx = self._seed_one()
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn,
                pacbio_sample_idx=ps_idx,
                smrt_cell_well_sample_id="1_A01",
                movie_context_id="m84",
            )
        self.assertEqual(self._read(ps_idx), ("1_A01", "m84"))

    def test_set_pacbio_sample_run_details_by_sample_name(self):
        ps_idx = self._seed_one(name="SN1")
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn, sample_name="SN1", movie_context_id="m84"
            )
        self.assertEqual(self._read(ps_idx), (None, "m84"))

    def test_set_pacbio_sample_run_details_single_field_leaves_other(self):
        ps_idx = self._seed_one(smrt_cell_well_sample_id="1_A01")
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn, pacbio_sample_idx=ps_idx, movie_context_id="m84"
            )
        self.assertEqual(self._read(ps_idx), ("1_A01", "m84"))

    def test_set_pacbio_sample_run_details_unchanged_is_noop_for_field(self):
        ps_idx = self._seed_one(smrt_cell_well_sample_id="1_A01")
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn,
                pacbio_sample_idx=ps_idx,
                smrt_cell_well_sample_id=UNCHANGED,
                movie_context_id="m84",
            )
        self.assertEqual(self._read(ps_idx), ("1_A01", "m84"))

    def test_set_pacbio_sample_run_details_clear_with_none(self):
        ps_idx = self._seed_one(smrt_cell_well_sample_id="1_A01")
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn, pacbio_sample_idx=ps_idx, smrt_cell_well_sample_id=None
            )
        self.assertEqual(self._read(ps_idx), (None, None))

    def test_set_pacbio_sample_run_details_invalid_smrt_cell_raises(self):
        ps_idx = self._seed_one()
        with (
            open_db(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError),
        ):
            set_pacbio_sample_run_details(
                conn, pacbio_sample_idx=ps_idx, smrt_cell_well_sample_id="9_Z99"
            )

    def test_set_pacbio_sample_run_details_ambiguous_name_raises(self):
        # Two input_samples share an effective Sample_Name -> two pacbio rows
        with open_db(self.db_path) as conn:
            _i1, prs1 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "DUP", "A1"
            )
            _i2, prs2 = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "DUP", "A2"
            )
            _add_pacbio_sample(conn, prs1)
            _add_pacbio_sample(conn, prs2)
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="ambiguous"),
        ):
            set_pacbio_sample_run_details(
                conn, sample_name="DUP", movie_context_id="m84"
            )

    def test_set_pacbio_sample_run_details_no_match_raises(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="No pacbio_sample matches"),
        ):
            set_pacbio_sample_run_details(
                conn, pacbio_sample_idx=999, movie_context_id="m84"
            )

    def test_set_pacbio_sample_run_details_requires_one_identifier(self):
        ps_idx = self._seed_one()
        expected_msg = (
            "^Exactly one of sample_name or pacbio_sample_idx must be supplied$"
        )
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match=expected_msg),
        ):
            set_pacbio_sample_run_details(conn, movie_context_id="m84")
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match=expected_msg),
        ):
            set_pacbio_sample_run_details(
                conn,
                sample_name="S1",
                pacbio_sample_idx=ps_idx,
                movie_context_id="m84",
            )

    def test_set_pacbio_sample_run_details_requires_a_field(self):
        ps_idx = self._seed_one()
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="At least one of"),
        ):
            set_pacbio_sample_run_details(conn, pacbio_sample_idx=ps_idx)

    def test_set_pacbio_sample_run_details_rejects_empty_string(self):
        ps_idx = self._seed_one()
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="must not be empty"),
        ):
            set_pacbio_sample_run_details(
                conn, pacbio_sample_idx=ps_idx, movie_context_id=""
            )

    def test_set_pacbio_sample_run_details_audit_log(self):
        ps_idx = self._seed_one(smrt_cell_well_sample_id="1_A01")
        with open_db(self.db_path) as conn:
            set_pacbio_sample_run_details(
                conn,
                pacbio_sample_idx=ps_idx,
                smrt_cell_well_sample_id="2_B01",
                movie_context_id="m84",
                reason="loaded",
            )
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT table_name, row_idx, column_name, old_value, new_value, reason "
                "FROM change_log ORDER BY change_idx"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (
                    "pacbio_sample",
                    ps_idx,
                    "smrt_cell_well_sample_id",
                    "1_A01",
                    "2_B01",
                    "loaded",
                ),
                ("pacbio_sample", ps_idx, "movie_context_id", None, "m84", "loaded"),
            ],
        )


class TestRequireExactlyOneKey(unittest.TestCase):
    def test__require_exactly_one_key(self):
        # The sole supplied pair is returned
        result = _require_exactly_one_key(
            (("project_name", None), ("external_project_id", "1111")),
            ProjectLookupKey,
            "project lookup key",
        )
        self.assertEqual(result, ("external_project_id", "1111"))

    def test__require_exactly_one_key_raise_key_err(self):
        # A supplied column outside the lookup-key Literal is rejected
        with pytest.raises(
            ValueError, match="^Unsupported project lookup key 'contact_email'"
        ):
            _require_exactly_one_key(
                (("project_name", None), ("contact_email", "x")),
                ProjectLookupKey,
                "project lookup key",
            )


class TestRequireNonempty(unittest.TestCase):
    def test__require_nonempty(self):
        # Surrounding whitespace is stripped; nothing left is rejected
        self.assertEqual(_require_nonempty(" X\t", "key"), "X")
        for blank in ("", "  ", "\t", None):
            with (
                self.subTest(blank=blank),
                pytest.raises(ValueError, match="key must not be empty"),
            ):
                _require_nonempty(blank, "key")

    def test__require_nonempty_or_none(self):
        # None passes through; whitespace is stripped; nothing left is rejected
        results = (
            _require_nonempty_or_none(None, "value"),
            _require_nonempty_or_none(" X\t", "value"),
        )
        self.assertEqual(results, (None, "X"))
        for blank in ("", "  ", "\t"):
            with (
                self.subTest(blank=blank),
                pytest.raises(ValueError, match="value must not be empty"),
            ):
                _require_nonempty_or_none(blank, "value")


class TestWhitespaceHandling(_UpdatesTestBase):
    """Setters strip surrounding whitespace and reject whitespace-only strings."""

    def test_set_project_identifier_strips_value_key_and_reason(self):
        with open_db(self.db_path) as conn:
            set_project_identifier(
                conn,
                "study_export_entity_id",
                " 42 ",
                project_name=" proj1 ",
                reason=" fixed ",
            )
            log_rows = _read_change_log(conn)
        expected = [
            ("project", self.project_idx, "study_export_entity_id", None, "42", "fixed")
        ]
        self.assertEqual(log_rows, expected)

    def test_set_input_sample_identifier_strips_sample_name(self):
        with open_db(self.db_path) as conn:
            ins_idx, _ = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            set_input_sample_identifier(
                conn, "matrix_tube_id", "\t0363 ", sample_name=" S1 "
            )
            log_rows = _read_change_log(conn)
        expected = [("input_sample", ins_idx, "matrix_tube_id", None, "0363", None)]
        self.assertEqual(log_rows, expected)

    def test_set_pacbio_sample_run_details_strips_sample_name(self):
        with open_db(self.db_path) as conn:
            _ins, prs = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            ps_idx = _add_pacbio_sample(conn, prs, None)
            set_pacbio_sample_run_details(
                conn, sample_name=" S1 ", movie_context_id=" m84 "
            )
            log_rows = _read_change_log(conn)
        expected = [("pacbio_sample", ps_idx, "movie_context_id", None, "m84", None)]
        self.assertEqual(log_rows, expected)

    def test_setters_reject_whitespace_only(self):
        with open_db(self.db_path) as conn:
            ins, prs = _helpers.seed_committed_sample_chain(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _add_pacbio_sample(conn, prs, None)
            _add_illumina_run(conn, self.run_idx)
        # Each setter's call is otherwise valid, so only its blank reason fails
        reason_calls = {
            "reason_input_sample_identifier": lambda c: set_input_sample_identifier(
                c, "matrix_tube_id", "0363", sample_name="S1", reason=" "
            ),
            "reason_update_lane": lambda c: update_lane(
                c, "illumina", 1, 2, reason=" "
            ),
            "reason_illumina_setting": lambda c: set_illumina_run_setting(
                c, "override_cycles", "Y151", reason=" "
            ),
            "reason_input_sample_do_not_use": lambda c: set_input_sample_do_not_use(
                c, input_sample_idx=ins, reason=" "
            ),
            "reason_prepped_sample_do_not_use": lambda c: set_prepped_sample_do_not_use(
                c, prs, reason=" "
            ),
            "reason_pacbio": lambda c: set_pacbio_sample_run_details(
                c, sample_name="S1", movie_context_id="m84", reason=" "
            ),
        }
        calls = {
            **reason_calls,
            "sample_value": lambda c: set_input_sample_identifier(
                c, "matrix_tube_id", "  ", sample_name="S1"
            ),
            "sample_key": lambda c: set_input_sample_identifier(
                c, "matrix_tube_id", "0363", sample_name="\t"
            ),
            "project_value": lambda c: set_project_identifier(
                c, "bioproject_accession", " ", project_name="proj1"
            ),
            "illumina_value": lambda c: set_illumina_run_setting(
                c, "override_cycles", "  "
            ),
            "do_not_use_key": lambda c: set_input_sample_do_not_use(
                c, biosample_accession="  "
            ),
            "pacbio_key": lambda c: set_pacbio_sample_run_details(
                c, sample_name="  ", movie_context_id="m84"
            ),
            "pacbio_value": lambda c: set_pacbio_sample_run_details(
                c, sample_name="S1", movie_context_id="  "
            ),
            "reason": lambda c: set_project_identifier(
                c, "bioproject_accession", "PRJNA1", project_name="proj1", reason=" "
            ),
        }
        for call_name, call in calls.items():
            with (
                self.subTest(call=call_name),
                open_db(self.db_path) as conn,
                pytest.raises(ValueError, match="must not be empty"),
            ):
                call(conn)
        # No rejected call may leave an audit row behind
        with open_db(self.db_path) as conn:
            self.assertEqual(_read_change_log(conn), [])


class TestSetterArgumentChecks(_UpdatesTestBase):
    """Setters check every argument before running any SQL."""

    def _seed_sample_and_runs(self, conn) -> tuple[int, int]:
        """Seed sample S1 with an illumina row on lane 1; return (ins, prs)."""
        ins_idx, prs_idx = _helpers.seed_committed_sample_chain(
            conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
        )
        _add_illumina_row(conn, prs_idx, lane=1)
        _add_illumina_run(conn, self.run_idx)
        return ins_idx, prs_idx

    def test_set_project_identifier_blank_reason_keeps_pending_write(self):
        # A blank reason is rejected before any SQL, so the caller's
        # uncommitted work on the connection is not rolled back
        with open_db(self.db_path) as conn:
            ins_idx, _ = self._seed_sample_and_runs(conn)
            conn.execute(
                "UPDATE input_sample SET matrix_tube_id = 'PENDING' "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            with pytest.raises(ValueError, match="reason must not be empty"):
                set_project_identifier(
                    conn,
                    "bioproject_accession",
                    "PRJNA1",
                    project_name="proj1",
                    reason="  ",
                )
            conn.commit()
            cur = conn.execute("SELECT matrix_tube_id FROM input_sample")
            tube_ids = cur.fetchall()
        self.assertEqual(tube_ids, [("PENDING",)])

    def test_update_lane_blank_reason_with_no_matching_rows(self):
        # reason is checked even when no row would be logged
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="reason must not be empty"),
        ):
            self._seed_sample_and_runs(conn)
            update_lane(conn, "illumina", 7, 8, reason="  ")

    def test_setters_reject_non_str_arguments(self):
        with open_db(self.db_path) as conn:
            self._seed_sample_and_runs(conn)
        calls = {
            "int_value": lambda c: set_project_identifier(
                c, "study_export_entity_id", 42, project_name="proj1"
            ),
            "bytes_value": lambda c: set_project_identifier(
                c, "bioproject_accession", b"PRJNA1", project_name="proj1"
            ),
            "int_key": lambda c: set_project_identifier(
                c, "bioproject_accession", "PRJNA1", external_project_id=1
            ),
            "int_reason": lambda c: set_illumina_run_setting(
                c, "override_cycles", "Y151", reason=5
            ),
            "deprecated_alias_int_key": lambda c: set_bioproject_accession(
                c, "PRJNA1", external_project_id=1
            ),
        }
        for call_name, call in calls.items():
            with (
                self.subTest(call=call_name),
                open_db(self.db_path) as conn,
                pytest.raises(TypeError, match="must be a str"),
            ):
                call(conn)

    def test_set_illumina_run_setting_strips_value(self):
        with open_db(self.db_path) as conn:
            self._seed_sample_and_runs(conn)
            set_illumina_run_setting(conn, "override_cycles", " Y151;I10 ")
            cur = conn.execute("SELECT override_cycles FROM illumina_run")
            stored = cur.fetchall()
        self.assertEqual(stored, [("Y151;I10",)])

    def test_set_input_sample_do_not_use_strips_biosample_accession(self):
        with open_db(self.db_path) as conn:
            self._seed_sample_and_runs(conn)
            set_input_sample_identifier(
                conn, "biosample_accession", "SAMN1", sample_name="S1"
            )
            set_input_sample_do_not_use(conn, biosample_accession=" SAMN1\u200b")
            cur = conn.execute("SELECT do_not_use FROM input_sample")
            flags = cur.fetchall()
        self.assertEqual(flags, [(1,)])

    def test_do_not_use_setters_reject_unsupported_values(self):
        # Each do-not-use setter accepts only its two allowed values
        with open_db(self.db_path) as conn:
            ins_idx, prs_idx = self._seed_sample_and_runs(conn)
        cases = (
            (
                "prepped",
                lambda c, v: set_prepped_sample_do_not_use(c, prs_idx, value=v),
                (False, 0, 1, "no"),
            ),
            (
                "input",
                lambda c, v: set_input_sample_do_not_use(
                    c, input_sample_idx=ins_idx, value=v
                ),
                (None, 0, 1, 2, 0.5, "no"),
            ),
        )
        for setter_name, call, bad_values in cases:
            for bad_value in bad_values:
                with (
                    self.subTest(setter=setter_name, value=bad_value),
                    open_db(self.db_path) as conn,
                    pytest.raises(
                        ValueError, match=re.escape(f"{bad_value!r} is not supported")
                    ),
                ):
                    call(conn, bad_value)

    def test_update_lane_rejects_non_positive_int_lanes(self):
        with open_db(self.db_path) as conn:
            self._seed_sample_and_runs(conn)
        for bad_lane in (-1, 0, 1.5, "1", True):
            for lanes in ((1, bad_lane), (bad_lane, 2)):
                with (
                    self.subTest(lanes=lanes),
                    open_db(self.db_path) as conn,
                    pytest.raises(ValueError, match="must be a positive int or None"),
                ):
                    update_lane(conn, "illumina", *lanes)

    def test_set_illumina_run_setting_raise_no_illumina_run_err(self):
        with (
            open_db(self.db_path) as conn,
            pytest.raises(ValueError, match="no illumina_run row"),
        ):
            set_illumina_run_setting(conn, "override_cycles", "Y151")
