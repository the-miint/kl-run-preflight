"""File-level read and write entry points for native (SQLite DB) and bcl-convert run preflight files."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import stat
import tempfile
from pathlib import Path

from .constants import (
    COL_INDEX,
    COL_INDEX2,
    COL_LANE,
    COL_SAMPLE_ID,
    COL_SAMPLE_NAME,
    COL_SAMPLE_PROJECT,
    FIELD_FILE_FORMAT_VERSION,
    IN_MEMORY_PATH,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SETTINGS,
    SQLITE_MAGIC,
)
from .db import get_illumina_sample_rows, get_illumina_settings
from .migrate import apply_patches

# The mode an ordinary open() requests for a new file, before the umask
# narrows it; mkstemp instead creates its file at 0600.
_DEFAULT_OUTPUT_FILE_MODE = 0o666


def _resolve_output_mode(target: Path) -> int:
    """Determine the permissions an ordinary write to *target* would leave.

    Args:
        target: Path being written, which need not exist.

    Returns:
        int: The existing file's own mode, or for a new file the default
        creation mode narrowed by the current umask.
    """
    # An existing target keeps whatever permissions it already carries
    try:
        target_stat = target.stat()
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None:
        existing_mode = stat.S_IMODE(target_stat.st_mode)
        return existing_mode

    # A new file gets the default creation mode less the process umask,
    # which is only readable by setting it and putting it back
    current_umask = os.umask(0)
    os.umask(current_umask)
    new_mode = _DEFAULT_OUTPUT_FILE_MODE & ~current_umask
    return new_mode


def _require_sqlite_header(head: bytes, subject: str) -> None:
    """Reject input that does not open with the SQLite file header.

    Args:
        head: Leading bytes of the candidate database, at least as many
            as the header itself.
        subject: How to name the rejected input in the message.

    Raises:
        ValueError: If *head* lacks the SQLite file header.
    """
    if not head.startswith(SQLITE_MAGIC):
        raise ValueError(f"{subject} is not a SQLite database (missing file header)")


def atomic_write(path: str, data: bytes | str) -> None:
    """Write *data* to *path* so that a failed call leaves it unchanged.

    The content is staged in a temporary file in the target's own
    directory and renamed into place, which is atomic within a
    filesystem. A failure at any point before the rename leaves an
    existing file at *path* untouched. An existing file's permissions
    carry over to the replacement; a new file gets the permissions an
    ordinary write would give it under the current umask.

    NB: the rename is atomic against what a concurrent reader sees, not
    against power loss. Nothing here is fsynced, so a crash can still
    lose a write this call has already reported as complete.

    Args:
        path: Filesystem path to write. Any existing file is replaced.
        data: Payload to write, as bytes or as text.
    """
    target = Path(path)

    # Stage beside the target so the rename stays on one filesystem
    handle, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    tmp_path = Path(tmp_name)

    # Fill the staged file, give it the mode a plain write would have
    # produced, then swap it in; anything that goes wrong from here on
    # discards the staged file rather than stranding it beside the target
    try:
        output_mode = _resolve_output_mode(target)
        if isinstance(data, bytes):
            tmp_path.write_bytes(data)
        else:
            tmp_path.write_text(data)
        os.chmod(tmp_path, output_mode)
        tmp_path.replace(target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


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
    atomic_write(csv_path, text)


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


def load_db_bytes(
    blob: bytes,
    patches_dir: Path | None = None,
) -> sqlite3.Connection:
    """Load a serialized SQLite database into a detached in-memory connection.

    The returned connection is independent of wherever *blob* came from:
    it sits at the latest schema version with foreign-key enforcement
    enabled, and nothing done to it reaches the original bytes. Caller
    owns and must close it, and must call save_db_file or
    dump_db_bytes to persist any change.

    Args:
        blob: The full contents of a SQLite database file.
        patches_dir: Directory to scan for patches.  Defaults to the
            built-in ``sql/patches/`` directory.

    Returns:
        sqlite3.Connection: A detached in-memory connection at the
        latest schema version.

    Raises:
        ValueError: If *blob* does not carry the SQLite file header.
        sqlite3.DatabaseError: If *blob* carries the header but is
            truncated or otherwise unreadable.
        SchemaVersionTooNewError: If the schema version exceeds the
            shipped patch set.
    """
    # Reject non-database input up front so the failure names the input
    # itself, rather than arriving as a bare "file is not a database" or,
    # for empty input, as MemoryError out of the deserialize
    _require_sqlite_header(blob, "blob")

    # Deserialize into a private in-memory DB and bring it to the latest
    # schema version; the patches land in memory, leaving *blob* untouched
    conn = sqlite3.connect(IN_MEMORY_PATH)
    try:
        conn.deserialize(blob)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_patches(conn, patches_dir)
    except Exception:
        conn.close()
        raise
    return conn


def load_db_file(
    db_path: str,
    patches_dir: Path | None = None,
) -> sqlite3.Connection:
    """Load a SQLite database file into a detached in-memory connection.

    The file at *db_path* is read once and never written: pending schema
    patches are applied to the in-memory copy, so a file behind the patch
    set stays that way on disk until the caller persists the connection
    with save_db_file. Caller owns and must close the connection.

    NB: Reading the raw bytes bypasses SQLite crash recovery, so a hot
    journal or WAL sidecar left by a crashed writer is ignored and the
    database loads in its un-rolled-back state.

    Args:
        db_path: Filesystem path to the SQLite database file.
        patches_dir: Directory to scan for patches.  Defaults to the
            built-in ``sql/patches/`` directory.

    Returns:
        sqlite3.Connection: A detached in-memory connection at the
        latest schema version.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
        ValueError: If the file is not a SQLite database.
        sqlite3.DatabaseError: If the file carries the SQLite header but
            is truncated or otherwise unreadable.
        SchemaVersionTooNewError: If the schema version exceeds the
            shipped patch set.
    """
    # Check the header off the first read so a large non-database input
    # is rejected by name instead of being pulled into memory first
    with Path(db_path).open("rb") as db_handle:
        head = db_handle.read(len(SQLITE_MAGIC))
        _require_sqlite_header(head, db_path)
        remainder = db_handle.read()
    blob = head + remainder

    conn = load_db_bytes(blob, patches_dir)
    return conn


def dump_db_bytes(conn: sqlite3.Connection) -> bytes:
    """Serialize a live SQLite connection to database file bytes.

    Works for both in-memory and file-backed source connections. The
    caller retains ownership of *conn*. The image is verbatim, including
    any do_not_use-flagged records and any changes not yet committed.

    Args:
        conn: An open SQLite source connection.

    Returns:
        bytes: The contents of an equivalent SQLite database file.
    """
    blob = conn.serialize()
    return blob


def save_db_file(conn: sqlite3.Connection, db_path: str) -> None:
    """Write a live SQLite connection out as a SQLite database file.

    This is the durability boundary for the connections the load entry
    points return: those are detached in-memory copies, so their contents
    reach disk only through this call. Works for both in-memory and
    file-backed source connections, and the caller retains ownership of
    *conn*. The copy is verbatim, including any do_not_use-flagged
    records and any changes not yet committed.

    Args:
        conn: An open SQLite source connection.
        db_path: Filesystem path at which the database file will be
            created. Any existing file is replaced atomically, and
            survives unchanged if the write fails.
    """
    blob = dump_db_bytes(conn)
    atomic_write(db_path, blob)
