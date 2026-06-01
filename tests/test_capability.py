"""Tests for derived capability views."""

from __future__ import annotations

import sqlite3
import unittest

from run_preflight.db import create_db


def _setup_run_and_sample(conn: sqlite3.Connection) -> tuple[int, int]:
    """Create minimal prerequisite rows and return (run_idx, prs_idx).

    Inserts a project, input_plate, input_sample, processing_run,
    compression_sample, and prepped_sample so that
    metagenomic_absquant_sample can reference a valid
    prepped_sample_idx.
    """
    cur = conn.cursor()

    # Insert a project
    cur.execute(
        "INSERT INTO project "
        "(project_name, external_project_id, human_filtering, "
        " library_construction_protocol, experiment_design_description) "
        "VALUES ('proj1', '1', 1, 'proto', 'desc')"
    )
    assert cur.lastrowid is not None
    project_idx = cur.lastrowid

    # Insert an input plate
    cur.execute(
        "INSERT INTO input_plate (plate_name, primary_project_idx) VALUES ('plate1', ?)",
        (project_idx,),
    )
    assert cur.lastrowid is not None
    plate_idx = cur.lastrowid

    # Insert an input sample
    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
        "VALUES ('sample1', ?, ?, 1)",
        (plate_idx, project_idx),
    )
    assert cur.lastrowid is not None
    input_sample_idx = cur.lastrowid

    # Insert a processing run
    cur.execute(
        "INSERT INTO processing_run "
        "(experiment_name, run_date, instrument_type, "
        " assay_type_idx, platform_idx) "
        "VALUES ('exp1', '2025-01-01', 'Unknown', 1, 1)"
    )
    assert cur.lastrowid is not None
    run_idx = cur.lastrowid

    # Insert a compression_sample
    cur.execute(
        "INSERT INTO compression_sample "
        "(run_idx, input_sample_idx, compression_well) "
        "VALUES (?, ?, 'A1')",
        (run_idx, input_sample_idx),
    )
    assert cur.lastrowid is not None
    cs_idx = cur.lastrowid

    # Insert a compression sample
    cur.execute(
        "INSERT INTO prepped_sample "
        "(compression_sample_idx, prepped_well) "
        "VALUES (?, 'A1')",
        (cs_idx,),
    )
    assert cur.lastrowid is not None
    prs_idx = cur.lastrowid

    conn.commit()
    return run_idx, prs_idx


