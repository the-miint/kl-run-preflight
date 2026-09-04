"""Drift detection: schema_v0.sql + all patches must equal schema.sql.

This test prevents the canonical schema (``schema.sql``) and the patch
sequence (``sql/patches/NNN_*.{sql,py}``) from getting out of step.  Any
schema change must be applied to both ``schema.sql`` (so fresh databases
get the new schema) and as a patch (so existing databases can be brought
forward).  This test catches drift between the two paths by building one
database from each path and comparing full structure and content.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from run_preflight.constants import IN_MEMORY_PATH
from run_preflight.db import create_db
from run_preflight.migrate import apply_patches, get_latest_version

from ._helpers import capture_db_snapshot

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "src" / "run_preflight" / "sql"
_SCHEMA_V0 = _SCHEMA_DIR / "schema_v0.sql"


def _build_via_schema_sql() -> sqlite3.Connection:
    """Create an in-memory DB the same way production create_db does."""
    return create_db(IN_MEMORY_PATH)


def _build_via_baseline_and_patches() -> sqlite3.Connection:
    """Create an in-memory DB from schema_v0.sql, then apply all patches."""
    conn = sqlite3.connect(IN_MEMORY_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_V0.read_text())
    # Baseline starts at user_version 0; apply_patches brings it forward
    apply_patches(conn)
    return conn


@pytest.fixture
def snapshots():
    """Return (schema_sql_snapshot, baseline_plus_patches_snapshot)."""
    conn_schema = _build_via_schema_sql()
    conn_patches = _build_via_baseline_and_patches()
    try:
        yield (
            capture_db_snapshot(conn_schema),
            capture_db_snapshot(conn_patches),
        )
    finally:
        conn_schema.close()
        conn_patches.close()


class TestSchemaDrift:
    """schema_v0.sql + all patches must produce the same DB as schema.sql."""

    def test_schema_drift_user_version(self):
        """Both paths must stamp the database to the same user_version."""
        conn_schema = _build_via_schema_sql()
        conn_patches = _build_via_baseline_and_patches()
        try:
            v_schema = conn_schema.execute("PRAGMA user_version").fetchone()[0]
            v_patches = conn_patches.execute("PRAGMA user_version").fetchone()[0]
            assert v_schema == v_patches == get_latest_version()
        finally:
            conn_schema.close()
            conn_patches.close()

    def test_schema_drift_tables(self, snapshots):
        """Table set, columns, and FK constraints must match."""
        snap_schema, snap_patches = snapshots
        assert snap_patches["tables"] == snap_schema["tables"]

    def test_schema_drift_indexes(self, snapshots):
        """Index definitions and indexed columns must match."""
        snap_schema, snap_patches = snapshots
        assert snap_patches["indexes"] == snap_schema["indexes"]

    def test_schema_drift_triggers(self, snapshots):
        """Trigger SQL bodies must match (whitespace-normalized)."""
        snap_schema, snap_patches = snapshots
        assert snap_patches["triggers"] == snap_schema["triggers"]

    def test_schema_drift_views(self, snapshots):
        """View SQL bodies must match (whitespace-normalized)."""
        snap_schema, snap_patches = snapshots
        assert snap_patches["views"] == snap_schema["views"]

    def test_schema_drift_data(self, snapshots):
        """Seed-data rows in every table must match."""
        snap_schema, snap_patches = snapshots
        assert snap_patches["data"] == snap_schema["data"]
