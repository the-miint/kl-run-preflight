"""Tests for updates.py: set_biosample_accession and update_lane."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import unittest

import pytest

from run_preflight.db import create_db
from run_preflight.updates import set_biosample_accession, update_lane


@contextlib.contextmanager
def _open(db_path: str):
    """Open a raw connection to *db_path* with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _setup_run(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert a project, plate, and run; return (project_idx, plate_idx, run_idx)."""
    cur = conn.cursor()
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
        "INSERT INTO processing_run "
        "(experiment_name, run_date, instrument_type, assay_type_idx, platform_idx) "
        "VALUES ('exp1', '2025-01-01', 'Unknown', 1, 1)"
    )
    run_idx = cur.lastrowid
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
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
        "VALUES (?, ?, ?, 1)",
        (sample_name, plate_idx, project_idx),
    )
    ins_idx = cur.lastrowid
    cur.execute(
        "INSERT INTO compression_sample "
        "(run_idx, input_sample_idx, compression_well) "
        "VALUES (?, ?, ?)",
        (run_idx, ins_idx, well),
    )
    cs_idx = cur.lastrowid
    cur.execute(
        "INSERT INTO prepped_sample "
        "(compression_sample_idx, prepped_well, sample_name) "
        "VALUES (?, ?, ?)",
        (cs_idx, well, prs_name),
    )
    prs_idx = cur.lastrowid
    conn.commit()
    return ins_idx, prs_idx


def _add_illumina_row(
    conn: sqlite3.Connection,
    prs_idx: int,
    lane: int | None = None,
) -> int:
    """Insert one illumina_sample row; return its surrogate id."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO illumina_sample "
        "(prepped_sample_idx, i7_index_id, i7_sequence, "
        " i5_index_id, i5_sequence, lane) "
        "VALUES (?, 'i7', 'AAAA', 'i5', 'CCCC', ?)",
        (prs_idx, lane),
    )
    conn.commit()
    return cur.lastrowid


