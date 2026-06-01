"""Shared DB-seed helpers for the test suite.

These functions encapsulate the project → plate → run → input_sample →
compression_sample → prepped_sample → platform_sample insert chain so
tests do not duplicate it. Each helper takes an open SQLite connection
and returns the surrogate id of the row it inserted.
"""

from __future__ import annotations

import sqlite3


def seed_project_and_plate(conn: sqlite3.Connection) -> tuple[int, int]:
    """Insert one project and one input_plate; return (project_idx, plate_idx)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO project "
        "(project_name, external_project_id, human_filtering, "
        " library_construction_protocol, experiment_design_description) "
        "VALUES ('proj1', '1', 1, 'proto', 'desc')"
    )
    project_idx = cur.lastrowid
    cur.execute(
        "INSERT INTO input_plate (plate_name, primary_project_idx) "
        "VALUES ('plate1', ?)",
        (project_idx,),
    )
    plate_idx = cur.lastrowid
    return project_idx, plate_idx


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
    project_idx: int,
    *,
    sample_name: str = "sample1",
) -> int:
    """Insert one input_sample row; return input_sample_idx."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO input_sample "
        "(sample_name, input_plate_idx, project_idx, sample_type_idx) "
        "VALUES (?, ?, ?, 1)",
        (sample_name, plate_idx, project_idx),
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
    project_idx: int,
    run_idx: int,
    *,
    sample_name: str = "sample1",
    well: str = "A1",
    prs_name: str | None = None,
) -> tuple[int, int, int]:
    """Insert input_sample + compression_sample + prepped_sample.

    Returns (input_sample_idx, compression_sample_idx, prepped_sample_idx).
    """
    ins_idx = seed_input_sample(
        conn, plate_idx, project_idx, sample_name=sample_name
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
