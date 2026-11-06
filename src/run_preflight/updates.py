"""Post-fill update operations for filled SQLite run preflights.

Each operation writes one row to ``change_log`` per modified domain
row, capturing the prior and new values plus an optional caller-
supplied reason. Free-text string arguments (values, lookup keys, reason)
are stripped of surrounding whitespace and invisible format characters; one
left empty raises ValueError, and a non-str one raises TypeError.
"""

from __future__ import annotations

import sqlite3
import warnings
from typing import Literal, get_args

from .constants import (
    DB_COL_BIOPROJECT_ACCESSION,
    DB_COL_BIOSAMPLE_ACCESSION,
    DB_COL_DO_NOT_USE,
    DB_COL_EXTERNAL_PROJECT_ID,
    DB_COL_INPUT_SAMPLE_IDX,
    DB_COL_LANE,
    DB_COL_MOVIE_CONTEXT_ID,
    DB_COL_PACBIO_SAMPLE_IDX,
    DB_COL_PREPPED_SAMPLE_IDX,
    DB_COL_PROJECT_IDX,
    DB_COL_PROJECT_NAME,
    DB_COL_RUN_IDX,
    DB_COL_SAMPLE_NAME,
    DB_COL_SMRT_CELL_WELL_SAMPLE_ID,
    InputSampleIdentifierKind,
    InputSampleLookupKey,
    PacbioSampleLookupKey,
    PlatformSpecificSampleKind,
    ProjectIdentifierKind,
    ProjectLookupKey,
    TABLE_CHANGE_LOG,
    TABLE_ILLUMINA_RUN,
    TABLE_INPUT_SAMPLE,
    TABLE_PACBIO_SAMPLE,
    TABLE_PREPPED_SAMPLE,
    TABLE_PROJECT,
)
from .db import (
    get_single_run_idx,
    lookup_input_samples_by_name,
    lookup_project_by_key,
    sample_kind_names,
)
from .validation import require_literal_member, strip_str

# The platform-specific sample kinds sequenced on the Illumina platform;
# these are the lane-bearing tables that lane updates may target. Their
# table and primary-key column names are derived from the kind token.
_ILLUMINA_PLATFORM_SAMPLE_KINDS: frozenset[PlatformSpecificSampleKind] = frozenset(
    {"illumina", "tellseq"}
)

IllumRunSetting = Literal["mask_short_reads", "override_cycles"]


class Unchanged:
    """Sentinel type for a set-field argument the caller did not supply.

    Distinct from None, which explicitly clears a field: passing the
    UNCHANGED singleton (or omitting the argument) leaves a field
    untouched.
    """

    def __repr__(self) -> str:
        return "<unchanged>"


UNCHANGED = Unchanged()


def _require_nonempty_or_none(value: str | None, param_name: str) -> str | None:
    """Return *value* without surrounding whitespace or format characters.

    None is returned unchanged.

    Raises TypeError for a non-str *value*, and ValueError if nothing is
    left after stripping.
    """
    if value is None:
        return None
    stripped = strip_str(value, param_name)
    if not stripped:
        raise ValueError(f"{param_name} must not be empty or whitespace-only")
    return stripped


def _require_nonempty(value: str | None, param_name: str) -> str:
    """Return *value* without surrounding whitespace or format characters.

    Raises TypeError for a non-str *value*, and ValueError if *value* is
    None or nothing is left after stripping.
    """
    # None is checked as an empty string so the rejection has one message
    stripped = _require_nonempty_or_none("" if value is None else value, param_name)
    return stripped


def _require_exactly_one_key(
    pairs: tuple[tuple[str, object], ...],
    key_literal: object,
    label: str,
) -> tuple[str, object]:
    """Return the sole (column, value) in *pairs* whose value is not None.

    *key_literal* is the Literal of lookup-key columns the caller accepts,
    and *label* names it in error messages. Raises ValueError unless
    exactly one value is non-None and its column is a *key_literal* member.
    """
    # Require exactly one supplied key, named in the Literal's order
    supplied = [(col, val) for col, val in pairs if val is not None]
    if len(supplied) != 1:
        description = " or ".join(get_args(key_literal))
        raise ValueError(f"Exactly one of {description} must be supplied")
    key_col, key_value = supplied[0]

    # Reject a key column outside the Literal
    require_literal_member(key_col, key_literal, label)
    return key_col, key_value


