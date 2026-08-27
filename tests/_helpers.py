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

from run_preflight.db import get_preflight_data_facts

_DATA_DIR = Path(__file__).parent / "data"
LEGACY_DATA_DIR = _DATA_DIR / "legacy"
NATIVE_DATA_DIR = _DATA_DIR / "native"
GOOD_LEGACY_GLOB = "good_*.csv"
NATIVE_SNAPSHOT_SUFFIX = ".generated_snapshot.json"

# Content-derived fact tokens marking populated post-preflight data. A true
# preflight (loaded from most legacy CSVs) carries none of these and so has a
# bare filename; a fixture that adds a class of data carries the matching
# token(s) as a dot-delimited suffix, letting a consumer pick a fixture that
# already satisfies the stage their code needs. Facts are independent because
# sequencing placement is platform-specific (Illumina Lane is present at
# preflight; PacBio placement appears only post-flight) and can arrive
# separately from NCBI accessions.
FACT_ACCESSIONED = "accessioned"
FACT_PLACED = "placed"


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


def _split_top_level(body: str) -> list[str]:
    # Split on commas that sit outside all parentheses, so a CHECK/FK whose
    # own argument list contains commas is not split mid-constraint.
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _canonical_table_sql(sql: str | None) -> list[str]:
    """Return an order- and format-independent list of a table's definitions.

    PRAGMA table_info omits CHECK, COLLATE, and table-level constraints, and
    a column added by ALTER TABLE lands in the stored CREATE text in a
    different position and formatting than the same column written inline in
    schema.sql. Collapsing whitespace, splitting the parenthesized body on
    top-level commas, and sorting yields a form that surfaces those
    constraints yet compares equal regardless of column order or
    inline-vs-ALTER origin.
    """
    if sql is None:
        return []
    # Strip -- line comments before whitespace is collapsed: comments are not
    # schema structure, and flattening newlines first would merge a comment
    # into the following column/constraint definition.
    without_comments = re.sub(r"--[^\n]*", "", sql)
    collapsed = _normalize_sql(without_comments)
    open_idx = collapsed.find("(")
    close_idx = collapsed.rfind(")")
    if open_idx == -1 or close_idx == -1 or close_idx < open_idx:
        return [collapsed]
    body = collapsed[open_idx + 1 : close_idx]
    return sorted(_split_top_level(body))


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

    # Tables: canonical definition (captures CHECK/COLLATE/table constraints),
    # column structure, foreign keys, and full row contents
    cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    table_defs = cur.fetchall()
    for tname, tsql in table_defs:
        cols = cur.execute(f"PRAGMA table_info({tname})").fetchall()
        fks = cur.execute(f"PRAGMA foreign_key_list({tname})").fetchall()
        snapshot["tables"][tname] = {
            "definition": _canonical_table_sql(tsql),
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


def derive_native_facts(conn: sqlite3.Connection) -> list[str]:
    """Return the sorted content-derived fact tokens for a native DB.

    Maps the domain facts from get_preflight_data_facts to this suite's
    fixture-naming tokens: FACT_ACCESSIONED when any NCBI accession is
    populated, FACT_PLACED when any PacBio SMRT Cell placement is populated.
    A true preflight yields [].
    """
    data_facts = get_preflight_data_facts(conn)
    tokens: list[str] = []
    if data_facts.has_accessions:
        tokens.append(FACT_ACCESSIONED)
    if data_facts.has_pacbio_placement:
        tokens.append(FACT_PLACED)
    return sorted(tokens)


def native_stem(base_stem: str, facts: list[str]) -> str:
    """Build a native fixture stem: source stem plus sorted fact suffixes.

    A true preflight (empty *facts*) keeps the bare source stem; each fact
    token is appended as a dot-delimited suffix.
    """
    suffix = "".join(f".{fact}" for fact in sorted(facts))
    return f"{base_stem}{suffix}"


def facts_in_native_stem(stem: str) -> list[str]:
    """Return the fact tokens declared in a native fixture *stem*.

    Source stems contain no dots, so any dot-delimited tokens after the first
    segment are fact tokens.
    """
    segments = stem.split(".")
    return sorted(segments[1:])


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


def seed_amplicon_sample(
    conn: sqlite3.Connection,
    prs_idx: int,
    *,
    barcode: str = "ACGTACGTACGT",
) -> int:
    """Insert one amplicon_sample row; return amplicon_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO amplicon_sample (prepped_sample_idx, barcode) VALUES (?, ?)",
        (prs_idx, barcode),
    )
    return cur.lastrowid
