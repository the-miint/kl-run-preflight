"""Tests for updates.py: set_biosample_accession and update_lane."""

from __future__ import annotations

import sqlite3
import unittest

import pytest

from sequencing_brief.db import create_db
from sequencing_brief.updates import set_biosample_accession, update_lane


def _setup_run(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert a project, plate, and run; return (project_id, plate_id, run_id)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO project "
        "(project_name, qiita_id, human_filtering, "
        " library_construction_protocol, experiment_design_description) "
        "VALUES ('proj1', '1', 1, 'proto', 'desc')"
    )
    project_id = cur.lastrowid
    cur.execute(
        "INSERT INTO input_plate (plate_name, primary_project_id) VALUES ('plate1', ?)",
        (project_id,),
    )
    plate_id = cur.lastrowid
    cur.execute(
        "INSERT INTO sequencing_run "
        "(experiment_name, run_date, sequencer, assay_type_id, platform_id) "
        "VALUES ('exp1', '2025-01-01', 'Unknown', 1, 1)"
    )
    run_id = cur.lastrowid
    conn.commit()
    return project_id, plate_id, run_id


def _add_sample(
    conn: sqlite3.Connection,
    plate_id: int,
    project_id: int,
    run_id: int,
    sample_name: str,
    well: str,
    prs_name: str | None = None,
) -> tuple[int, int]:
    """Insert input + compression + prepped sample; return (ins_id, prs_id)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_id, project_id, sample_type_id) "
        "VALUES (?, ?, ?, 1)",
        (sample_name, plate_id, project_id),
    )
    ins_id = cur.lastrowid
    cur.execute(
        "INSERT INTO compression_sample "
        "(run_id, input_sample_id, compression_well) "
        "VALUES (?, ?, ?)",
        (run_id, ins_id, well),
    )
    cs_id = cur.lastrowid
    cur.execute(
        "INSERT INTO prepped_sample "
        "(compression_sample_id, prepped_well, sample_name) "
        "VALUES (?, ?, ?)",
        (cs_id, well, prs_name),
    )
    prs_id = cur.lastrowid
    conn.commit()
    return ins_id, prs_id


def _add_illumina_row(
    conn: sqlite3.Connection,
    prs_id: int,
    lane: int | None = None,
) -> int:
    """Insert one illumina_sample row; return its surrogate id."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO illumina_sample "
        "(prepped_sample_id, i7_index_id, i7_sequence, "
        " i5_index_id, i5_sequence, lane) "
        "VALUES (?, 'i7', 'AAAA', 'i5', 'CCCC', ?)",
        (prs_id, lane),
    )
    conn.commit()
    return cur.lastrowid


