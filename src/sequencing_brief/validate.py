"""Validate a parsed omnibus file against the view registry.

Checks that the file contains the expected sections, and that each section's
columns match the corresponding view definition.  Returns a list of error
strings (empty if valid).

Optional column groups (defined in legacy_samplesheet_optional_columns) are
allowed to be absent -- the validation only requires the *base* columns.
"""

from __future__ import annotations

from .constants import (
    FIELD_SHEET_TYPE,
    FIELD_SHEET_VERSION,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
    SECTION_HEADER,
)
from .reconstruct import _get_view_columns


def validate_omnibus(conn, sections: dict) -> list[str]:
    """Validate a parsed omnibus file against the view registry.

    Checks performed:
      1. SheetType + SheetVersion map to a known legacy format.
      2. All expected sections are present; no unexpected sections exist.
      3. Column names in each section match the view definition
         (with optional column groups allowed to be absent).
      4. Values-only sections have the expected number of values.

    Args:
        conn: An open SQLite connection with the schema (including
            legacy_samplesheet_format and legacy_samplesheet_view tables)
            already populated.
        sections: The dict returned by parse_omnibus(), keyed by section
            name.

    Returns:
        list[str]: A list of human-readable error messages. An empty list
        means the file is valid.
    """
    cur = conn.cursor()
    errors: list[str] = []

    header = sections.get(SECTION_HEADER, {})
    sheet_type = header.get(FIELD_SHEET_TYPE, "")
    try:
        sheet_version = int(header.get(FIELD_SHEET_VERSION, "0"))
    except ValueError:
        errors.append("Invalid SheetVersion")
        return errors

    # -- Resolve the legacy format ------------------------------------------
    cur.execute(
        "SELECT legacy_format_id FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (sheet_type, sheet_version),
    )
    fmt = cur.fetchone()
    if not fmt:
        errors.append(f"Unknown format: {sheet_type} v{sheet_version}")
        return errors
    legacy_format_id = fmt[0]

    # -- Load optional column groups for this format ------------------------
    optional_cols = _load_optional_columns(cur, legacy_format_id)

    # -- Check section presence ---------------------------------------------
    cur.execute(
        "SELECT section_name, view_name, section_format "
        "FROM legacy_samplesheet_view "
        "WHERE legacy_format_id = ? ORDER BY section_order",
        (legacy_format_id,),
    )
    expected_sections = cur.fetchall()
    expected_names = {name for name, _, _ in expected_sections}
    file_names = set(sections.keys())

    for name in expected_names:
        if name not in file_names:
            errors.append(f"Missing section: [{name}]")
    for name in file_names:
        if name not in expected_names:
            errors.append(f"Unexpected section: [{name}]")

    # -- Per-section column checks ------------------------------------------
    for section_name, view_name, section_format in expected_sections:
        if section_name not in sections:
            continue

        all_view_cols = _get_view_columns(cur, view_name)
        section_optional = optional_cols.get(section_name, set())
        required_cols = [c for c in all_view_cols if c not in section_optional]
        file_section = sections[section_name]

        if section_format == FORMAT_HEADER_KV:
            actual_cols = (
                list(file_section.keys()) if isinstance(file_section, dict) else []
            )
            _check_columns(errors, section_name, required_cols, actual_cols, section_optional)

        elif section_format == FORMAT_VALUES_ONLY:
            if isinstance(file_section, list) and len(file_section) != len(all_view_cols):
                errors.append(
                    f"[{section_name}] expected {len(all_view_cols)} values, "
                    f"got {len(file_section)}"
                )

        elif section_format == FORMAT_TABULAR:
            if file_section:
                actual_cols = list(file_section[0].keys())
                _check_columns(errors, section_name, required_cols, actual_cols, section_optional)

    return errors


def _load_optional_columns(cur, legacy_format_id: int) -> dict[str, set[str]]:
    """Load optional column groups for a legacy format from the database.

    Args:
        cur: An open SQLite cursor.
        legacy_format_id: The primary key of the legacy_samplesheet_format
            row to look up.

    Returns:
        dict[str, set[str]]: Mapping of section name to the set of column
        names that are optional for that section.
    """
    cur.execute(
        "SELECT section_name, column_names "
        "FROM legacy_samplesheet_optional_columns "
        "WHERE legacy_format_id = ?",
        (legacy_format_id,),
    )
    result: dict[str, set[str]] = {}
    for section_name, col_names_csv in cur.fetchall():
        cols = {c.strip() for c in col_names_csv.split(",")}
        result.setdefault(section_name, set()).update(cols)
    return result


def _check_columns(
    errors: list[str],
    section_name: str,
    required: list[str],
    actual: list[str],
    optional: set[str] | None = None,
) -> None:
    """Compare expected vs actual column lists and append errors.

    Required columns must be present. Optional columns may appear but are
    not required. Anything else is flagged as unexpected.

    Args:
        errors: The accumulating list of error strings; new errors are
            appended in place.
        section_name: Name of the section being checked (used in error
            messages).
        required: Column names that must appear in the section.
        actual: Column names that were found in the parsed file.
        optional: Column names that may appear but are not required.
            Defaults to an empty set.
    """
    optional = optional or set()
    required_set = set(required)
    actual_set = set(actual)

    missing = required_set - actual_set
    extra = actual_set - required_set - optional

    if missing:
        errors.append(f"[{section_name}] missing columns: {sorted(missing)}")
    if extra:
        errors.append(f"[{section_name}] unexpected columns: {sorted(extra)}")