def _require_unique_match(
    matches: list[tuple],
    no_match_msg: str,
    ambiguous_msg: str,
) -> tuple:
    """Return the sole element of *matches*.

    Raises ValueError with *no_match_msg* when empty, or *ambiguous_msg*
    when *matches* holds more than one row.
    """
    if not matches:
        raise ValueError(no_match_msg)
    if len(matches) > 1:
        raise ValueError(ambiguous_msg)
    return matches[0]


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

    *table*, *pk_col*, and *column* are interpolated into the SQL statement,
    so each must already be checked against a closed set. Issues no
    commit or rollback so a caller can batch several rows into one
    transaction.
    """
    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE {pk_col} = ?",
        (new_value, pk_value),
    )
    _log_change(conn, table, pk_value, column, old_value, new_value, reason)


def _apply_row_updates(
    conn: sqlite3.Connection,
    specs: list[tuple],
) -> None:
    """Apply several row updates and their audit logs in one transaction.

    Each spec is the argument tuple passed to _apply_row_update after
    *conn*: (table, pk_col, pk_value, column, old_value, new_value,
    reason). A failure on any spec rolls back the whole batch.
    """
    try:
        for table, pk_col, pk_value, column, old_value, new_value, reason in specs:
            _apply_row_update(
                conn, table, pk_col, pk_value, column, old_value, new_value, reason
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
    """Apply a single-column update to one row and commit it."""
    _apply_row_updates(
        conn, [(table, pk_col, pk_value, column, old_value, new_value, reason)]
    )


def set_input_sample_identifier(
    conn: sqlite3.Connection,
    identifier_kind: InputSampleIdentifierKind,
    value: str | None,
    *,
    sample_name: str,
    reason: str | None = None,
) -> None:
    """Set one identifier column on the input_sample matching sample_name.

    *sample_name* may be an input-sample name or a per-replicate alias; all
    aliases of one biological sample resolve to the same input_sample and
    so share its identifiers. *value* may be None to clear.

    Raises:
        ValueError: If *identifier_kind* is not an input_sample identifier,
            if *sample_name* or *value* is empty or whitespace-only, if no
            input_sample matches *sample_name*, or if multiple distinct
            input_samples match (ambiguous).
        TypeError: If *sample_name*, *value*, or *reason* is not a str.
        sqlite3.IntegrityError: If clearing *value* would leave the
            input_sample with no identifier.
    """
    # Strip the lookup key, value, and reason, rejecting any that is blank
    sample_name = _require_nonempty(sample_name, "sample_name")
    reason = _require_nonempty_or_none(reason, "reason")
    value = _require_nonempty_or_none(value, "value")

    # Resolve effective Sample_Name to a unique input_sample
    cur = conn.cursor()
    matches = lookup_input_samples_by_name(cur, sample_name, identifier_kind)
    input_sample_idx, old_value = _require_unique_match(
        matches,
        f"No input_sample matches Sample_Name {sample_name!r}",
        f"Sample_Name {sample_name!r} is ambiguous; resolves to "
        f"{len(matches)} distinct input_samples",
    )
    _apply_single_row_update(
        conn,
        TABLE_INPUT_SAMPLE,
        DB_COL_INPUT_SAMPLE_IDX,
        input_sample_idx,
        identifier_kind,
        old_value,
        value,
        reason,
    )


def set_project_identifier(
    conn: sqlite3.Connection,
    identifier_kind: ProjectIdentifierKind,
    value: str | None,
    *,
    project_name: str | None = None,
    external_project_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Set one identifier column on the project matching the given key.

    Exactly one of *project_name* or *external_project_id* must be a
    non-empty string. *value* may be None to clear.

    Raises:
        ValueError: If *identifier_kind* is not a project identifier, if
            zero or two keys are supplied, if the supplied key or *value*
            is empty or whitespace-only, or if no project matches the
            supplied key.
        TypeError: If the supplied key, *value*, or *reason* is not a str.
        sqlite3.IntegrityError: If another project already holds *value*,
            or if clearing *value* would leave the project with no
            identifier.
    """
    # Strip the value and reason, rejecting either if blank; None clears
    value = _require_nonempty_or_none(value, "value")
    reason = _require_nonempty_or_none(reason, "reason")

    # Require exactly one supplied key, then reject it if empty, so an empty
    # key is never silently ignored or matched
    key_col, key_value = _require_exactly_one_key(
        (
            (DB_COL_PROJECT_NAME, project_name),
            (DB_COL_EXTERNAL_PROJECT_ID, external_project_id),
        ),
        ProjectLookupKey,
        "project lookup key",
    )
    key_value = _require_nonempty(key_value, key_col)

    # Resolve the chosen key to project_idx and capture the prior value
    cur = conn.cursor()
    match = lookup_project_by_key(cur, key_col, key_value, identifier_kind)
    if match is None:
        raise ValueError(f"No project matches {key_col} {key_value!r}")
    project_idx, old_value = match
    _apply_single_row_update(
        conn,
        TABLE_PROJECT,
        DB_COL_PROJECT_IDX,
        project_idx,
        identifier_kind,
        old_value,
        value,
        reason,
    )


