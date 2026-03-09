"""Reconstruct an omnibus CSV from the database using the view registry.

The legacy_samplesheet_view table maps each (format, section) to a SQL view
and a section_format.  This module queries those views and writes the
appropriate CSV output for each section format:

  - header_kv:    [SectionName] followed by key,value rows
  - values_only:  [SectionName] followed by one bare value per row
  - tabular:      [SectionName] followed by a header row + data rows
"""

from __future__ import annotations

import csv
import io

from ..constants import (
    CHECK_CONTAINS_KATHAROSEQ,
    CHECK_CONTAINS_REPLICATES,
    COL_SAMPLE_ID,
    COL_SAMPLE_NAME,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
    SECTION_DATA,
)
from ..db import introspect_view
from .formatting import bcl_scrub_name, format_value


# ---------------------------------------------------------------------------
# CSV post-processing
# ---------------------------------------------------------------------------


def _pad_to_max_width(csv_text: str) -> str:
    """Pad every row in the CSV to have the same number of columns.

    Original omnibus files pad all rows to the width of the widest
    section, using trailing commas for empty cells.

    Args:
        csv_text: The raw CSV output string.

    Returns:
        str: The CSV string with every row padded to the maximum column
        count found in the file.
    """
    line_ends = "\n\r"
    lines = csv_text.splitlines(keepends=True)
    max_cols = 0
    for line in lines:
        # Count columns by counting commas in non-empty lines.
        stripped = line.rstrip(line_ends)
        if stripped:
            num_cols = stripped.count(",") + 1
            if num_cols > max_cols:
                max_cols = num_cols

    padded = []
    for line in lines:
        stripped = line.rstrip(line_ends)
        num_cols = stripped.count(",") + 1 if stripped else 1
        padding = "," * (max_cols - num_cols)
        padded.append(stripped + padding + "\n")

    return "".join(padded)


def _query_view(
    cur, view_name: str, col_names: list[str], has_run_id: bool, run_id: int
):
    """Query a view for the given run and return all matching rows.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to query.
        col_names: Column names to select (should exclude run_id).
        has_run_id: Whether the view contains a run_id column.
        run_id: The sequencing_run.run_id to filter on.

    Returns:
        list[tuple]: All matching rows, each as a tuple of values in the
        same order as col_names.
    """
    select_cols = ", ".join(f'"{c}"' for c in col_names)

    if has_run_id:
        cur.execute(
            f'SELECT {select_cols} FROM "{view_name}" WHERE run_id = ?',
            (run_id,),
        )
    else:
        # Fallback: return everything (shouldn't happen in practice).
        cur.execute(f'SELECT {select_cols} FROM "{view_name}"')

    return cur.fetchall()


# ---------------------------------------------------------------------------
# Section writers (pure formatters — no DB access)
# ---------------------------------------------------------------------------


def _write_header_kv(writer, section_name, col_names, row):
    """Write a key-value section like [Header] or [Settings].

    Each column in the view becomes one key,value row in the CSV output.

    Args:
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        col_names: Column names (excluding run_id).
        row: A single data tuple matching col_names, or None.
    """
    if not row:
        return

    # Map column names to values from the single returned row.
    values = dict(zip(col_names, row))

    writer.writerow([f"[{section_name}]"])
    for col in col_names:
        raw = values.get(col)
        # Skip keys whose DB value is NULL (e.g. settings not present
        # for this sequencer). Empty strings are kept.
        if raw is None:
            continue
        writer.writerow([col, format_value(raw, col)])
    writer.writerow([])


def _write_values_only(writer, section_name, row):
    """Write a values-only section like [Reads].

    Each value in the single row becomes one bare-value row in the CSV.

    Args:
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        row: A single data tuple, or None.
    """
    writer.writerow([f"[{section_name}]"])
    if row:
        # Emit each column value as its own CSV row (e.g. "151\n151\n").
        for val in row:
            writer.writerow([format_value(val, "")])
    writer.writerow([])