class _UpdatesTestBase(unittest.TestCase):
    """Shared tempfile-backed DB setup for update-function tests."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        # Build the DB via create_db so it is stamped at the latest
        # user_version; seed the run, then close so each test reopens
        # via _open for its arrange / act / assert phases.
        conn = create_db(self.db_path)
        try:
            self.project_idx, self.plate_idx, self.run_idx = _setup_run(conn)
        finally:
            conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()


class TestSetBiosampleAccession(_UpdatesTestBase):
    def test_set_biosample_accession_non_replicate(self):
        with _open(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        with _open(self.db_path) as conn:
            set_biosample_accession(conn, "S1", "SAMN001", reason="initial")

        with _open(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN001",))

    def test_set_biosample_accession_replicate_alias(self):
        # Replicate alias resolves through prs.sample_name to the
        # underlying input_sample
        with _open(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn,
                self.plate_idx,
                self.project_idx,
                self.run_idx,
                "S1",
                "A1",
                prs_name="S1.A1",
            )

        with _open(self.db_path) as conn:
            set_biosample_accession(conn, "S1.A1", "SAMN002")

        with _open(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN002",))

    def test_set_biosample_accession_replicates_share_one_accession(self):
        # Two prepped_samples (replicates) of one input_sample share
        # the input_sample's accession
        with _open(self.db_path) as conn:
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
        with _open(self.db_path) as conn:
            set_biosample_accession(conn, "S1.B2", "SAMN003")

        with _open(self.db_path) as conn:
            cur = conn.execute(
                "SELECT biosample_accession FROM input_sample "
                "WHERE input_sample_idx = ?",
                (ins_idx,),
            )
            self.assertEqual(cur.fetchone(), ("SAMN003",))

    def test_set_biosample_accession_ambiguous(self):
        # Two distinct input_samples produce the same effective Sample_Name
        with _open(self.db_path) as conn:
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

        with _open(self.db_path) as conn, pytest.raises(ValueError, match="ambiguous"):
            set_biosample_accession(conn, "S1", "SAMN004")

    def test_set_biosample_accession_missing(self):
        with (
            _open(self.db_path) as conn,
            pytest.raises(ValueError, match="No input_sample matches"),
        ):
            set_biosample_accession(conn, "nonexistent", "SAMN005")

    def test_set_biosample_accession_audit(self):
        with _open(self.db_path) as conn:
            ins_idx, _ = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )

        # Two successive sets so the second log entry captures the
        # first call's value as old_value
        with _open(self.db_path) as conn:
            set_biosample_accession(conn, "S1", "SAMN006", reason="initial")
            set_biosample_accession(conn, "S1", "SAMN007", reason="correction")

        with _open(self.db_path) as conn:
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


class TestUpdateLane(_UpdatesTestBase):
    def test_update_lane_null_to_value(self):
        # All rows uniformly NULL, then assigned a lane
        with _open(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=None)
            _add_illumina_row(conn, prs2, lane=None)

        with _open(self.db_path) as conn:
            n = update_lane(conn, "illumina", from_lane=None, to_lane=2)
        self.assertEqual(n, 2)
        with _open(self.db_path) as conn:
            cur = conn.execute(
                "SELECT lane FROM illumina_sample ORDER BY illumina_sample_idx"
            )
            self.assertEqual([r[0] for r in cur.fetchall()], [2, 2])

    def test_update_lane_value_to_value(self):
        with _open(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs2, lane=1)

        with _open(self.db_path) as conn:
            n = update_lane(conn, "illumina", from_lane=1, to_lane=3)
        self.assertEqual(n, 2)
        with _open(self.db_path) as conn:
            cur = conn.execute(
                "SELECT lane FROM illumina_sample ORDER BY illumina_sample_idx"
            )
            self.assertEqual([r[0] for r in cur.fetchall()], [3, 3])

    def test_update_lane_collision(self):
        # Multi-lane fan-out: one prepped_sample at both lane 1 and lane 2
        with _open(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs1, lane=2)

        with (
            _open(self.db_path) as conn,
            pytest.raises(ValueError, match="already have a row at"),
        ):
            update_lane(conn, "illumina", from_lane=1, to_lane=2)

    def test_update_lane_uniformity_violation(self):
        # Setting some rows to NULL while others remain non-NULL is mixed
        with _open(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            _add_illumina_row(conn, prs1, lane=1)
            _add_illumina_row(conn, prs2, lane=2)

        with (
            _open(self.db_path) as conn,
            pytest.raises(ValueError, match="uniformity violation"),
        ):
            update_lane(conn, "illumina", from_lane=1, to_lane=None)

    def test_update_lane_unsupported_platform(self):
        with (
            _open(self.db_path) as conn,
            pytest.raises(ValueError, match="Unsupported platform"),
        ):
            update_lane(conn, "pacbio", from_lane=1, to_lane=2)

    def test_update_lane_audit(self):
        with _open(self.db_path) as conn:
            _, prs1 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S1", "A1"
            )
            _, prs2 = _add_sample(
                conn, self.plate_idx, self.project_idx, self.run_idx, "S2", "B2"
            )
            i1 = _add_illumina_row(conn, prs1, lane=1)
            i2 = _add_illumina_row(conn, prs2, lane=1)

        with _open(self.db_path) as conn:
            update_lane(conn, "illumina", from_lane=1, to_lane=4, reason="reload")

        with _open(self.db_path) as conn:
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
        with _open(self.db_path) as conn:
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

        with _open(self.db_path) as conn:
            n = update_lane(conn, "tellseq", from_lane=None, to_lane=1)
        self.assertEqual(n, 1)
        with _open(self.db_path) as conn:
            cur = conn.execute("SELECT lane FROM tellseq_sample")
            self.assertEqual(cur.fetchone(), (1,))


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
            _open(self.db_path) as conn,
            pytest.raises(sqlite3.IntegrityError, match="CHECK"),
        ):
            self._insert(conn, None, None)

    def test_input_sample_check_accepts_sample_name_only(self):
        # sample_name alone satisfies the CHECK
        with _open(self.db_path) as conn:
            ins_idx = self._insert(conn, "S1", None)
            self.assertEqual(self._read_identity(conn, ins_idx), ("S1", None))

    def test_input_sample_check_accepts_biosample_accession_only(self):
        # biosample_accession alone satisfies the CHECK
        with _open(self.db_path) as conn:
            ins_idx = self._insert(conn, None, "SAMN100")
            self.assertEqual(self._read_identity(conn, ins_idx), (None, "SAMN100"))

    def test_input_sample_check_accepts_both_non_null(self):
        # Both non-null satisfies the CHECK
        with _open(self.db_path) as conn:
            ins_idx = self._insert(conn, "S1", "SAMN101")
            self.assertEqual(self._read_identity(conn, ins_idx), ("S1", "SAMN101"))
