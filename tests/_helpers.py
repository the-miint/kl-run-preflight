"""Shared DB helpers for the test suite.

Provides two families of helper. The seed functions encapsulate the
project → plate → run → input_sample → compression_sample →
prepped_sample → platform_sample insert chain so tests do not duplicate
it; each takes an open SQLite connection and returns the surrogate id of
the row it inserted. The snapshot function captures a deterministic,
JSON-serializable image of a database's structure and contents for
byte-comparison against a persisted expectation.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
LEGACY_DATA_DIR = _DATA_DIR / "legacy"
NATIVE_DATA_DIR = _DATA_DIR / "native"
GOOD_LEGACY_GLOB = "good_*.csv"
NATIVE_SNAPSHOT_SUFFIX = ".generated_snapshot.json"


@contextlib.contextmanager
def open_db(db_path: str):
    """Open a raw connection to *db_path* with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _normalize_sql(sql: str | None) -> str:
    """Collapse whitespace runs so functionally equal SQL compares equal.

    SQLite's stored CREATE statements may differ from the original file
    in whitespace after ALTER TABLE rewrites; collapsing runs of
    whitespace into single spaces makes equivalent definitions equal.
    """
    if sql is None:
        return ""
    return re.sub(r"\s+", " ", sql).strip()


def _row_sort_key(row: tuple) -> list[tuple[bool, str, str]]:
    # Deterministic, None-safe ordering for heterogeneous row tuples:
    # NULLs first, then by type name, then by string value
    return [(v is None, type(v).__name__, str(v)) for v in row]


def capture_db_snapshot(conn: sqlite3.Connection) -> dict:
    """Return a deterministic snapshot of DB structure and full contents.

    The snapshot is keyed by object name and every row list is sorted,
    so neither object-creation order nor row-insertion order affects
    equality. All values are JSON-serializable, so a caller can persist
    a snapshot and byte-compare it against a later capture.
    """
    snapshot: dict = {
        "tables": {},
        "indexes": {},
        "triggers": {},
        "views": {},
        "data": {},
    }
    cur = conn.cursor()

    # Tables: column structure, foreign keys, and full row contents
    cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    table_names = [r[0] for r in cur.fetchall()]
    for tname in table_names:
        cols = cur.execute(f"PRAGMA table_info({tname})").fetchall()
        fks = cur.execute(f"PRAGMA foreign_key_list({tname})").fetchall()
        snapshot["tables"][tname] = {
            "columns": [tuple(c) for c in cols],
            "foreign_keys": sorted((tuple(f) for f in fks), key=_row_sort_key),
        }
        # Sort rows so insertion order does not affect equality
        rows = cur.execute(f"SELECT * FROM {tname}").fetchall()
        snapshot["data"][tname] = sorted((tuple(r) for r in rows), key=_row_sort_key)

    # Indexes: include auto-indexes from PK / UNIQUE constraints
    cur.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' ORDER BY name"
    )
    for name, tbl_name, sql in cur.fetchall():
        cols = cur.execute(f"PRAGMA index_info({name})").fetchall()
        snapshot["indexes"][name] = {
            "table": tbl_name,
            "sql": _normalize_sql(sql),
            "columns": [tuple(c) for c in cols],
        }

    # Triggers and views: compare normalized SQL bodies
    cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    )
    for name, sql in cur.fetchall():
        snapshot["triggers"][name] = _normalize_sql(sql)

    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name")
    for name, sql in cur.fetchall():
        snapshot["views"][name] = _normalize_sql(sql)

    return snapshot


def snapshot_to_json(snapshot: dict) -> str:
    """Serialize a snapshot to canonical JSON text.

    Stable key order and a trailing newline make two independently
    produced snapshots byte-comparable, so a persisted image and a
    freshly captured one can be diffed directly.
    """
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def seed_project_and_plate(conn: sqlite3.Connection) -> tuple[int, int]:
    """Insert one project and one input_plate; return (project_idx, plate_idx)."""
    project_idx = seed_project(conn)
    plate_idx = seed_plate(conn, project_idx)
    return project_idx, plate_idx


def seed_project(
    conn: sqlite3.Connection,
    *,
    project_name: str = "proj1",
    external_project_id: str | None = "1",
    bioproject_accession: str | None = None,
) -> int:
    """Insert one project.

    Either *external_project_id* or *bioproject_accession* may be None,
    but the row is rejected if both are NULL.
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO project "
        "(project_name, external_project_id, human_filtering, "
        " library_construction_protocol, experiment_design_description, "
        " bioproject_accession) "
        "VALUES (?, ?, 1, 'proto', 'desc', ?)",
        (project_name, external_project_id, bioproject_accession),
    )
    return cur.lastrowid


def seed_plate(
    conn: sqlite3.Connection,
    primary_project_idx: int,
    *,
    plate_name: str = "plate1",
) -> int:
    """Insert one input_plate; return plate_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO input_plate (plate_name, primary_project_idx) VALUES (?, ?)",
        (plate_name, primary_project_idx),
    )
    return cur.lastrowid


