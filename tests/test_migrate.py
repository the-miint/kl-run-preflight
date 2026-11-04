"""Tests for schema migration infrastructure."""

from __future__ import annotations

import shutil
import sqlite3
import textwrap
from pathlib import Path

import pytest

from run_preflight.constants import IN_MEMORY_PATH
from run_preflight.db import create_db
from run_preflight.file_io import load_db_file
from run_preflight.legacy.api import load_file
from run_preflight.migrate import (
    SchemaVersionTooNewError,
    apply_patches,
    get_latest_version,
    get_pending_patches,
    get_schema_version,
)

from . import _helpers

_SQL_DIR = Path(__file__).resolve().parent.parent / "src" / "run_preflight" / "sql"
_EXPORT_ENTITY_ID_PATCH_NUM = 3


def _fetch_all(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    """Run *sql* and return every result row."""
    cur = conn.execute(sql)
    rows = cur.fetchall()
    return rows


def _fetch_scalar(conn: sqlite3.Connection, sql: str):
    """Run *sql* and return the first column of its first row."""
    cur = conn.execute(sql)
    row = cur.fetchone()
    return row[0]


def _build_populated_db_before_patch(
    tmp_path: Path, patch_num: int
) -> tuple[sqlite3.Connection, dict[str, int]]:
    """Build an FK-enforcing DB one version short of *patch_num*, seeded.

    Seeds one project, plate, run, and sample chain, and returns the
    connection plus the seeded row keys.
    """
    # Stage only the shipped patches that precede patch_num
    earlier_dir = tmp_path / "earlier_patches"
    earlier_dir.mkdir()
    for patch_path in (_SQL_DIR / "patches").glob("[0-9][0-9][0-9]_*"):
        if int(patch_path.name[:3]) < patch_num:
            shutil.copy(patch_path, earlier_dir / patch_path.name)

    # Bring the frozen baseline forward to just before patch_num
    conn = sqlite3.connect(IN_MEMORY_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((_SQL_DIR / "schema_v0.sql").read_text())
    apply_patches(conn, earlier_dir)

    # Seed a minimal sample chain that references both rebuilt tables
    project_idx, plate_idx = _helpers.seed_project_and_plate(conn)
    run_idx = _helpers.seed_processing_run(conn)
    ins_idx, _cs_idx, prs_idx = _helpers.seed_sample_chain(
        conn, plate_idx, project_idx, run_idx
    )
    conn.commit()
    keys = {
        "project_idx": project_idx,
        "plate_idx": plate_idx,
        "ins_idx": ins_idx,
        "prs_idx": prs_idx,
    }
    return conn, keys


def _seed_v0_db_with_pending_patch(tmp_path) -> tuple[str, Path]:
    """Create a version-0 DB file plus a one-patch dir; return both paths.

    The patch adds a ``tag`` column to the DB's single ``base`` table, so a
    caller can assert either that the patch landed on the connection or
    that the source file was left alone.
    """
    db_path = str(tmp_path / "test.db")
    patches_subdir = tmp_path / "patches"
    patches_subdir.mkdir()

    # Minimal DB deliberately left at version 0 so the patch stays pending
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE base (id INTEGER PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 0")
    conn.close()

    (patches_subdir / "001_add_col.sql").write_text(
        "ALTER TABLE base ADD COLUMN tag TEXT;"
    )
    return db_path, patches_subdir


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
        conn = sqlite3.connect(IN_MEMORY_PATH)
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
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.execute("PRAGMA user_version = 1")
        result = get_pending_patches(conn, tmp_path)
        expected = [(2, tmp_path / "002_b.sql")]
        assert result == expected

    def test_get_pending_patches_version_gap(self, tmp_path):
        """Missing patch 001 with 002 present raises ValueError."""
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(IN_MEMORY_PATH)
        with pytest.raises(ValueError, match="missing"):
            get_pending_patches(conn, tmp_path)

    def test_get_pending_patches_db_newer_than_code(self, tmp_path):
        """DB version exceeds latest patch number raises SchemaVersionTooNewError."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.execute("PRAGMA user_version = 5")
        with pytest.raises(SchemaVersionTooNewError, match="exceeds"):
            get_pending_patches(conn, tmp_path)


class TestApplyPatches:
    def test_apply_patches_sql(self, tmp_path):
        """SQL patch creates a table and runner sets user_version."""
        (tmp_path / "001_add_test_table.sql").write_text(
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY);"
        )
        conn = sqlite3.connect(IN_MEMORY_PATH)
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
        conn = sqlite3.connect(IN_MEMORY_PATH)
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

        conn = sqlite3.connect(IN_MEMORY_PATH)
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
        conn = sqlite3.connect(IN_MEMORY_PATH)
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
        conn = sqlite3.connect(IN_MEMORY_PATH)
        with pytest.raises(ValueError, match="missing"):
            apply_patches(conn, tmp_path)

    def test_apply_patches_db_newer_than_code(self, tmp_path):
        """SchemaVersionTooNewError when DB version exceeds latest patch."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.execute("PRAGMA user_version = 5")
        with pytest.raises(SchemaVersionTooNewError, match="exceeds"):
            apply_patches(conn, tmp_path)

    def test_apply_patches_restores_foreign_keys(self, tmp_path):
        """FK enforcement is suspended for the patch and restored after."""
        # The patch records the enforcement setting it ran under
        (tmp_path / "001_record_fk.py").write_text(
            textwrap.dedent("""\
                def apply(conn):
                    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                    conn.execute("CREATE TABLE fk_seen (value INTEGER)")
                    conn.execute("INSERT INTO fk_seen VALUES (?)", (fk,))
            """)
        )
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_patches(conn, tmp_path)
        seen = _fetch_scalar(conn, "SELECT value FROM fk_seen")
        after = _fetch_scalar(conn, "PRAGMA foreign_keys")
        assert (seen, after) == (0, 1)

    def test_apply_patches_rolls_back_fk_violation(self, tmp_path):
        """A patch introducing a dangling reference is rolled back and raises."""
        (tmp_path / "001_dangling.sql").write_text(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));\n"
            "INSERT INTO child VALUES (99);"
        )
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="foreign-key violations"):
            apply_patches(conn, tmp_path)
        table_names = _fetch_all(
            conn, "SELECT name FROM sqlite_master WHERE type='table'"
        )
        fk = _fetch_scalar(conn, "PRAGMA foreign_keys")
        assert (get_schema_version(conn), table_names, fk) == (0, [], 1)

    def test_apply_patches_ignores_preexisting_fk_violation(self, tmp_path):
        """A violation already in the DB is not blamed on the patch."""
        (tmp_path / "001_unrelated.sql").write_text("CREATE TABLE unrelated (id INT);")
        # Plant a dangling reference while enforcement is off
        conn = sqlite3.connect(IN_MEMORY_PATH)
        conn.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));\n"
            "INSERT INTO child VALUES (99);"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        result = apply_patches(conn, tmp_path)
        assert result == 1

    def test_apply_patches_restores_legacy_alter_table(self, tmp_path):
        """A failing patch cannot leave legacy ALTER TABLE mode switched on."""
        (tmp_path / "001_fails.sql").write_text(
            "PRAGMA legacy_alter_table = ON;\nSELECT * FROM no_such_table;"
        )
        conn = sqlite3.connect(IN_MEMORY_PATH)
        with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
            apply_patches(conn, tmp_path)
        legacy_alter = _fetch_scalar(conn, "PRAGMA legacy_alter_table")
        assert legacy_alter == 0

    def test_apply_patches_py_missing_apply(self, tmp_path):
        """Python patch without apply() raises AttributeError."""
        patch_code = "def wrong_name(conn):\n    pass\n"
        (tmp_path / "001_bad.py").write_text(patch_code)
        conn = sqlite3.connect(IN_MEMORY_PATH)
        with pytest.raises(AttributeError, match="must define an apply"):
            apply_patches(conn, tmp_path)


