"""Tests for the format-detecting open_file entry point."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from run_preflight import (
    migrate_legacy_csv_to_db_file,
    open_file,
)
from run_preflight.legacy import LegacyExtraColumnWarning

DATA_DIR = Path(__file__).parent / "data"
GOOD_CSV = DATA_DIR / "good_standard_metagv90.csv"


class TestOpenFile(unittest.TestCase):
    def setUp(self):
        # Per-test scratch dir for any intermediate DB files
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_file_csv(self):
        # A legacy omnibus CSV must dispatch through load_legacy_csv and
        # return a populated in-memory connection
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            conn = open_file(str(GOOD_CSV))
        try:
            run_idxs = [
                row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
            ]
        finally:
            conn.close()
        self.assertEqual(run_idxs, [1])

    def test_open_file_db(self):
        # A SQLite DB file must be detected via the magic header and
        # dispatch through open_db_file
        db_path = self.tmp_dir / "loaded.db"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyExtraColumnWarning)
            migrate_legacy_csv_to_db_file(str(GOOD_CSV), str(db_path))

        # Re-opening the DB via open_file should yield the same single run row
        conn = open_file(str(db_path))
        try:
            run_idxs = [
                row[0] for row in conn.execute("SELECT run_idx FROM processing_run")
            ]
        finally:
            conn.close()
        self.assertEqual(run_idxs, [1])

    def test_open_file_missing_path(self):
        # A nonexistent path must raise FileNotFoundError with a clear message
        missing = self.tmp_dir / "does_not_exist.csv"
        with self.assertRaisesRegex(FileNotFoundError, r"No such file"):
            open_file(str(missing))

    def test_open_file_empty_file(self):
        # An empty file cannot be SQLite (no magic header) so it falls
        # through to the legacy parser, which must reject it via ValueError
        empty = self.tmp_dir / "empty.csv"
        empty.write_text("")
        with self.assertRaises(ValueError):
            open_file(str(empty))


if __name__ == "__main__":
    unittest.main()