def seed_processing_run(
    conn: sqlite3.Connection,
    *,
    experiment_name: str = "exp1",
    run_date: str = "2025-01-01",
    instrument_type: str = "Unknown",
    platform_idx: int = 1,
) -> int:
    """Insert one processing_run row; return its run_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO processing_run "
        "(experiment_name, run_date, instrument_type, "
        " assay_type_idx, platform_idx) "
        "VALUES (?, ?, ?, 1, ?)",
        (experiment_name, run_date, instrument_type, platform_idx),
    )
    return cur.lastrowid


def seed_illumina_run_config(
    conn: sqlite3.Connection,
    run_idx: int,
    *,
    mask_short_reads: str | None = None,
    override_cycles: str | None = None,
) -> None:
    """Insert the matching illumina_run row for *run_idx*."""
    conn.execute(
        "INSERT INTO illumina_run "
        "(run_idx, read1_length, read2_length, mask_short_reads, override_cycles) "
        "VALUES (?, 151, 151, ?, ?)",
        (run_idx, mask_short_reads, override_cycles),
    )


def seed_input_sample(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int | None,
    *,
    sample_name: str = "sample1",
    sample_type_name: str = "standard",
) -> int:
    """Insert one input_sample row; return input_sample_idx.

    Controls are seeded by passing ``project_idx=None`` together with a
    control *sample_type_name* (``extraction_blank`` or
    ``katharoseq_cells_positive_control``).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT sample_type_idx FROM sample_type WHERE name = ?",
        (sample_type_name,),
    )
    (st_idx,) = cur.fetchone()
    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
        "VALUES (?, ?, ?, ?)",
        (sample_name, plate_idx, project_idx, st_idx),
    )
    return cur.lastrowid


def seed_compression_sample(
    conn: sqlite3.Connection,
    run_idx: int,
    input_sample_idx: int,
    *,
    well: str = "A1",
) -> int:
    """Insert one compression_sample row; return compression_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO compression_sample "
        "(run_idx, input_sample_idx, compression_well) "
        "VALUES (?, ?, ?)",
        (run_idx, input_sample_idx, well),
    )
    return cur.lastrowid


def seed_prepped_sample(
    conn: sqlite3.Connection,
    compression_sample_idx: int,
    *,
    well: str = "A1",
    sample_name: str | None = None,
) -> int:
    """Insert one prepped_sample row; return prepped_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO prepped_sample "
        "(compression_sample_idx, prepped_well, sample_name) "
        "VALUES (?, ?, ?)",
        (compression_sample_idx, well, sample_name),
    )
    return cur.lastrowid


def seed_sample_chain(
    conn: sqlite3.Connection,
    plate_idx: int,
    project_idx: int | None,
    run_idx: int,
    *,
    sample_name: str = "sample1",
    sample_type_name: str = "standard",
    well: str = "A1",
    prs_name: str | None = None,
) -> tuple[int, int, int]:
    """Insert input_sample + compression_sample + prepped_sample.

    Returns (input_sample_idx, compression_sample_idx, prepped_sample_idx).
    Pass ``project_idx=None`` with a control *sample_type_name* to seed
    a control chain.
    """
    ins_idx = seed_input_sample(
        conn,
        plate_idx,
        project_idx,
        sample_name=sample_name,
        sample_type_name=sample_type_name,
    )
    cs_idx = seed_compression_sample(conn, run_idx, ins_idx, well=well)
    prs_idx = seed_prepped_sample(conn, cs_idx, well=well, sample_name=prs_name)
    return ins_idx, cs_idx, prs_idx


def seed_illumina_sample(
    conn: sqlite3.Connection,
    prs_idx: int,
    *,
    i7_index_id: str = "i7",
    i7_seq: str = "AAAA",
    i5_index_id: str = "i5",
    i5_seq: str = "CCCC",
    lane: int | None = None,
) -> int:
    """Insert one illumina_sample row; return illumina_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO illumina_sample "
        "(prepped_sample_idx, i7_index_id, i7_sequence, "
        " i5_index_id, i5_sequence, lane) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (prs_idx, i7_index_id, i7_seq, i5_index_id, i5_seq, lane),
    )
    return cur.lastrowid


def seed_tellseq_sample(
    conn: sqlite3.Connection,
    prs_idx: int,
    *,
    barcode_id: str = "BC1",
    lane: int | None = None,
) -> int:
    """Insert one tellseq_sample row; return tellseq_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tellseq_sample "
        "(prepped_sample_idx, barcode_id, lane) "
        "VALUES (?, ?, ?)",
        (prs_idx, barcode_id, lane),
    )
    return cur.lastrowid


def seed_pacbio_sample(
    conn: sqlite3.Connection,
    prs_idx: int,
    *,
    barcode_id: str = "BC1",
    smrt_cell_well_sample_id: str | None = None,
) -> int:
    """Insert one pacbio_sample row; return pacbio_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pacbio_sample "
        "(prepped_sample_idx, barcode_id, smrt_cell_well_sample_id) "
        "VALUES (?, ?, ?)",
        (prs_idx, barcode_id, smrt_cell_well_sample_id),
    )
    return cur.lastrowid