class TestSetBiosampleAccession(unittest.TestCase):
    def setUp(self):
        self.conn = create_db(":memory:")
        self.project_id, self.plate_id, self.run_id = _setup_run(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_set_biosample_accession_non_replicate(self):
        ins_id, _ = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        set_biosample_accession(self.conn, "S1", "SAMN001", reason="initial")
        cur = self.conn.execute(
            "SELECT biosample_accession FROM input_sample WHERE input_sample_id = ?",
            (ins_id,),
        )
        self.assertEqual(cur.fetchone(), ("SAMN001",))

    def test_set_biosample_accession_replicate_alias(self):
        # Replicate alias resolves through prs.sample_name to the
        # underlying input_sample
        ins_id, _ = _add_sample(
            self.conn,
            self.plate_id,
            self.project_id,
            self.run_id,
            "S1",
            "A1",
            prs_name="S1.A1",
        )
        set_biosample_accession(self.conn, "S1.A1", "SAMN002")
        cur = self.conn.execute(
            "SELECT biosample_accession FROM input_sample WHERE input_sample_id = ?",
            (ins_id,),
        )
        self.assertEqual(cur.fetchone(), ("SAMN002",))

    def test_set_biosample_accession_replicates_share_one_accession(self):
        # Two prepped_samples (replicates) of one input_sample share
        # the input_sample's accession
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO input_sample "
            "(sample_name, input_plate_id, project_id, sample_type_id) "
            "VALUES ('S1', ?, ?, 1)",
            (self.plate_id, self.project_id),
        )
        ins_id = cur.lastrowid
        cur.execute(
            "INSERT INTO compression_sample "
            "(run_id, input_sample_id, compression_well) "
            "VALUES (?, ?, 'A1')",
            (self.run_id, ins_id),
        )
        cs_id = cur.lastrowid
        cur.execute(
            "INSERT INTO prepped_sample "
            "(compression_sample_id, prepped_well, sample_name) "
            "VALUES (?, 'A1', 'S1.A1')",
            (cs_id,),
        )
        cur.execute(
            "INSERT INTO prepped_sample "
            "(compression_sample_id, prepped_well, sample_name) "
            "VALUES (?, 'B2', 'S1.B2')",
            (cs_id,),
        )
        self.conn.commit()

        # Update via one alias, the other alias shows the same accession
        set_biosample_accession(self.conn, "S1.B2", "SAMN003")
        cur = self.conn.execute(
            "SELECT biosample_accession FROM input_sample WHERE input_sample_id = ?",
            (ins_id,),
        )
        self.assertEqual(cur.fetchone(), ("SAMN003",))

    def test_set_biosample_accession_ambiguous(self):
        # Two distinct input_samples produce the same effective Sample_Name
        _add_sample(self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1")
        _add_sample(
            self.conn,
            self.plate_id,
            self.project_id,
            self.run_id,
            "S2",
            "B2",
            prs_name="S1",
        )
        with pytest.raises(ValueError, match="ambiguous"):
            set_biosample_accession(self.conn, "S1", "SAMN004")

    def test_set_biosample_accession_missing(self):
        with pytest.raises(ValueError, match="No input_sample matches"):
            set_biosample_accession(self.conn, "nonexistent", "SAMN005")

    def test_set_biosample_accession_audit(self):
        ins_id, _ = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        # Two successive sets so we can verify the second log entry
        # captures the first call's value as old_value
        set_biosample_accession(self.conn, "S1", "SAMN006", reason="initial")
        set_biosample_accession(self.conn, "S1", "SAMN007", reason="correction")
        cur = self.conn.execute(
            "SELECT table_name, row_id, column_name, "
            " old_value, new_value, reason "
            "FROM change_log ORDER BY change_id"
        )
        expected = [
            ("input_sample", ins_id, "biosample_accession", None, "SAMN006", "initial"),
            (
                "input_sample",
                ins_id,
                "biosample_accession",
                "SAMN006",
                "SAMN007",
                "correction",
            ),
        ]
        self.assertEqual(cur.fetchall(), expected)


class TestUpdateLane(unittest.TestCase):
    def setUp(self):
        self.conn = create_db(":memory:")
        self.project_id, self.plate_id, self.run_id = _setup_run(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_update_lane_null_to_value(self):
        # All rows uniformly NULL, then assigned a lane
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        _, prs2 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S2", "B2"
        )
        _add_illumina_row(self.conn, prs1, lane=None)
        _add_illumina_row(self.conn, prs2, lane=None)

        n = update_lane(self.conn, "illumina", from_lane=None, to_lane=2)
        self.assertEqual(n, 2)
        cur = self.conn.execute(
            "SELECT lane FROM illumina_sample ORDER BY illumina_sample_id"
        )
        self.assertEqual([r[0] for r in cur.fetchall()], [2, 2])

    def test_update_lane_value_to_value(self):
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        _, prs2 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S2", "B2"
        )
        _add_illumina_row(self.conn, prs1, lane=1)
        _add_illumina_row(self.conn, prs2, lane=1)

        n = update_lane(self.conn, "illumina", from_lane=1, to_lane=3)
        self.assertEqual(n, 2)
        cur = self.conn.execute(
            "SELECT lane FROM illumina_sample ORDER BY illumina_sample_id"
        )
        self.assertEqual([r[0] for r in cur.fetchall()], [3, 3])

    def test_update_lane_collision(self):
        # Multi-lane fan-out: one prepped_sample at both lane 1 and lane 2
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        _add_illumina_row(self.conn, prs1, lane=1)
        _add_illumina_row(self.conn, prs1, lane=2)

        with pytest.raises(ValueError, match="already have a row at"):
            update_lane(self.conn, "illumina", from_lane=1, to_lane=2)

    def test_update_lane_uniformity_violation(self):
        # Setting some rows to NULL while others remain non-NULL is mixed
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        _, prs2 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S2", "B2"
        )
        _add_illumina_row(self.conn, prs1, lane=1)
        _add_illumina_row(self.conn, prs2, lane=2)

        with pytest.raises(ValueError, match="uniformity violation"):
            update_lane(self.conn, "illumina", from_lane=1, to_lane=None)

    def test_update_lane_unsupported_platform(self):
        with pytest.raises(ValueError, match="Unsupported platform"):
            update_lane(self.conn, "pacbio", from_lane=1, to_lane=2)

    def test_update_lane_audit(self):
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        _, prs2 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S2", "B2"
        )
        i1 = _add_illumina_row(self.conn, prs1, lane=1)
        i2 = _add_illumina_row(self.conn, prs2, lane=1)

        update_lane(self.conn, "illumina", from_lane=1, to_lane=4, reason="reload")
        cur = self.conn.execute(
            "SELECT table_name, row_id, column_name, "
            " old_value, new_value, reason "
            "FROM change_log ORDER BY change_id"
        )
        expected = [
            ("illumina_sample", i1, "lane", "1", "4", "reload"),
            ("illumina_sample", i2, "lane", "1", "4", "reload"),
        ]
        self.assertEqual(cur.fetchall(), expected)

    def test_update_lane_tellseq(self):
        _, prs1 = _add_sample(
            self.conn, self.plate_id, self.project_id, self.run_id, "S1", "A1"
        )
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO tellseq_sample (prepped_sample_id, barcode_id, lane) "
            "VALUES (?, 'BC1', NULL)",
            (prs1,),
        )
        self.conn.commit()

        n = update_lane(self.conn, "tellseq", from_lane=None, to_lane=1)
        self.assertEqual(n, 1)
        cur = self.conn.execute("SELECT lane FROM tellseq_sample")
        self.assertEqual(cur.fetchone(), (1,))
