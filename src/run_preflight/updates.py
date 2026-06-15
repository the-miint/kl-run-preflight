"""Post-fill update operations for filled SQLite run preflights.

Each operation writes one row to ``change_log`` per modified domain
row, capturing the prior and new values plus an optional caller-
supplied reason.
"""

from __future__ import annotations

import sqlite3
from typing import Literal, get_args

from .constants import (
    DB_COL_BIOPROJECT_ACCESSION,
    DB_COL_BIOSAMPLE_ACCESSION,
    DB_COL_DO_NOT_USE,
    DB_COL_EXTERNAL_PROJECT_ID,
    DB_COL_ILLUMINA_SAMPLE_IDX,
    DB_COL_INPUT_SAMPLE_IDX,
    DB_COL_LANE,
    DB_COL_PREPPED_SAMPLE_IDX,
    DB_COL_PROJECT_IDX,
    DB_COL_PROJECT_NAME,
    DB_COL_RUN_IDX,
    DB_COL_TELLSEQ_SAMPLE_IDX,
    TABLE_CHANGE_LOG,
    TABLE_ILLUMINA_RUN,
    TABLE_ILLUMINA_SAMPLE,
    TABLE_INPUT_SAMPLE,
    TABLE_PREPPED_SAMPLE,
    TABLE_PROJECT,
    TABLE_TELLSEQ_SAMPLE,
    UPDATE_PLATFORM_ILLUMINA,
    UPDATE_PLATFORM_TELLSEQ,
)
from .db import (
    get_single_run_idx,
    lookup_input_samples_by_name,
    lookup_projects_by_key,
)

# Map platform strings to (table_name, primary_key_column) for the
# platform-specific sample tables targeted by lane updates.
_PLATFORM_TABLES = {
    UPDATE_PLATFORM_ILLUMINA: (TABLE_ILLUMINA_SAMPLE, DB_COL_ILLUMINA_SAMPLE_IDX),
    UPDATE_PLATFORM_TELLSEQ: (TABLE_TELLSEQ_SAMPLE, DB_COL_TELLSEQ_SAMPLE_IDX),
}

IllumRunSetting = Literal["mask_short_reads", "override_cycles"]


def _require_nonempty_or_none(value: str | None, param_name: str) -> str | None:
    """Reject empty-string *value*; allow None (clear) and non-empty strings.

    Returned unchanged on the allowed paths so call sites can chain.
    """
    if value == "":
        raise ValueError(
            f"{param_name} must not be empty; pass None to clear, or supply a non-empty value"
        )
    return value


def _require_nonempty(value: str, param_name: str) -> str:
    """Reject empty-string or None *value*; allow only non-empty strings."""
    if value is None or value == "":
        raise ValueError(f"{param_name} must be a non-empty string")
    return value


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


def _apply_row_update(
    conn: sqlite3.Connection,
    table: str,
    pk_col: str,
    pk_value: int,
    column: str,
    old_value: object,
    new_value: object,
    reason: str | None,
) -> None:
    """Update one row's *column* and log the change; caller owns the commit.

    *table*, *pk_col*, and *column* must come from a closed set of
    constants — they are interpolated into the SQL statement. Issues no
    commit or rollback so a caller can batch several rows into one
    transaction.
    """
    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE {pk_col} = ?",
        (new_value, pk_value),
    )
    _log_change(conn, table, pk_value, column, old_value, new_value, reason)


