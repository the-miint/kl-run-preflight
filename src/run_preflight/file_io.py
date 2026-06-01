"""Format-detecting entry point that opens either a legacy omnibus CSV or a SQLite database file."""

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
    FIELD_FILE_FORMAT_VERSION,
    FIELD_MASK_SHORT_READS,
    FIELD_OVERRIDE_CYCLES,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SETTINGS,
)
from .legacy.api import load_legacy_csv
from .migrate import open_db_file

# SQLite database files begin with this 16-byte magic header (see https://sqlite.org/fileformat.html)
_SQLITE_MAGIC = b"SQLite format 3\x00"


def save_bclconvert_v1_csv(conn: sqlite3.Connection, csv_path: str) -> None:
    """Write a minimal bcl-convert v1 sample sheet from the run in *conn*.

    *conn* must describe exactly one Illumina processing run with at
    least one illumina_sample row (i.e. neither PacBio nor TellSeq).
    Sample_ID values are emitted as the integer illumina_sample_idx;
    index/index2 are emitted exactly as stored on illumina_sample.
    The Lane column appears in [Data] only when illumina_sample.lane
    is non-null (lane uniformity is enforced by trigger). Settings keys
    appear in [Settings] only when their illumina_run column is non-null.

    Raises:
        ValueError: If *conn* contains zero or multiple processing runs,
            or if the run has no illumina_sample rows.
    """
    # Resolve the one run and pull all needed data before any formatting
    run_idx = _get_single_run_idx(conn)
    data_rows = _fetch_illumina_sample_rows(conn, run_idx)
    if not data_rows:
        raise ValueError(
            "run has no illumina_sample rows; cannot write bcl-convert sample sheet"
        )
    settings = _fetch_illumina_settings(conn, run_idx)

    # Format the CSV text in a DB-free path, then write it out
    text = _format_bclconvert_v1(settings, data_rows)
    Path(csv_path).write_text(text)


# ---------------------------------------------------------------------------
# Data fetchers (DB-only)
# ---------------------------------------------------------------------------


def _get_single_run_idx(conn: sqlite3.Connection) -> int:
    """Return the run_idx of the sole processing_run in *conn*.

    Raises:
        ValueError: If zero or multiple processing_run rows exist.
    """
    run_idxs = [row[0] for row in conn.execute("SELECT run_idx FROM processing_run")]
    if len(run_idxs) != 1:
        raise ValueError(f"Expected exactly one processing run, found {len(run_idxs)}")
    return run_idxs[0]


def _fetch_illumina_sample_rows(
    conn: sqlite3.Connection, run_idx: int
) -> list[tuple[int, int | None, str, str]]:
    """Return illumina_sample data tuples for the run, in deterministic order.

    Each tuple is (illumina_sample_idx, lane, i7_sequence, i5_sequence).
    Rows are ordered by illumina_sample_idx so that legacy-loaded
    databases emit Sample_ID values matching the source CSV row order.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT ils.illumina_sample_idx, ils.lane, "
        "       ils.i7_sequence, ils.i5_sequence "
        "FROM illumina_sample ils "
        "JOIN prepped_sample prs "
        "  ON ils.prepped_sample_idx = prs.prepped_sample_idx "
        "JOIN compression_sample cs "
        "  ON prs.compression_sample_idx = cs.compression_sample_idx "
        "WHERE cs.run_idx = ? "
        "ORDER BY ils.illumina_sample_idx",
        (run_idx,),
    )
    return cur.fetchall()


def _fetch_illumina_settings(
    conn: sqlite3.Connection, run_idx: int
) -> dict[str, str | None]:
    """Return a settings_dict for the run keyed by [Settings] key name.

    Values are None for any column that is NULL on illumina_run.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT mask_short_reads, override_cycles FROM illumina_run WHERE run_idx = ?",
        (run_idx,),
    )
    mask_short_reads, override_cycles = cur.fetchone()
    return {
        FIELD_MASK_SHORT_READS: mask_short_reads,
        FIELD_OVERRIDE_CYCLES: override_cycles,
    }


# ---------------------------------------------------------------------------
# Pure formatter (no DB access)
# ---------------------------------------------------------------------------


def _format_bclconvert_v1(
    settings: dict[str, str | None],
    data_rows: list[tuple[int, int | None, str, str]],
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

    # [Data] — Lane column included only when illumina_sample.lane is non-null
    include_lane = data_rows[0][1] is not None
    writer.writerow([f"[{SECTION_DATA}]"])
    if include_lane:
        writer.writerow([COL_LANE, COL_SAMPLE_ID, COL_INDEX, COL_INDEX2])
        for ils_idx, lane, i7_seq, i5_seq in data_rows:
            writer.writerow([lane, ils_idx, i7_seq, i5_seq])
    else:
        writer.writerow([COL_SAMPLE_ID, COL_INDEX, COL_INDEX2])
        for ils_idx, _lane, i7_seq, i5_seq in data_rows:
            writer.writerow([ils_idx, i7_seq, i5_seq])

    return output.getvalue()


def open_file(path: str) -> sqlite3.Connection:
    """Open a run preflight from either a legacy omnibus CSV or a SQLite DB file.

    Detects the format from the file's first 16 bytes: files beginning
    with the SQLite magic header are opened via *open_db_file*; everything
    else is treated as a legacy omnibus CSV via *load_legacy_csv*. Caller
    owns and must close the returned connection.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file is detected as legacy CSV but fails parsing
            or validation (re-raised from load_legacy_csv).
    """
    # Confirm the file exists before any read attempt so the error is unambiguous
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {path}")

    # Read just enough bytes to identify the SQLite magic header
    with p.open("rb") as fh:
        head = fh.read(len(_SQLITE_MAGIC))

    # Dispatch on detected format
    if head == _SQLITE_MAGIC:
        return open_db_file(path)
    return load_legacy_csv(path)