def set_biosample_accession(
    conn: sqlite3.Connection,
    sample_name: str,
    accession: str | None,
    reason: str | None = None,
) -> None:
    """Deprecated alias that sets biosample_accession; see set_input_sample_identifier."""
    warnings.warn(
        "set_biosample_accession is deprecated; use set_input_sample_identifier instead.",  # noqa: E501
        DeprecationWarning,
        stacklevel=2,
    )
    set_input_sample_identifier(
        conn,
        DB_COL_BIOSAMPLE_ACCESSION,
        accession,
        sample_name=sample_name,
        reason=reason,
    )


def _require_lane_or_none(lane: object, param_name: str) -> None:
    """Raise ValueError unless *lane* is a positive int (not a bool) or None."""
    is_lane = isinstance(lane, int) and not isinstance(lane, bool) and lane >= 1
    if lane is not None and not is_lane:
        raise ValueError(f"{param_name} must be a positive int or None, got {lane!r}")


def update_lane(
    conn: sqlite3.Connection,
    sample_kind: PlatformSpecificSampleKind,
    from_lane: int | None,
    to_lane: int | None,
    reason: str | None = None,
) -> int:
    """Bulk-reassign lane values on a platform-specific sample table.

    Every row whose current lane equals *from_lane* (NULL is a value)
    is updated to *to_lane*. *sample_kind* must be an Illumina-platform
    sample kind. Returns the number of rows updated.

    Raises:
        ValueError: For a lane that is neither a positive int nor None, a
            blank *reason*, a sample kind without lanes, a post-update state
            that mixes NULL and non-NULL lane values, or a collision
            with the unique ``(prepped_sample_idx, lane)`` index.
        TypeError: If *reason* is not a str.
    """
    if sample_kind not in _ILLUMINA_PLATFORM_SAMPLE_KINDS:
        supported = sorted(_ILLUMINA_PLATFORM_SAMPLE_KINDS)
        raise ValueError(
            f"Unsupported sample kind {sample_kind!r}; lane updates are only "
            f"defined for {supported}"
        )
    _require_lane_or_none(from_lane, "from_lane")
    _require_lane_or_none(to_lane, "to_lane")
    reason = _require_nonempty_or_none(reason, "reason")
    names = sample_kind_names(sample_kind)
    table, pk_col = names.table, names.idx_col
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
    """Set *column*, an IllumRunSetting member, on the sole illumina_run row.

    Raises ValueError if *column* is not an IllumRunSetting member, if the
    database does not hold exactly one processing run, or if that run has
    no illumina_run row.
    """
    require_literal_member(column, IllumRunSetting, "illumina_run setting")
    run_idx = get_single_run_idx(conn)
    cur = conn.execute(
        f"SELECT {column} FROM {TABLE_ILLUMINA_RUN} WHERE run_idx = ?",
        (run_idx,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(
            f"Run {run_idx} has no illumina_run row, so it has no Illumina settings"
        )
    (old_value,) = row
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
        ValueError: If *value* is empty or whitespace-only, if *setting* is
            not a supported illumina_run setting column, if the database does
            not hold exactly one processing run, or if that run has no
            illumina_run row.
        TypeError: If *value* or *reason* is not a str.
    """
    value = _require_nonempty_or_none(value, "value")
    reason = _require_nonempty_or_none(reason, "reason")
    _set_illumina_run_column(conn, setting, value, reason)


def set_bioproject_accession(
    conn: sqlite3.Connection,
    accession: str | None,
    *,
    project_name: str | None = None,
    external_project_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Deprecated alias that sets bioproject_accession; see set_project_identifier."""
    warnings.warn(
        "set_bioproject_accession is deprecated; use set_project_identifier instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    set_project_identifier(
        conn,
        DB_COL_BIOPROJECT_ACCESSION,
        accession,
        project_name=project_name,
        external_project_id=external_project_id,
        reason=reason,
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
    specs = [
        (table, pk_col, pk_value, DB_COL_DO_NOT_USE, old_value, new_value, reason)
        for pk_value, old_value in rows
    ]
    _apply_row_updates(conn, specs)


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
        ValueError: If *value* is neither True nor False, if zero or two
            keys are supplied, if *biosample_accession* or *reason* is
            empty or whitespace-only, or if no input_sample matches.
        TypeError: If *biosample_accession* or *reason* is not a str.
    """
    if value is not True and value is not False:
        raise ValueError(
            f"value must be True (flag) or False (clear); {value!r} is not supported"
        )

    # Require exactly one supplied key, then resolve it to (column, value)
    key_col, key_value = _require_exactly_one_key(
        (
            (DB_COL_INPUT_SAMPLE_IDX, input_sample_idx),
            (DB_COL_BIOSAMPLE_ACCESSION, biosample_accession),
        ),
        InputSampleLookupKey,
        "input_sample lookup key",
    )
    if key_col == DB_COL_BIOSAMPLE_ACCESSION:
        key_value = _require_nonempty(key_value, key_col)
    reason = _require_nonempty_or_none(reason, "reason")

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
        ValueError: If *value* is neither True nor None, if *reason* is
            blank, or if no prepped_sample matches *prepped_sample_idx*.
        TypeError: If *reason* is not a str.
    """
    if value is not True and value is not None:
        raise ValueError(
            f"value must be True (flag) or None (inherit); {value!r} is not supported"
        )
    reason = _require_nonempty_or_none(reason, "reason")
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


def _resolve_pacbio_sample(
    cur,
    key_col: str,
    key_value: object,
    columns: list[str],
) -> tuple[int, dict[str, object]]:
    """Return (pacbio_sample_idx, {column: current_value}) for the sole match.

    *key_col* is DB_COL_PACBIO_SAMPLE_IDX (matched directly) or
    DB_COL_SAMPLE_NAME (resolved via the effective Sample_Name exposed by
    prepped_sample_name). *columns* are pacbio_sample columns whose
    current values the caller needs for audit logging; they and the key
    column come from the closed set of column constants — they are
    interpolated into the SQL.

    Raises:
        ValueError: If the key matches zero, or more than one, pacbio_sample.
    """
    select_cols = ", ".join(
        [f"ps.{DB_COL_PACBIO_SAMPLE_IDX}"] + [f"ps.{c}" for c in columns]
    )
    if key_col == DB_COL_PACBIO_SAMPLE_IDX:
        cur.execute(
            f"SELECT {select_cols} FROM {TABLE_PACBIO_SAMPLE} ps "
            f"WHERE ps.{DB_COL_PACBIO_SAMPLE_IDX} = ?",
            (key_value,),
        )
    else:
        cur.execute(
            f"SELECT {select_cols} FROM {TABLE_PACBIO_SAMPLE} ps "
            "JOIN prepped_sample_name psn "
            f"ON ps.{DB_COL_PREPPED_SAMPLE_IDX} = psn.{DB_COL_PREPPED_SAMPLE_IDX} "
            f"WHERE psn.{DB_COL_SAMPLE_NAME} = ?",
            (key_value,),
        )
    rows = cur.fetchall()
    match = _require_unique_match(
        rows,
        f"No pacbio_sample matches {key_col} {key_value!r}",
        f"{key_col} {key_value!r} is ambiguous; resolves to {len(rows)} "
        "pacbio_samples — use pacbio_sample_idx",
    )
    ps_idx = match[0]
    old_values = {col: match[i + 1] for i, col in enumerate(columns)}
    return ps_idx, old_values


def set_pacbio_sample_run_details(
    conn: sqlite3.Connection,
    *,
    sample_name: str | None = None,
    pacbio_sample_idx: int | None = None,
    smrt_cell_well_sample_id: str | None | Unchanged = UNCHANGED,
    movie_context_id: str | None | Unchanged = UNCHANGED,
    reason: str | None = None,
) -> None:
    """Set post-creation PacBio run details on one pacbio_sample.

    Identify the target by exactly one of *sample_name* or
    *pacbio_sample_idx*; a *sample_name* must resolve to exactly one
    pacbio_sample (a name matching several raises, directing the caller
    to pacbio_sample_idx). Each field left as UNCHANGED is untouched; a
    supplied value is written and a supplied None clears it. At least one
    field must be supplied. smrt_cell_well_sample_id values are validated
    by the database CHECK: an invalid value raises sqlite3.IntegrityError.

    Raises:
        ValueError: If not exactly one identifier is supplied, if no field
            is supplied, if *sample_name* or a supplied value is empty or
            whitespace-only, or if the identifier matches zero or multiple
            pacbio_samples.
        TypeError: If *sample_name*, a supplied value, or *reason* is not a
            str.
    """
    # Require exactly one identifier key.
    key_col, key_value = _require_exactly_one_key(
        (
            (DB_COL_PACBIO_SAMPLE_IDX, pacbio_sample_idx),
            (DB_COL_SAMPLE_NAME, sample_name),
        ),
        PacbioSampleLookupKey,
        "pacbio_sample lookup key",
    )
    if key_col == DB_COL_SAMPLE_NAME:
        key_value = _require_nonempty(key_value, key_col)
    reason = _require_nonempty_or_none(reason, "reason")

    # Collect the supplied fields (UNCHANGED means leave alone); a supplied
    # None clears the field, a blank string is rejected.
    updates: list[tuple[str, str | None]] = []
    for col, value in (
        (DB_COL_SMRT_CELL_WELL_SAMPLE_ID, smrt_cell_well_sample_id),
        (DB_COL_MOVIE_CONTEXT_ID, movie_context_id),
    ):
        if not isinstance(value, Unchanged):
            stripped_value = _require_nonempty_or_none(value, col)
            updates.append((col, stripped_value))
    if not updates:
        raise ValueError(
            "At least one of smrt_cell_well_sample_id or movie_context_id "
            "must be supplied"
        )

    # Resolve to exactly one pacbio_sample and its current per-column values.
    cur = conn.cursor()
    ps_idx, old_values = _resolve_pacbio_sample(
        cur, key_col, key_value, [col for col, _ in updates]
    )

    # Apply every supplied field on the row in a single transaction.
    specs = [
        (
            TABLE_PACBIO_SAMPLE,
            DB_COL_PACBIO_SAMPLE_IDX,
            ps_idx,
            col,
            old_values[col],
            new_value,
            reason,
        )
        for col, new_value in updates
    ]
    _apply_row_updates(conn, specs)
