"""Post-fill update operations for filled SQLite run preflights.

Each operation writes one row to ``change_log`` per modified domain
row, capturing the prior and new values plus an optional caller-
supplied reason.
"""

from __future__ import annotations

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


def set_biosample_accession(
    conn: sqlite3.Connection,
    sample_name: str,
    accession: str | None,
    reason: str | None = None,
) -> None:
    """Set biosample_accession on the input_sample matching sample_name.

    *sample_name* is resolved via ``COALESCE(prepped_sample.sample_name,
    input_sample.sample_name)`` — callers may pass either an
    input-sample name or a per-replicate alias. All replicate aliases
    of a single biological sample resolve to the same input_sample and
    update the shared accession. *accession* may be None to clear.

    Raises:
        ValueError: If no input_sample matches *sample_name*, or if
            multiple distinct input_samples match (ambiguous).
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


def update_lane(
    conn: sqlite3.Connection,
    platform: str,
    from_lane: int | None,
    to_lane: int | None,
    reason: str | None = None,
) -> int:
    """Bulk-reassign lane values on a platform-specific sample table.

    Every row whose current lane equals *from_lane* (NULL is a value)
    is updated to *to_lane*. *platform* must be ``"illumina"`` or
    ``"tellseq"``. Returns the number of rows updated.

    Raises:
        ValueError: For an unsupported platform, a post-update state
            that mixes NULL and non-NULL lane values, or a collision
            with the unique ``(prepped_sample_idx, lane)`` index.
    """
    if platform not in _PLATFORM_TABLES:
        supported = sorted(_PLATFORM_TABLES.keys())
        raise ValueError(
            f"Unsupported platform {platform!r}; lane updates are only "
            f"defined for {supported}"
        )
    table, pk_col = _PLATFORM_TABLES[platform]
    cur = conn.cursor()

    # Verify post-update lane uniformity: rows whose current lane != from_lane
    # keep their value and break uniformity if their null-ness differs from to_lane's.
    if to_lane is None:
        null_filter, would_state, target = "lane IS NOT NULL", "non-NULL", "NULL"
    else:
        null_filter, would_state, target = "lane IS NULL", "NULL", repr(to_lane)
    cur.execute(
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE COALESCE(lane, -1) != COALESCE(?, -1) AND {null_filter}",
        (from_lane,),
    )
    offending = cur.fetchone()[0]
    if offending > 0:
        raise ValueError(
            f"Setting {table}.lane to {target} would leave {offending} "
            f"rows with {would_state} lane (uniformity violation)"
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