class TestLoadDbFile:
    def test_load_db_file_current_version(self, tmp_path):
        """Loading a current-version DB applies no patches."""
        db_path = str(tmp_path / "test.db")
        # Create fresh DB (already at latest version)
        conn = create_db(db_path)
        conn.close()
        # Load via load_db_file
        conn = load_db_file(db_path)
        assert get_schema_version(conn) == get_latest_version()
        # Verify foreign keys enabled
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1
        conn.close()

    def test_load_db_file_applies_patches(self, tmp_path):
        """Loading a DB with lowered version applies pending patches."""
        db_path, patches_subdir = _seed_v0_db_with_pending_patch(tmp_path)

        conn = load_db_file(db_path, patches_dir=patches_subdir)
        assert get_schema_version(conn) == 1
        # Verify column added
        cur = conn.execute("PRAGMA table_info(base)")
        col_names = [row[1] for row in cur.fetchall()]
        assert col_names == ["id", "tag"]
        conn.close()

    def test_load_db_file_does_not_modify_source(self, tmp_path):
        """Patching on load leaves the source file byte-identical."""
        db_path, patches_subdir = _seed_v0_db_with_pending_patch(tmp_path)
        before = Path(db_path).read_bytes()

        # The returned connection is upgraded even though the file is not
        conn = load_db_file(db_path, patches_dir=patches_subdir)
        try:
            assert get_schema_version(conn) == 1
        finally:
            conn.close()
        assert Path(db_path).read_bytes() == before


