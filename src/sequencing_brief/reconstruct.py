"""Reconstruct an omnibus CSV from the database using the view registry.

The legacy_samplesheet_view table maps each (format, section) to a SQL view
and a section_format.  This module queries those views and writes the
appropriate CSV output for each section format:

  - header_kv:    [SectionName] followed by key,value rows
  - values_only:  [SectionName] followed by one bare value per row
  - tabular:      [SectionName] followed by a header row + data rows
"""

import csv
import io

from .constants import (
    CHECK_CONTAINS_KATHAROSEQ,
    CHECK_CONTAINS_REPLICATES,
    COL_RUN_ID,
    COL_SAMPLE_ID,
    COL_SAMPLE_NAME,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
)
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
    lines = csv_text.splitlines(keepends=True)
    max_cols = 0
    for line in lines:
        # Count columns by counting commas in non-empty lines.
        stripped = line.rstrip("\n\r")
        if stripped:
            num_cols = stripped.count(",") + 1
            if num_cols > max_cols:
                max_cols = num_cols

    padded = []
    for line in lines:
        stripped = line.rstrip("\n\r")
        num_cols = stripped.count(",") + 1 if stripped else 1
        padding = "," * (max_cols - num_cols)
        padded.append(stripped + padding + "\n")

    return "".join(padded)


# ---------------------------------------------------------------------------
# View introspection helpers
# ---------------------------------------------------------------------------

def _get_view_columns(cur, view_name: str) -> list[str]:
    """Return the column names of a SQL view, excluding run_id.

    run_id is a filter column used internally and is never written to
    the output CSV.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        list[str]: Ordered list of column names from the view, with
        run_id omitted.
    """
    cur.execute(f"PRAGMA table_info({view_name})")
    return [row[1] for row in cur.fetchall() if row[1] != COL_RUN_ID]


def _view_has_run_id(cur, view_name: str) -> bool:
    """Check whether a SQL view contains a run_id column.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        bool: True if the view has a column named run_id.
    """
    cur.execute(f"PRAGMA table_info({view_name})")
    return any(row[1] == COL_RUN_ID for row in cur.fetchall())


def _query_view(cur, view_name: str, col_names: list[str], run_id: int):
    """Query a view for the given run and return all matching rows.

    Most views have a run_id column and are filtered directly. Shared
    views (contact, sample_context) lack run_id, so they are filtered
    via a sub-select on the relevant project or sample names for the run.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to query.
        col_names: Column names to select (should exclude run_id).
        run_id: The sequencing_run.run_id to filter on.

    Returns:
        list[tuple]: All matching rows, each as a tuple of values in the
        same order as col_names.
    """
    select_cols = ", ".join(f'"{c}"' for c in col_names)

    if _view_has_run_id(cur, view_name):
        # Direct filter on run_id.
        cur.execute(
            f'SELECT {select_cols} FROM "{view_name}" WHERE run_id = ?',
            (run_id,),
        )

    elif "contact" in view_name:
        # Contact view has no run_id — filter by projects that appear in
        # the run's compression samples, ordered by project insertion order.
        cur.execute(
            f"""SELECT {select_cols} FROM "{view_name}"
                WHERE Sample_Project IN (
                    SELECT DISTINCT p.project_name
                    FROM compression_sample cs
                    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
                    JOIN project p ON ins.project_id = p.project_id
                    WHERE cs.run_id = ?)
                ORDER BY (SELECT p2.project_id FROM project p2
                          WHERE p2.project_name = "{view_name}".Sample_Project)""",
            (run_id,),
        )

    elif "sample_context" in view_name:
        # SampleContext view — filter by control samples in this run.
        cur.execute(
            f"""SELECT {select_cols} FROM "{view_name}"
                WHERE sample_name IN (
                    SELECT COALESCE(cs.sample_name, ins.sample_name)
                    FROM compression_sample cs
                    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
                    WHERE cs.run_id = ? AND ins.project_id IS NULL)""",
            (run_id,),
        )

    else:
        # Fallback: return everything (shouldn't happen in practice).
        cur.execute(f'SELECT {select_cols} FROM "{view_name}"')

    return cur.fetchall()


