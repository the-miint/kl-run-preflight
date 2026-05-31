"""Validate a parsed omnibus file against the view registry.

Reports any structural problems that would prevent a clean round-trip or
result in silent data corruption.  Returns a list of human-readable error
strings (empty if valid).

The per-section column checks are scoped to match the de-facto round-trip
contract:

  - [Settings] keys are all treated as optional, since the reconstructor's
    _write_header_kv skips NULL values on output.
  - [Data] unexpected columns are accepted because they are routed through
    the legacy_extra_column carry-through mechanism at populate time.
  - All other tabular sections (Bioinformatics, Contact, SampleContext)
    require an exact column-name match against the view definition.
  - Optional column groups (legacy_samplesheet_optional_columns) are
    allowed to be absent in any section.
"""

from __future__ import annotations

from ..constants import (
    EXPECTED_ILLUMINA_HEADER_CONSTANTS,
    FIELD_SHEET_TYPE,
    FIELD_SHEET_VERSION,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SETTINGS,
)
from ..db import get_view_columns


def validate_omnibus(conn, sections: dict) -> list[str]:
    """Validate a parsed omnibus file against the view registry.

    Checks performed:
      1. SheetType and SheetVersion are present in [Header].
      2. SheetVersion parses as an integer.
      3. SheetType + SheetVersion map to a known legacy format.
      4. For non-PacBio formats, the four hardcoded Illumina header
         constants (IEMFileVersion, Workflow, Application, Chemistry),
         if present in the file, match the literals the reconstructor
         emits.  Deviating values would be silently replaced on
         round-trip and so are rejected here.
      5. All expected sections are present; no unexpected sections
         exist.
      6. Per-section column checks, with section-specific contracts:
           * [Header] (header_kv): all required view columns present;
             unexpected keys flagged.
           * [Settings] (header_kv): missing keys are allowed (the
             reconstructor NULL-skips on output); unexpected keys
             flagged.
           * [Data] (tabular): all required view columns present;
             unrecognized columns accepted as legacy_extra_column
             carry-throughs.
           * Other tabular sections (Bioinformatics, Contact,
             SampleContext): exact match required.
           * Values-only sections (e.g. [Reads]): value count matches
             the view's column count.

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

    # SheetType and SheetVersion drive the format lookup; missing values
    # would default into a meaningless ("", 0) lookup, so flag them up front.
    header = sections.get(SECTION_HEADER, {})
    sheet_type = header.get(FIELD_SHEET_TYPE)
    sheet_version_raw = header.get(FIELD_SHEET_VERSION)
    if not sheet_type:
        errors.append(f"[{SECTION_HEADER}] missing required field: {FIELD_SHEET_TYPE}")
    if not sheet_version_raw:
        errors.append(
            f"[{SECTION_HEADER}] missing required field: {FIELD_SHEET_VERSION}"
        )
    if errors:
        return errors

    try:
        sheet_version = int(sheet_version_raw)
    except ValueError:
        errors.append("Invalid SheetVersion")
        return errors

    # -- Resolve the legacy format ------------------------------------------
    cur.execute(
        "SELECT legacy_format_idx FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (sheet_type, sheet_version),
    )
    fmt = cur.fetchone()
    if not fmt:
        errors.append(f"Unknown format: {sheet_type} v{sheet_version}")
        return errors
    legacy_format_idx = fmt[0]

    # -- Reject deviations from Illumina header constants -------------------
    # The omnibus_illumina_header view emits IEMFileVersion, Workflow,
    # Application, and Chemistry as hardcoded literals; the DB does not
    # store the originals. Any deviating input would be silently replaced
    # on round-trip, so reject such files at load time instead.
    # PacBio uses a different header view with no such literals.
    is_pacbio = "pacbio" in sheet_type.lower()
    if not is_pacbio:
        for field, expected in EXPECTED_ILLUMINA_HEADER_CONSTANTS.items():
            observed = header.get(field)
            if observed is not None and observed != expected:
                errors.append(
                    f"[Header] {field}={observed!r} cannot be preserved; "
                    f"only {expected!r} is supported for this samplesheet type"
                )

    # -- Load optional column groups for this format ------------------------
    optional_cols = _load_optional_columns(cur, legacy_format_idx)

    # -- Check section presence ---------------------------------------------
    cur.execute(
        "SELECT section_name, view_name, section_format "
        "FROM legacy_samplesheet_view "
        "WHERE legacy_format_idx = ? ORDER BY section_order",
        (legacy_format_idx,),
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

        all_view_cols = get_view_columns(cur, view_name)
        section_optional = optional_cols.get(section_name, set())
        required_cols = [c for c in all_view_cols if c not in section_optional]
        file_section = sections[section_name]

        if section_format == FORMAT_HEADER_KV:
            # Settings keys may legitimately be absent: the reconstructor's
            # _write_header_kv drops keys whose DB value is NULL on output,
            # so missing Settings keys round-trip cleanly. Header keys back
            # NOT NULL DB fields and remain required.
            allow_missing = section_name == SECTION_SETTINGS
            actual_cols = (
                list(file_section.keys()) if isinstance(file_section, dict) else []
            )
            _check_columns(
                errors,
                section_name,
                required_cols,
                actual_cols,
                section_optional,
                allow_missing=allow_missing,
            )

        elif section_format == FORMAT_VALUES_ONLY:
            if isinstance(file_section, list) and len(file_section) != len(
                all_view_cols
            ):
                errors.append(
                    f"[{section_name}] expected {len(all_view_cols)} values, "
                    f"got {len(file_section)}"
                )

        elif section_format == FORMAT_TABULAR:
            if file_section:
                # Data extras are routed to legacy_extra_column at populate
                # and re-emitted on reconstruct, so unrecognized Data
                # columns are legal carry-throughs (a warning is raised
                # at populate time). Other tabular sections have no
                # extras mechanism, so unrecognized columns are errors.
                allow_unrecognized = section_name == SECTION_DATA
                actual_cols = list(file_section[0].keys())
                _check_columns(
                    errors,
                    section_name,
                    required_cols,
                    actual_cols,
                    section_optional,
                    allow_unrecognized=allow_unrecognized,
                )

    return errors


def _load_optional_columns(cur, legacy_format_idx: int) -> dict[str, set[str]]:
    """Load optional column groups for a legacy format from the database.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The primary key of the legacy_samplesheet_format
            row to look up.

    Returns:
        dict[str, set[str]]: Mapping of section name to the set of column
        names that are optional for that section.
    """
    cur.execute(
        "SELECT section_name, column_names "
        "FROM legacy_samplesheet_optional_columns "
        "WHERE legacy_format_idx = ?",
        (legacy_format_idx,),
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
    *,
    allow_unrecognized: bool = False,
    allow_missing: bool = False,
) -> None:
    """Compare expected vs actual column lists and append errors.

    Required columns must be present and unknown columns are flagged as
    unexpected, except where the section's contract opts out via one of
    the keyword flags below.

    Args:
        errors: The accumulating list of error strings; new errors are
            appended in place.
        section_name: Name of the section being checked (used in error
            messages).
        required: Column names that must appear in the section.
        actual: Column names that were found in the parsed file.
        optional: Column names that may appear but are not required.
            Defaults to an empty set.
        allow_unrecognized: When True, suppress the "unexpected columns"
            error for this section. Callers should set this only for
            sections that support the extras carry-through pattern
            (currently the Data section).
        allow_missing: When True, suppress the "missing columns" error
            for this section. Callers should set this only for sections
            whose reconstructor tolerates absent columns (currently the
            Settings section, where _write_header_kv NULL-skips).
    """
    optional = optional or set()
    required_set = set(required)
    actual_set = set(actual)

    missing = required_set - actual_set
    extra = actual_set - required_set - optional
    if allow_unrecognized:
        extra = set()
    if allow_missing:
        missing = set()

    if missing:
        errors.append(f"[{section_name}] missing columns: {sorted(missing)}")
    if extra:
        errors.append(f"[{section_name}] unexpected columns: {sorted(extra)}")