class TestLoadFile:
    def test_load_file_does_not_modify_source(self, tmp_path):
        """Loading a native file through load_file leaves it byte-identical.

        Sits beside the load_db_file test rather than in test_file_io.py
        because it needs the same pre-patch seed fixture.
        """
        db_path, patches_subdir = _seed_v0_db_with_pending_patch(tmp_path)
        before = Path(db_path).read_bytes()

        # The returned connection is upgraded even though the file is not
        conn = load_file(db_path, patches_dir=patches_subdir)
        try:
            assert get_schema_version(conn) == 1
        finally:
            conn.close()
        assert Path(db_path).read_bytes() == before


class TestCreateDbVersion:
    def test_create_db_sets_version(self, tmp_path):
        """create_db stamps user_version to match get_latest_version."""
        db_path = str(tmp_path / "test.db")
        conn = create_db(db_path)
        assert get_schema_version(conn) == get_latest_version()
        conn.close()


class TestExportEntityIdPatch:
    def test_apply_patches_export_entity_id_patch_preserves_rows(self, tmp_path):
        """Rebuilding project and input_sample keeps rows, keys, and views."""
        conn, keys = _build_populated_db_before_patch(
            tmp_path, _EXPORT_ENTITY_ID_PATCH_NUM
        )
        apply_patches(conn)

        # Read back the rebuilt rows, a view joining through them, and the
        # connection state the runner must leave behind
        actual = {
            "projects": _fetch_all(
                conn,
                "SELECT project_idx, project_name, external_project_id, "
                "bioproject_accession, study_export_entity_id FROM project",
            ),
            "input_samples": _fetch_all(
                conn,
                "SELECT input_sample_idx, sample_name, biosample_accession, "
                "matrix_tube_id, biosample_export_entity_id FROM input_sample",
            ),
            "view_rows": _fetch_all(
                conn,
                "SELECT prepped_sample_idx, project_idx FROM prepped_sample_project",
            ),
            "fk_violations": _fetch_all(conn, "PRAGMA foreign_key_check"),
            "foreign_keys": _fetch_scalar(conn, "PRAGMA foreign_keys"),
            "version": get_schema_version(conn),
        }
        conn.close()

        expected = {
            "projects": [(keys["project_idx"], "proj1", "1", None, None)],
            "input_samples": [(keys["ins_idx"], "sample1", None, None, None)],
            "view_rows": [(keys["prs_idx"], keys["project_idx"])],
            "fk_violations": [],
            "foreign_keys": 1,
            "version": get_latest_version(),
        }
        assert actual == expected

    def test_apply_patches_export_entity_id_patch_keeps_autoincrement(self, tmp_path):
        """Ids of rows deleted before the rebuild are never reissued."""
        conn, keys = _build_populated_db_before_patch(
            tmp_path, _EXPORT_ENTITY_ID_PATCH_NUM
        )

        # Raise each high-water mark above the surviving rows, then delete
        doomed_project_idx = _helpers.seed_project(
            conn, project_name="proj2", external_project_id="2"
        )
        doomed_ins_idx = _helpers.seed_input_sample(
            conn, keys["plate_idx"], keys["project_idx"], sample_name="sample2"
        )
        conn.execute(
            "DELETE FROM input_sample WHERE input_sample_idx = ?", (doomed_ins_idx,)
        )
        conn.execute("DELETE FROM project WHERE project_idx = ?", (doomed_project_idx,))
        conn.commit()
        sequence_sql = "SELECT name, seq FROM sqlite_sequence WHERE name IN ('project', 'input_sample') ORDER BY name"  # noqa: E501
        before = _fetch_all(conn, sequence_sql)

        apply_patches(conn)
        after = _fetch_all(conn, sequence_sql)
        conn.close()

        expected = [("input_sample", doomed_ins_idx), ("project", doomed_project_idx)]
        assert (before, after) == (expected, expected)
