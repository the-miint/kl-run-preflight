"""Post-fill update operations for filled SQLite run preflights.

These operations are intentionally scoped: they update specific fields
on an already-populated database without touching unrelated state.
Each operation writes one row to ``change_log`` per modified domain
row, capturing the prior and new values plus an optional
caller-supplied reason.

Supported operations:

- ``set_biosample_accession``: set ``input_sample.biosample_accession``
  by looking up an effective Sample_Name (matches the CSV-side value
  ``COALESCE(prs.sample_name, ins.sample_name)``).
- ``update_lane``: bulk-reassign ``illumina_sample.lane`` or
  ``tellseq_sample.lane`` from one value (or NULL) to another while
  preserving the lane-uniformity invariant and the per-prepped-sample
  uniqueness index.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3

from .constants import (
    DB_COL_BIOSAMPLE_ACCESSION,
    DB_COL_LANE,
    TABLE_CHANGE_LOG,
    TABLE_ILLUMINA_SAMPLE,
    TABLE_INPUT_SAMPLE,
    TABLE_TELLSEQ_SAMPLE,
    UPDATE_PLATFORM_ILLUMINA,
    UPDATE_PLATFORM_TELLSEQ,
)
from .migrate import open_db

# Map platform strings to (table_name, primary_key_column).  Used to
# dispatch update_lane to the correct platform-specific sample table.
_PLATFORM_TABLES = {
    UPDATE_PLATFORM_ILLUMINA: (TABLE_ILLUMINA_SAMPLE, "illumina_sample_idx"),
    UPDATE_PLATFORM_TELLSEQ: (TABLE_TELLSEQ_SAMPLE, "tellseq_sample_idx"),
}


def _to_audit_value(value: object) -> str | None:
    """Convert a value for storage in change_log's TEXT columns.

    None stays None (stored as SQL NULL); everything else is
    stringified so the column consistently holds either NULL or TEXT.
    """
    return None if value is None else str(value)


def _log_change(
    conn: sqlite3.Connection,
    table_name: str,
    row_idx: int,
    column_name: str,
    old_value: object,
    new_value: object,
    reason: str | None,
) -> None:
    """Insert one row into change_log capturing a single column change."""
    conn.execute(
        f"INSERT INTO {TABLE_CHANGE_LOG} "
        "(table_name, row_idx, column_name, old_value, new_value, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            table_name,
            row_idx,
            column_name,
            _to_audit_value(old_value),
            _to_audit_value(new_value),
            reason,
        ),
    )


# ---------------------------------------------------------------------------
# set_biosample_accession
# ---------------------------------------------------------------------------


def set_biosample_accession(
    db_path: str | os.PathLike,
    sample_name: str,
    accession: str | None,
    reason: str | None = None,
) -> None:
    """Set biosample_accession on the input_sample matching sample_name.

    Opens *db_path* via ``migrate.open_db`` so any pending schema
    patches are applied before the update runs, and closes the
    connection afterward.

    Args:
        db_path: Filesystem path to the SQLite run-preflight file.
        sample_name: The CSV-effective Sample_Name to match.  Resolved
            via ``COALESCE(prepped_sample.sample_name,
            input_sample.sample_name)`` so callers may pass either an
            input-sample name or a per-replicate alias.  All replicate
            aliases of a single biological sample resolve to the same
            ``input_sample`` and update the shared accession.
        accession: The new BioSample accession.  None clears it.
        reason: Optional caller-supplied note recorded in change_log.

    Raises:
        ValueError: If no input_sample matches *sample_name*, or if
            multiple distinct input_samples match (ambiguous name).
    """
    with contextlib.closing(open_db(str(db_path))) as conn:
        _set_biosample_accession(conn, sample_name, accession, reason)


def _set_biosample_accession(
    conn: sqlite3.Connection,
    sample_name: str,
    accession: str | None,
    reason: str | None,
) -> None:
    """Connection-scoped implementation of set_biosample_accession.

    Tests use this directly against an in-memory connection to avoid
    file I/O.  External callers should use the path-based public
    function so that schema patches are guaranteed to be applied.
    """
    cur = conn.cursor()

    # Resolve effective Sample_Name to a unique input_sample.  DISTINCT
    # collapses replicate rows that share one input_sample.
    cur.execute(
        """
        SELECT DISTINCT ins.input_sample_idx, ins.biosample_accession
        FROM input_sample ins
        JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
        JOIN prepped_sample prs ON prs.compression_sample_idx = cs.compression_sample_idx
        WHERE COALESCE(prs.sample_name, ins.sample_name) = ?
        """,
        (sample_name,),
    )
    matches = cur.fetchall()
    if not matches:
        raise ValueError(f"No input_sample matches Sample_Name {sample_name!r}")
    if len(matches) > 1:
        raise ValueError(
            f"Sample_Name {sample_name!r} is ambiguous; resolves to "
            f"{len(matches)} distinct input_samples"
        )

    input_sample_idx, old_accession = matches[0]

    # Apply the update and write the audit row in a single transaction.
    try:
        cur.execute(
            f"UPDATE {TABLE_INPUT_SAMPLE} "
            f"SET {DB_COL_BIOSAMPLE_ACCESSION} = ? WHERE input_sample_idx = ?",
            (accession, input_sample_idx),
        )
        _log_change(
            conn,
            TABLE_INPUT_SAMPLE,
            input_sample_idx,
            DB_COL_BIOSAMPLE_ACCESSION,
            old_accession,
            accession,
            reason,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# update_lane
# ---------------------------------------------------------------------------


def update_lane(
    db_path: str | os.PathLike,
    platform: str,
    from_lane: int | None,
    to_lane: int | None,
    reason: str | None = None,
) -> int:
    """Bulk-reassign lane values on a platform-specific sample table.

    Opens *db_path* via ``migrate.open_db`` so any pending schema
    patches are applied before the update runs, and closes the
    connection afterward.

    Every row whose current lane equals *from_lane* (NULL is treated as
    a value) is updated to *to_lane*.  The operation rejects on:

    - an unsupported platform (only ``"illumina"`` and ``"tellseq"`` are
      supported; pacbio_sample has no lane column)
    - a post-update state that would mix NULL and non-NULL lane values
      across the platform table (lane-uniformity violation)
    - a post-update state that would collide with the unique
      ``(prepped_sample_idx, COALESCE(lane, -1))`` index (i.e. a
      prepped_sample already has a row at *to_lane*)

    Args:
        db_path: Filesystem path to the SQLite run-preflight file.
        platform: Either ``"illumina"`` or ``"tellseq"``.
        from_lane: The current lane value to match.  None matches NULL.
        to_lane: The new lane value.  None sets the column to NULL.
        reason: Optional caller-supplied note recorded in change_log
            (one row per affected platform-sample row).

    Returns:
        int: The number of platform-sample rows updated.

    Raises:
        ValueError: For any of the rejection conditions described above.
    """
    with contextlib.closing(open_db(str(db_path))) as conn:
        return _update_lane(conn, platform, from_lane, to_lane, reason)


def _update_lane(
    conn: sqlite3.Connection,
    platform: str,
    from_lane: int | None,
    to_lane: int | None,
    reason: str | None,
) -> int:
    """Connection-scoped implementation of update_lane.

    Tests use this directly against an in-memory connection to avoid
    file I/O.  External callers should use the path-based public
    function so that schema patches are guaranteed to be applied.
    """
    if platform not in _PLATFORM_TABLES:
        supported = sorted(_PLATFORM_TABLES.keys())
        raise ValueError(
            f"Unsupported platform {platform!r}; lane updates are only "
            f"defined for {supported}"
        )
    table, pk_col = _PLATFORM_TABLES[platform]
    cur = conn.cursor()

    # Verify post-update lane uniformity: the platform table must be
    # uniformly NULL or uniformly non-NULL after the update.
    if to_lane is None:
        # Rows not at from_lane that are currently non-NULL would
        # remain non-NULL while rows at from_lane become NULL — mixed.
        cur.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE COALESCE(lane, -1) != COALESCE(?, -1) "
            "AND lane IS NOT NULL",
            (from_lane,),
        )
        offending = cur.fetchone()[0]
        if offending > 0:
            raise ValueError(
                f"Setting {table}.lane to NULL would leave {offending} "
                f"rows with non-NULL lane (uniformity violation)"
            )
    else:
        # Rows not at from_lane that are currently NULL would remain
        # NULL while rows at from_lane become non-NULL — mixed.
        cur.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE COALESCE(lane, -1) != COALESCE(?, -1) "
            "AND lane IS NULL",
            (from_lane,),
        )
        offending = cur.fetchone()[0]
        if offending > 0:
            raise ValueError(
                f"Setting {table}.lane to {to_lane!r} would leave "
                f"{offending} rows with NULL lane (uniformity violation)"
            )

    # Verify the unique (prepped_sample_idx, lane) index will not
    # collide.  When from_lane == to_lane there is no logical change.
    if from_lane != to_lane:
        cur.execute(
            f"SELECT COUNT(DISTINCT prepped_sample_idx) FROM {table} "
            "WHERE COALESCE(lane, -1) = COALESCE(?, -1) "
            "AND prepped_sample_idx IN ("
            f"  SELECT prepped_sample_idx FROM {table} "
            "  WHERE COALESCE(lane, -1) = COALESCE(?, -1)"
            ")",
            (to_lane, from_lane),
        )
        collisions = cur.fetchone()[0]
        if collisions > 0:
            raise ValueError(
                f"Cannot move lane {from_lane!r} -> {to_lane!r}: "
                f"{collisions} prepped_sample(s) already have a row at "
                f"lane {to_lane!r}"
            )

    # Capture affected rows for audit-log entries before the update.
    cur.execute(
        f"SELECT {pk_col}, lane FROM {table} "
        "WHERE COALESCE(lane, -1) = COALESCE(?, -1)",
        (from_lane,),
    )
    affected = cur.fetchall()

    # Apply update and log per-row audit entries in a single transaction.
    try:
        cur.execute(
            f"UPDATE {table} SET lane = ? WHERE COALESCE(lane, -1) = COALESCE(?, -1)",
            (to_lane, from_lane),
        )
        for row_idx, old_lane in affected:
            _log_change(
                conn,
                table,
                row_idx,
                DB_COL_LANE,
                old_lane,
                to_lane,
                reason,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return len(affected)
