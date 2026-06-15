"""File-level read and write entry points for native (SQLite DB) and bcl-convert run preflight files."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from .constants import (
    COL_INDEX,
    COL_INDEX2,
    COL_LANE,
    COL_SAMPLE_ID,
    COL_SAMPLE_NAME,
    COL_SAMPLE_PROJECT,
    FIELD_FILE_FORMAT_VERSION,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SETTINGS,
)
from .db import get_illumina_sample_rows, get_illumina_settings
from .migrate import apply_patches


def save_bclconvert_v1_csv(
    conn: sqlite3.Connection,
    csv_path: str,
    include_sample_name: bool = False,
    *,
    include_do_not_use: bool = False,
) -> None:
    """Write a minimal bcl-convert v1 sample sheet from the run in *conn*.

    *conn* must describe exactly one Illumina processing run with at
    least one illumina_sample row. Sample_ID is emitted as the integer
    illumina_sample_idx; index/index2 are emitted exactly as stored.
    Sample_Project resolves to the input_sample's project name (or the
    plate's primary project for controls). Lane is included in [Data]
    only when illumina_sample.lane is non-null; Settings keys appear
    only when their illumina_run column is non-null. When
    *include_sample_name* is True, the raw effective sample_name is
    emitted as a Sample_Name column immediately after Sample_ID.

    Samples flagged do_not_use are excluded unless *include_do_not_use*
    is True.

    Raises:
        ValueError: If *conn* lacks exactly one processing run, or has
            no illumina_sample rows.
    """
    # Pull all needed data before any formatting; do_not_use-flagged
    # samples are omitted unless the caller opts in.
    data_rows = get_illumina_sample_rows(conn, include_do_not_use=include_do_not_use)
    if not data_rows:
        raise ValueError(
            "run has no illumina_sample rows; cannot write bcl-convert sample sheet"
        )
    settings = get_illumina_settings(conn)

    # Format the CSV text in a DB-free path, then write it out
    text = _format_bclconvert_v1(settings, data_rows, include_sample_name)
    Path(csv_path).write_text(text)


def _format_bclconvert_v1(
    settings: dict[str, str | None],
    data_rows: list[tuple[int, int | None, str, str, str, str]],
    include_sample_name: bool,
) -> str:
    """Build a bcl-convert v1 sample sheet from pre-fetched data."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    # [Header]
    writer.writerow([f"[{SECTION_HEADER}]"])
    writer.writerow([FIELD_FILE_FORMAT_VERSION, "1"])
    writer.writerow([])

    # [Settings] — emitted only when at least one key has a non-null value
    active_settings = [(k, v) for k, v in settings.items() if v is not None]
    if active_settings:
        writer.writerow([f"[{SECTION_SETTINGS}]"])
        for key, value in active_settings:
            writer.writerow([key, value])
        writer.writerow([])

    # [Data] — Lane column included only when illumina_sample.lane is non-null;
    # Sample_Name column included only when caller opted in; Sample_Project is
    # always the final column.  Optional columns are spliced into both header
    # and data rows via parallel segment lists keyed off the same flags.
    include_lane = data_rows[0][1] is not None
    lane_prefix: list = [COL_LANE] if include_lane else []
    name_header: list = [COL_SAMPLE_NAME] if include_sample_name else []
    writer.writerow([f"[{SECTION_DATA}]"])
    writer.writerow(
        lane_prefix
        + [COL_SAMPLE_ID]
        + name_header
        + [COL_INDEX, COL_INDEX2, COL_SAMPLE_PROJECT]
    )
    for ils_idx, lane, i7_seq, i5_seq, project_name, sample_name in data_rows:
        row_prefix: list = [lane] if include_lane else []
        name_cell: list = [sample_name] if include_sample_name else []
        writer.writerow(
            row_prefix + [ils_idx] + name_cell + [i7_seq, i5_seq, project_name]
        )

    return output.getvalue()


def open_db_file(
    db_path: str,
    patches_dir: Path | None = None,
) -> sqlite3.Connection:
    """Open an existing SQLite database file and apply any pending patches.

    Enables foreign-key enforcement, checks the schema version, and
    applies patches as needed.

    Args:
        db_path: Filesystem path to the SQLite database file.
        patches_dir: Directory to scan for patches.  Defaults to the
            built-in ``sql/patches/`` directory.

    Returns:
        sqlite3.Connection: An open connection at the latest schema
        version with foreign-key enforcement enabled.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    apply_patches(conn, patches_dir)
    return conn


def save_db_file(conn: sqlite3.Connection, db_path: str) -> None:
    """Serialize a live SQLite connection to a database file.

    Works for both in-memory and file-backed source connections. The
    caller retains ownership of *conn*. The copy is verbatim, including
    any do_not_use-flagged records.

    Args:
        conn: An open SQLite source connection.
        db_path: Filesystem path at which the database file will be
            created. Any existing file is overwritten.
    """
    target = sqlite3.connect(db_path)
    try:
        conn.backup(target)
    finally:
        target.close()