def _write_tabular(writer, section_name, col_names, rows):
    """Write a tabular section like [Data] or [Bioinformatics].

    Emits a header row followed by one data row per result.
    Sample_ID columns are derived via bcl_scrub_name(Sample_Name).

    Args:
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        col_names: Column names to emit as the header row.
        rows: List of data tuples matching col_names.
    """
    writer.writerow([f"[{section_name}]"])
    writer.writerow(col_names)

    # Pre-compute column indices for Sample_ID / Sample_Name so we can
    # derive Sample_ID from Sample_Name via bcl_scrub_name.
    sample_id_idx = (
        col_names.index(COL_SAMPLE_ID) if COL_SAMPLE_ID in col_names else None
    )
    sample_name_idx = (
        col_names.index(COL_SAMPLE_NAME) if COL_SAMPLE_NAME in col_names else None
    )

    for row in rows:
        formatted = []
        for i, (val, col) in enumerate(zip(row, col_names)):
            if i == sample_id_idx and sample_name_idx is not None:
                # Sample_ID is always the scrubbed version of Sample_Name.
                formatted.append(bcl_scrub_name(str(row[sample_name_idx])))
            else:
                formatted.append(format_value(val, col))
        writer.writerow(formatted)
    writer.writerow([])


# ---------------------------------------------------------------------------
# Optional column detection
# ---------------------------------------------------------------------------

# Map check_function names → callables that inspect the DB for a given run.
# Each returns True if the optional column group should be included.
_CHECK_FUNCTIONS = {
    CHECK_CONTAINS_REPLICATES: lambda cur, run_id: _db_has_rows(
        cur,
        "SELECT 1 FROM replicated_samples WHERE run_id = ? LIMIT 1",
        (run_id,),
    ),
    CHECK_CONTAINS_KATHAROSEQ: lambda cur, run_id: _db_has_rows(
        cur,
        "SELECT 1 FROM katharoseq_sample LIMIT 1",
        (),
    ),
}


def _db_has_rows(cur, sql: str, params: tuple) -> bool:
    """Return True if the given SQL query produces at least one row.

    Args:
        cur: An open SQLite cursor.
        sql: The SQL query to execute.
        params: Bind parameters for the query.

    Returns:
        bool: True if the query returned at least one row, False otherwise.
    """
    cur.execute(sql, params)
    return cur.fetchone() is not None


def _get_active_columns(
    cur,
    legacy_format_id: int,
    section_name: str,
    all_cols: list[str],
    run_id: int,
) -> list[str]:
    """Return the subset of all_cols that should appear in the output.

    Starts with all columns, then removes any optional-group columns whose
    check function returns False for this run.

    Args:
        cur: An open SQLite cursor.
        legacy_format_id: The legacy format to look up optional column
            groups for.
        section_name: The section whose optional groups to evaluate.
        all_cols: The full list of column names defined by the view.
        run_id: The sequencing_run.run_id used to evaluate check functions.

    Returns:
        list[str]: The filtered column list with inactive optional columns
        removed.
    """
    cur.execute(
        "SELECT group_name, column_names, check_function "
        "FROM legacy_samplesheet_optional_columns "
        "WHERE legacy_format_id = ? AND section_name = ?",
        (legacy_format_id, section_name),
    )
    optional_groups = cur.fetchall()
    if not optional_groups:
        # No optional columns defined — use everything.
        return all_cols

    # Collect column names to exclude.
    exclude: set[str] = set()
    for _group_name, col_names_csv, check_fn_name in optional_groups:
        checker = _CHECK_FUNCTIONS.get(check_fn_name)
        if checker is None or not checker(cur, run_id):
            # Check function missing or returned False — exclude these columns.
            exclude.update(c.strip() for c in col_names_csv.split(","))

    return [c for c in all_cols if c not in exclude]


# ---------------------------------------------------------------------------
# Extra column merging
# ---------------------------------------------------------------------------


