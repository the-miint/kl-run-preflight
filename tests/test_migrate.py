"""Tests for schema migration infrastructure."""

from __future__ import annotations

import sqlite3
import textwrap

import pytest

from sequencing_brief.db import create_db
from sequencing_brief.migrate import (
    apply_patches,
    get_latest_version,
    get_pending_patches,
    get_schema_version,
    open_db,
)


class TestGetLatestVersion:
    def test_get_latest_version_empty_dir(self, tmp_path):
        """Empty patches directory returns 0."""
        assert get_latest_version(tmp_path) == 0

    def test_get_latest_version_with_patches(self, tmp_path):
        """Returns highest patch number from sql and py files."""
        (tmp_path / "001_first.sql").write_text("SELECT 1;")
        (tmp_path / "002_second.py").write_text("")
        assert get_latest_version(tmp_path) == 2

    def test_get_latest_version_ignores_non_patch_files(self, tmp_path):
        """Non-patch files (.gitkeep, README, etc.) are ignored."""
        (tmp_path / ".gitkeep").write_text("")
        (tmp_path / "README.md").write_text("info")
        (tmp_path / "001_real.sql").write_text("SELECT 1;")
        assert get_latest_version(tmp_path) == 1


class TestGetPendingPatches:
    def test_get_pending_patches_all_pending(self, tmp_path):
        """DB at version 0 with patches 001+002 returns both in order."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        result = get_pending_patches(conn, tmp_path)
        expected = [
            (1, tmp_path / "001_a.sql"),
            (2, tmp_path / "002_b.sql"),
        ]
        assert result == expected

    def test_get_pending_patches_partially_applied(self, tmp_path):
        """DB at version 1 returns only patch 002."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA user_version = 1")
        result = get_pending_patches(conn, tmp_path)
        expected = [(2, tmp_path / "002_b.sql")]
        assert result == expected

    def test_get_pending_patches_version_gap(self, tmp_path):
        """Missing patch 001 with 002 present raises ValueError."""
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        with pytest.raises(ValueError, match="missing"):
            get_pending_patches(conn, tmp_path)

    def test_get_pending_patches_db_newer_than_code(self, tmp_path):
        """DB version exceeds latest patch number raises ValueError."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA user_version = 5")
        with pytest.raises(ValueError, match="exceeds"):
            get_pending_patches(conn, tmp_path)


class TestApplyPatches:
    def test_apply_patches_sql(self, tmp_path):
        """SQL patch creates a table and runner sets user_version."""
        (tmp_path / "001_add_test_table.sql").write_text(
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY);"
        )
        conn = sqlite3.connect(":memory:")
        result = apply_patches(conn, tmp_path)
        # Verify table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        )
        assert cur.fetchone() is not None
        # Verify version set by runner
        assert result == 1
        assert get_schema_version(conn) == 1

    def test_apply_patches_py(self, tmp_path):
        """Python patch with apply(conn) adds a column; version set by runner."""
        # Create a base table first
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE base (id INTEGER PRIMARY KEY)")

        # Write synthetic .py patch
        patch_code = textwrap.dedent("""\
            def apply(conn):
                conn.execute("ALTER TABLE base ADD COLUMN name TEXT")
        """)
        (tmp_path / "001_add_name_col.py").write_text(patch_code)

        result = apply_patches(conn, tmp_path)
        # Verify column added and version set by runner
        cur = conn.execute("PRAGMA table_info(base)")
        col_names = [row[1] for row in cur.fetchall()]
        assert col_names == ["id", "name"]
        assert result == 1
        assert get_schema_version(conn) == 1

    def test_apply_patches_mixed(self, tmp_path):
        """SQL patch followed by Python patch both apply in order."""
        (tmp_path / "001_create.sql").write_text(
            "CREATE TABLE mixed (id INTEGER PRIMARY KEY);"
        )
        patch_code = textwrap.dedent("""\
            def apply(conn):
                conn.execute("ALTER TABLE mixed ADD COLUMN label TEXT")
        """)
        (tmp_path / "002_add_label.py").write_text(patch_code)

        conn = sqlite3.connect(":memory:")
        result = apply_patches(conn, tmp_path)
        # Verify both changes applied
        cur = conn.execute("PRAGMA table_info(mixed)")
        col_names = [row[1] for row in cur.fetchall()]
        assert col_names == ["id", "label"]
        assert result == 2
        assert get_schema_version(conn) == 2

    def test_apply_patches_already_current(self, tmp_path):
        """No-op when DB is already at latest version."""
        (tmp_path / "001_a.sql").write_text("CREATE TABLE should_not_run (id INT);")
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA user_version = 1")
        result = apply_patches(conn, tmp_path)
        assert result == 1
        # Table should NOT exist since patch was not applied
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='should_not_run'"
        )
        assert cur.fetchone() is None

    def test_apply_patches_version_gap(self, tmp_path):
        """Error on missing patch in sequence."""
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        with pytest.raises(ValueError, match="missing"):
            apply_patches(conn, tmp_path)

    def test_apply_patches_db_newer_than_code(self, tmp_path):
        """Error when DB version exceeds latest patch."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA user_version = 5")
        with pytest.raises(ValueError, match="exceeds"):
            apply_patches(conn, tmp_path)

    def test_apply_patches_py_missing_apply(self, tmp_path):
        """Python patch without apply() raises AttributeError."""
        patch_code = "def wrong_name(conn):\n    pass\n"
        (tmp_path / "001_bad.py").write_text(patch_code)
        conn = sqlite3.connect(":memory:")
        with pytest.raises(AttributeError, match="must define an apply"):
            apply_patches(conn, tmp_path)


class TestOpenDb:
    def test_open_db_current_version(self, tmp_path):
        """Opening a current-version DB is a no-op."""
        db_path = str(tmp_path / "test.db")
        # Create fresh DB (already at latest version)
        conn = create_db(db_path)
        conn.close()
        # Reopen via open_db
        conn = open_db(db_path)
        assert get_schema_version(conn) == get_latest_version()
        # Verify foreign keys enabled
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1
        conn.close()

    def test_open_db_applies_patches(self, tmp_path):
        """Opening a DB with lowered version applies pending patches."""
        db_path = str(tmp_path / "test.db")
        patches_subdir = tmp_path / "patches"
        patches_subdir.mkdir()

        # Create a minimal DB at version 0
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE base (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 0")
        conn.close()

        # Write a patch that adds a column
        (patches_subdir / "001_add_col.sql").write_text(
            "ALTER TABLE base ADD COLUMN tag TEXT;"
        )

        conn = open_db(db_path, patches_dir=patches_subdir)
        assert get_schema_version(conn) == 1
        # Verify column added
        cur = conn.execute("PRAGMA table_info(base)")
        col_names = [row[1] for row in cur.fetchall()]
        assert col_names == ["id", "tag"]
        conn.close()


class TestCreateDbVersion:
    def test_create_db_sets_version(self, tmp_path):
        """create_db stamps user_version to match get_latest_version."""
        db_path = str(tmp_path / "test.db")
        conn = create_db(db_path)
        assert get_schema_version(conn) == get_latest_version()
        conn.close()