# ---------------------------------------------------------------------------
# Section writers (one per section_format)
# ---------------------------------------------------------------------------

def _write_header_kv(cur, writer, section_name, view_name, run_id):
    """Write a key-value section like [Header] or [Settings].

    Each column in the view becomes one key,value row in the CSV output.

    Args:
        cur: An open SQLite cursor.
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        view_name: Name of the SQL view to read data from.
        run_id: The sequencing_run.run_id to filter on.
    """
    col_names = _get_view_columns(cur, view_name)

    # Fetch the single row for this run.
    if _view_has_run_id(cur, view_name):
        cur.execute(f"SELECT * FROM {view_name} WHERE run_id = ?", (run_id,))
    else:
        cur.execute(f"SELECT * FROM {view_name}")
    row = cur.fetchone()
    if not row:
        return

    # Map all columns (including run_id) to values, then skip run_id.
    cur.execute(f"PRAGMA table_info({view_name})")
    all_cols = [r[1] for r in cur.fetchall()]
    values = {col: val for col, val in zip(all_cols, row) if col != COL_RUN_ID}

    writer.writerow([f"[{section_name}]"])
    for col in col_names:
        writer.writerow([col, format_value(values.get(col, ""), col)])
    writer.writerow([])


def _write_values_only(cur, writer, section_name, view_name, col_names, run_id):
    """Write a values-only section like [Reads].

    Each column in the single view row becomes one bare-value row in the
    CSV output.

    Args:
        cur: An open SQLite cursor.
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        view_name: Name of the SQL view to read data from.
        col_names: Column names to select from the view.
        run_id: The sequencing_run.run_id to filter on.
    """
    rows = _query_view(cur, view_name, col_names, run_id)

    writer.writerow([f"[{section_name}]"])
    if rows:
        # The view returns one row with N columns; emit each column as
        # its own CSV row (e.g. "151\n151\n").
        for val in rows[0]:
            writer.writerow([format_value(val, "")])
    writer.writerow([])


def _write_tabular(cur, writer, section_name, view_name, col_names, run_id):
    """Write a tabular section like [Data] or [Bioinformatics].

    Emits a header row followed by one data row per view result.
    Sample_ID columns are derived via bcl_scrub_name(Sample_Name).

    Args:
        cur: An open SQLite cursor.
        writer: A csv.writer instance to write rows to.
        section_name: The section label (written as [SectionName]).
        view_name: Name of the SQL view to read data from.
        col_names: Column names to select and emit as the header row.
        run_id: The sequencing_run.run_id to filter on.
    """
    rows = _query_view(cur, view_name, col_names, run_id)

    writer.writerow([f"[{section_name}]"])
    writer.writerow(col_names)

    # Pre-compute column indices for Sample_ID / Sample_Name so we can
    # derive Sample_ID from Sample_Name via bcl_scrub_name.
    sample_id_idx = col_names.index(COL_SAMPLE_ID) if COL_SAMPLE_ID in col_names else None
    sample_name_idx = col_names.index(COL_SAMPLE_NAME) if COL_SAMPLE_NAME in col_names else None

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
    cur, legacy_format_id: int, section_name: str, all_cols: list[str], run_id: int,
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
# Top-level reconstruction
# ---------------------------------------------------------------------------

# Dispatch table: section_format string → writer function.
_WRITERS = {
    FORMAT_HEADER_KV: _write_header_kv,
    FORMAT_VALUES_ONLY: _write_values_only,
    FORMAT_TABULAR: _write_tabular,
}


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
        # Get all columns the view defines.
        all_cols = _get_view_columns(cur, view_name)

        if section_format == FORMAT_TABULAR:
            # Filter out inactive optional columns before writing.
            active_cols = _get_active_columns(
                cur, legacy_format_id, section_name, all_cols, run_id
            )
            _write_tabular(cur, writer, section_name, view_name, active_cols, run_id)

        elif section_format == FORMAT_VALUES_ONLY:
            _write_values_only(cur, writer, section_name, view_name, all_cols, run_id)

        else:
            # header_kv — col_names read inside the writer.
            _write_header_kv(cur, writer, section_name, view_name, run_id)

    return _pad_to_max_width(output.getvalue())