def _merge_extra_columns(
    cur, run_id: int, active_cols: list[str], rows: list[tuple]
) -> tuple[list[str], list[tuple]]:
    """Append extra columns from legacy_extra_column to the Data section.

    Queries legacy_extra_column for all samples in the run, determines the
    distinct extra column names, appends them alphabetically after the
    known columns, and merges the extra values into each row.

    Args:
        cur: An open SQLite cursor.
        run_id: The sequencing_run.run_id to query extra columns for.
        active_cols: The current list of active column names.
        rows: The current list of data tuples matching active_cols.

    Returns:
        tuple[list[str], list[tuple]]: Updated (columns, rows) with extra
        columns appended.
    """
    # Find distinct extra column names for this run
    cur.execute(
        "SELECT DISTINCT lec.column_name "
        "FROM legacy_extra_column lec "
        "JOIN compression_sample cs "
        "ON lec.compression_sample_id = cs.compression_sample_id "
        "JOIN compression_placement cp ON cs.placement_id = cp.placement_id "
        "WHERE cp.run_id = ? "
        "ORDER BY lec.column_name",
        (run_id,),
    )
    extra_col_names = [r[0] for r in cur.fetchall()]
    if not extra_col_names:
        return active_cols, rows

    # Build a lookup: (compression_sample_id, column_name) → value
    cur.execute(
        "SELECT lec.compression_sample_id, lec.column_name, lec.column_value "
        "FROM legacy_extra_column lec "
        "JOIN compression_sample cs "
        "ON lec.compression_sample_id = cs.compression_sample_id "
        "JOIN compression_placement cp ON cs.placement_id = cp.placement_id "
        "WHERE cp.run_id = ?",
        (run_id,),
    )
    extra_values: dict[tuple[int, str], str | None] = {}
    for cs_id, col_name, col_value in cur.fetchall():
        extra_values[(cs_id, col_name)] = col_value

    # Sample_ID is the compression_sample_id; find its index in active_cols
    sample_id_idx = active_cols.index(COL_SAMPLE_ID)

    # Append extra column values to each row
    merged_rows = []
    for row in rows:
        cs_id = row[sample_id_idx]
        extra_vals = tuple(
            extra_values.get((cs_id, col), "") for col in extra_col_names
        )
        merged_rows.append(row + extra_vals)

    return active_cols + extra_col_names, merged_rows


# ---------------------------------------------------------------------------
# Top-level reconstruction
# ---------------------------------------------------------------------------


def reconstruct_omnibus(conn, run_id: int) -> str:
    """Rebuild the full omnibus CSV for a sequencing run.

    Steps:
      1. Look up which legacy format this run uses.
      2. Query the view registry for the ordered list of sections.
      3. For tabular sections, determine which optional columns are active.
      4. Delegate each section to the appropriate writer based on its
         section_format.

    Args:
        conn: An open SQLite connection with a fully populated database.
        run_id: The sequencing_run.run_id to reconstruct the CSV for.

    Returns:
        str: The complete reconstructed omnibus CSV as a string.

    Raises:
        ValueError: If the run has no legacy format assigned.
    """
    cur = conn.cursor()

    # Resolve the legacy format for this run.
    cur.execute(
        """SELECT lf.legacy_format_id, lf.legacy_sheet_type, lf.legacy_version
           FROM sequencing_run sr
           JOIN legacy_samplesheet_format lf
             ON sr.legacy_format_id = lf.legacy_format_id
           WHERE sr.run_id = ?""",
        (run_id,),
    )
    fmt = cur.fetchone()
    if not fmt:
        raise ValueError(f"Run {run_id} has no legacy format assigned")
    legacy_format_id = fmt[0]

    # Fetch the ordered list of sections for this format.
    cur.execute(
        """SELECT section_name, view_name, section_format
           FROM legacy_samplesheet_view
           WHERE legacy_format_id = ?
           ORDER BY section_order""",
        (legacy_format_id,),
    )
    section_views = cur.fetchall()

    # Write each section to the output buffer.
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for section_name, view_name, section_format in section_views:
        # Introspect the view once to get columns and run_id presence
        all_cols, has_run_id = introspect_view(cur, view_name)

        if section_format == FORMAT_TABULAR:
            # Filter out inactive optional columns before querying
            active_cols = _get_active_columns(
                cur, legacy_format_id, section_name, all_cols, run_id
            )
            rows = _query_view(cur, view_name, active_cols, has_run_id, run_id)

            # Merge extra columns for the Data section
            if section_name == SECTION_DATA:
                active_cols, rows = _merge_extra_columns(cur, run_id, active_cols, rows)

            _write_tabular(writer, section_name, active_cols, rows)
        else:
            # Single-row sections (header_kv, values_only)
            rows = _query_view(cur, view_name, all_cols, has_run_id, run_id)
            row = rows[0] if rows else None

            if section_format == FORMAT_VALUES_ONLY:
                _write_values_only(writer, section_name, row)
            elif section_format == FORMAT_HEADER_KV:
                _write_header_kv(writer, section_name, all_cols, row)
            else:
                raise ValueError(f"Unknown section_format {section_format!r}")

    return _pad_to_max_width(output.getvalue())