def _apply_single_row_update(
    conn: sqlite3.Connection,
    table: str,
    pk_col: str,
    pk_value: int,
    column: str,
    old_value: object,
    new_value: object,
    reason: str | None,
) -> None:
    """Apply a single-column update to one row and commit it.

    The update and audit-log insert run inside one transaction: a
    failure in either rolls back both.
    """
    try:
        _apply_row_update(
            conn, table, pk_col, pk_value, column, old_value, new_value, reason
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
        ValueError: If *sample_name* or *accession* is an empty string,
            if no input_sample matches *sample_name*, or if multiple
            distinct input_samples match (ambiguous).
    """
    # Reject empty strings for both the lookup key and the value
    _require_nonempty(sample_name, "sample_name")
    _require_nonempty_or_none(accession, "accession")

    cur = conn.cursor()

    # Resolve effective Sample_Name to a unique input_sample
    matches = lookup_input_samples_by_name(cur, sample_name)
    if not matches:
        raise ValueError(f"No input_sample matches Sample_Name {sample_name!r}")
    if len(matches) > 1:
        raise ValueError(
            f"Sample_Name {sample_name!r} is ambiguous; resolves to "
            f"{len(matches)} distinct input_samples"
        )

    input_sample_idx, old_accession = matches[0]
    _apply_single_row_update(
        conn,
        TABLE_INPUT_SAMPLE,
        DB_COL_INPUT_SAMPLE_IDX,
        input_sample_idx,
        DB_COL_BIOSAMPLE_ACCESSION,
        old_accession,
        accession,
        reason,
    )


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


def _set_illumina_run_column(
    conn: sqlite3.Connection,
    column: str,
    value: str | None,
    reason: str | None,
) -> None:
    """Set *column* on the sole illumina_run row to *value*.

    *column* must be a constant from this module's closed set, never
    user input — it is interpolated into the SQL statement.
    """
    run_idx = get_single_run_idx(conn)
    cur = conn.execute(
        f"SELECT {column} FROM {TABLE_ILLUMINA_RUN} WHERE run_idx = ?",
        (run_idx,),
    )
    (old_value,) = cur.fetchone()
    _apply_single_row_update(
        conn,
        TABLE_ILLUMINA_RUN,
        DB_COL_RUN_IDX,
        run_idx,
        column,
        old_value,
        value,
        reason,
    )


def set_illumina_run_setting(
    conn: sqlite3.Connection,
    setting: IllumRunSetting,
    value: str | None,
    reason: str | None = None,
) -> None:
    """Set a named illumina_run setting column to *value*; None clears it.

    Raises:
        ValueError: If *value* is an empty string, or if *setting* is
            not a supported illumina_run setting column.
    """
    valid = get_args(IllumRunSetting)
    if setting not in valid:
        raise ValueError(
            f"Unsupported illumina_run setting {setting!r}; supported: {sorted(valid)}"
        )
    _require_nonempty_or_none(value, "value")
    _set_illumina_run_column(conn, setting, value, reason)


def set_bioproject_accession(
    conn: sqlite3.Connection,
    accession: str | None,
    *,
    project_name: str | None = None,
    external_project_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Set bioproject_accession on the project matching the given key.

    Exactly one of *project_name* or *external_project_id* must be
    non-None. *accession* may be None to clear; doing so raises
    IntegrityError if it would leave both accession identifiers NULL.

    Raises:
        ValueError: If zero or two key arguments are supplied with
            non-empty values, if *accession* is an empty string, if no
            project matches the supplied key, or if *external_project_id*
            matches multiple projects (the column is not unique).
    """
    # Reject empty-string accession; None remains the clear path
    _require_nonempty_or_none(accession, "accession")

    # Require exactly one non-empty key.  Empty strings count as
    # "not supplied" to avoid a silent no-match or unintended SELECT.
    supplied = {
        DB_COL_PROJECT_NAME: project_name,
        DB_COL_EXTERNAL_PROJECT_ID: external_project_id,
    }
    keys = [k for k, v in supplied.items() if v]
    if len(keys) != 1:
        raise ValueError(
            "Exactly one of project_name or external_project_id must be "
            f"supplied as a non-empty string; got {keys}"
        )
    key_col = keys[0]
    key_value = supplied[key_col]

    # Resolve the chosen key to project_idx and capture the prior value
    cur = conn.cursor()
    matches = lookup_projects_by_key(cur, key_col, key_value)
    if not matches:
        raise ValueError(f"No project matches {key_col} {key_value!r}")
    if len(matches) > 1:
        raise ValueError(
            f"{key_col} {key_value!r} is ambiguous; resolves to {len(matches)} projects"
        )

    project_idx, old_accession = matches[0]
    _apply_single_row_update(
        conn,
        TABLE_PROJECT,
        DB_COL_PROJECT_IDX,
        project_idx,
        DB_COL_BIOPROJECT_ACCESSION,
        old_accession,
        accession,
        reason,
    )


def _lookup_do_not_use_rows(
    cur,
    table: str,
    pk_col: str,
    key_col: str,
    key_value: object,
) -> list[tuple[int, object]]:
    """Return (pk_value, current do_not_use) rows where key_col = key_value.

    *table*, *pk_col*, and *key_col* must come from the closed set of
    table/column constants — they are interpolated into the SQL.
    """
    cur.execute(
        f"SELECT {pk_col}, {DB_COL_DO_NOT_USE} FROM {table} WHERE {key_col} = ?",
        (key_value,),
    )
    return cur.fetchall()


def _set_do_not_use(
    conn: sqlite3.Connection,
    table: str,
    pk_col: str,
    rows: list[tuple[int, object]],
    value: bool | None,
    reason: str | None,
) -> None:
    """Set do_not_use to *value* on each (pk_value, old_value) in *rows*.

    Callers resolve *rows* and supply the target (table, pk_col); this
    body applies and audits the change for each. All rows are updated
    within a single transaction: a failure on any row rolls back the
    whole batch.
    """
    # Store as 0/1/NULL so the column and audit log stay integer-consistent
    new_value = None if value is None else int(value)
    try:
        for pk_value, old_value in rows:
            _apply_row_update(
                conn,
                table,
                pk_col,
                pk_value,
                DB_COL_DO_NOT_USE,
                old_value,
                new_value,
                reason,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def set_input_sample_do_not_use(
    conn: sqlite3.Connection,
    *,
    input_sample_idx: int | None = None,
    biosample_accession: str | None = None,
    value: bool = True,
    reason: str | None = None,
) -> None:
    """Set do_not_use on the input_sample(s) identified by one key.

    Exactly one of *input_sample_idx* or *biosample_accession* must be
    supplied; a *biosample_accession* matching several input_samples sets
    every match. *value* True (the default) excludes the sample and all
    its preps from default fetches regardless of any per-prep override
    (the flag is a hard floor); False clears the flag.

    Raises:
        ValueError: If zero or two keys are supplied, if
            *biosample_accession* is empty, or if no input_sample matches.
    """
    # Require exactly one supplied key, then resolve it to (column, value)
    keys = [
        col
        for col, val in (
            (DB_COL_INPUT_SAMPLE_IDX, input_sample_idx),
            (DB_COL_BIOSAMPLE_ACCESSION, biosample_accession),
        )
        if val is not None
    ]
    if len(keys) != 1:
        raise ValueError(
            "Exactly one of input_sample_idx or biosample_accession must be supplied"
        )
    key_col = keys[0]
    if key_col == DB_COL_BIOSAMPLE_ACCESSION:
        _require_nonempty(biosample_accession, "biosample_accession")
        key_value = biosample_accession
    else:
        key_value = input_sample_idx

    # Resolve the key to one or more input_sample rows and update each
    cur = conn.cursor()
    rows = _lookup_do_not_use_rows(
        cur, TABLE_INPUT_SAMPLE, DB_COL_INPUT_SAMPLE_IDX, key_col, key_value
    )
    if not rows:
        raise ValueError(f"No input_sample matches {key_col} {key_value!r}")
    _set_do_not_use(
        conn, TABLE_INPUT_SAMPLE, DB_COL_INPUT_SAMPLE_IDX, rows, value, reason
    )


def set_prepped_sample_do_not_use(
    conn: sqlite3.Connection,
    prepped_sample_idx: int,
    *,
    value: bool | None = True,
    reason: str | None = None,
) -> None:
    """Set the per-prep do_not_use override on one prepped_sample.

    The override is two-state: *value* True flags this replicate; None
    clears the override so it inherits the input_sample flag. False is
    rejected — an explicit prep-level "not flagged" is indistinguishable
    from inheriting, so use None. The override can only add exclusion: an
    input_sample marked do_not_use stays excluded regardless.

    Raises:
        ValueError: If *value* is False, or if no prepped_sample matches
            *prepped_sample_idx*.
    """
    if value is False:
        raise ValueError(
            "value must be True (flag) or None (inherit); False is not supported"
        )
    cur = conn.cursor()
    rows = _lookup_do_not_use_rows(
        cur,
        TABLE_PREPPED_SAMPLE,
        DB_COL_PREPPED_SAMPLE_IDX,
        DB_COL_PREPPED_SAMPLE_IDX,
        prepped_sample_idx,
    )
    if not rows:
        raise ValueError(
            f"No prepped_sample matches prepped_sample_idx {prepped_sample_idx!r}"
        )
    _set_do_not_use(
        conn, TABLE_PREPPED_SAMPLE, DB_COL_PREPPED_SAMPLE_IDX, rows, value, reason
    )