class TestRunCapabilityViews(unittest.TestCase):
    def setUp(self):
        # Create an in-memory DB with the full schema
        self.conn = create_db(":memory:")
        self.run_idx, self.prs_idx = _setup_run_and_sample(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_run_capability_absquant_mass_present(self):
        """Per-capability view returns run_idx when mass data exists."""
        cur = self.conn.cursor()

        # Insert a sample with non-null mass
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g) "
            "VALUES (?, 1.5)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute("SELECT run_idx FROM run_capability_absquant_mass")
        expected = [(self.run_idx,)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_absquant_mass_absent(self):
        """Per-capability view returns no rows when mass is NULL."""
        cur = self.conn.cursor()

        # Insert a sample with NULL mass
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g) "
            "VALUES (?, NULL)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute("SELECT run_idx FROM run_capability_absquant_mass")
        expected = []
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_absquant_volume_present(self):
        """Per-capability view returns run_idx when volume data exists."""
        cur = self.conn.cursor()

        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_volume_ul) "
            "VALUES (?, 2.0)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute("SELECT run_idx FROM run_capability_absquant_volume")
        expected = [(self.run_idx,)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_absquant_surface_area_present(self):
        """Per-capability view returns run_idx when surface area data exists."""
        cur = self.conn.cursor()

        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_surface_area_cm2) "
            "VALUES (?, 3.0)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute("SELECT run_idx FROM run_capability_absquant_surface_area")
        expected = [(self.run_idx,)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_union_multiple(self):
        """Union view returns all capabilities present in the data."""
        cur = self.conn.cursor()

        # Insert a sample with mass and volume but not surface area
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g, "
            " extracted_sample_volume_ul) "
            "VALUES (?, 1.5, 2.0)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute(
            "SELECT capability_name FROM run_capability "
            "WHERE run_idx = ? ORDER BY capability_name",
            (self.run_idx,),
        )
        expected = [("absquant_mass",), ("absquant_volume",)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_union_no_data(self):
        """Union view returns no rows when no metric data exists."""
        cur = self.conn.cursor()

        # Insert a sample with all metrics NULL
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample (prepped_sample_idx) VALUES (?)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute(
            "SELECT capability_name FROM run_capability WHERE run_idx = ?",
            (self.run_idx,),
        )
        expected = []
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_capability_sparse_data(self):
        """Capability detected even when only some samples have the value."""
        cur = self.conn.cursor()

        # Insert first sample with mass
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g) "
            "VALUES (?, 1.5)",
            (self.prs_idx,),
        )

        # Add a second sample (blank/control) with NULL mass
        second_cs_idx = self._add_second_sample()
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g) "
            "VALUES (?, NULL)",
            (second_cs_idx,),
        )
        self.conn.commit()

        # Capability should still be detected from the first sample
        cur.execute("SELECT run_idx FROM run_capability_absquant_mass")
        expected = [(self.run_idx,)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def _add_second_sample(self) -> int:
        """Add a second compression sample to the same run."""
        cur = self.conn.cursor()

        # Reuse existing plate and project
        cur.execute(
            "INSERT INTO input_sample "
            "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
            "VALUES ('sample2', 1, 1, 1)"
        )
        assert cur.lastrowid is not None
        input_sample_idx = cur.lastrowid

        cur.execute(
            "INSERT INTO compression_sample "
            "(run_idx, input_sample_idx, compression_well) "
            "VALUES (?, ?, 'B1')",
            (self.run_idx, input_sample_idx),
        )
        assert cur.lastrowid is not None
        cs_idx = cur.lastrowid

        cur.execute(
            "INSERT INTO prepped_sample "
            "(compression_sample_idx, prepped_well) "
            "VALUES (?, 'B1')",
            (cs_idx,),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid


class TestRunDerivedCapability(unittest.TestCase):
    def setUp(self):
        self.conn = create_db(":memory:")
        self.run_idx, self.prs_idx = _setup_run_and_sample(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_run_derived_capability_absquant_v1(self):
        """View returns ('absquant', 1) when one metric is present."""
        cur = self.conn.cursor()

        # Insert a sample with mass data
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g) "
            "VALUES (?, 1.5)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute(
            "SELECT capability_family, version "
            "FROM run_derived_capability WHERE run_idx = ?",
            (self.run_idx,),
        )
        expected = [("absquant", 1)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_derived_capability_no_metrics(self):
        """View returns no rows when no metric data exists."""
        cur = self.conn.cursor()

        cur.execute(
            "SELECT capability_family, version "
            "FROM run_derived_capability WHERE run_idx = ?",
            (self.run_idx,),
        )
        expected = []
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_derived_capability_max_version(self):
        """MAX(version) returns 1 when multiple metrics present."""
        cur = self.conn.cursor()

        # Insert a sample with both mass and volume
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g, "
            " extracted_sample_volume_ul) "
            "VALUES (?, 1.5, 2.0)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute(
            "SELECT MAX(version) FROM run_derived_capability "
            "WHERE run_idx = ? AND capability_family = 'absquant'",
            (self.run_idx,),
        )
        expected = [(1,)]
        result = cur.fetchall()
        self.assertEqual(result, expected)

    def test_run_derived_capability_multiple_caps_one_row(self):
        """View returns one row per family even with multiple metrics."""
        cur = self.conn.cursor()

        # Insert a sample with mass and surface area
        cur.execute(
            "INSERT INTO metagenomic_absquant_sample "
            "(prepped_sample_idx, extracted_sample_mass_g, "
            " extracted_sample_surface_area_cm2) "
            "VALUES (?, 1.5, 3.0)",
            (self.prs_idx,),
        )
        self.conn.commit()

        cur.execute(
            "SELECT capability_family, version "
            "FROM run_derived_capability WHERE run_idx = ?",
            (self.run_idx,),
        )
        expected = [("absquant", 1)]
        result = cur.fetchall()
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
