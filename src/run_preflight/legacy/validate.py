"""Validate a parsed omnibus file against the view registry.

Reports any structural problems that would prevent a clean round-trip
or result in silent data corruption. Returns a list of human-readable
error strings (empty if valid).
"""

from __future__ import annotations

from ..constants import (
    COL_QIITA_ID,
    EXPECTED_ILLUMINA_HEADER_CONSTANTS,
    FIELD_SHEET_TYPE,
    FIELD_SHEET_VERSION,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
    SECTION_BIOINFORMATICS,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SETTINGS,
)
from ..db import (
    get_format_sections,
    get_legacy_format_idx,
    get_optional_columns_by_section,
    get_view_columns,
)


def validate_omnibus(conn, sections: dict) -> list[str]:
    """Validate a parsed omnibus file against the view registry.

    Args:
        conn: An open SQLite connection with the legacy format registry
            tables populated.
        sections: The dict returned by parse_omnibus(), keyed by section
            name.

    Returns:
        list[str]: Human-readable error messages, one per problem found.
        An empty list means the file is valid.
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
    legacy_format_idx = get_legacy_format_idx(cur, sheet_type, sheet_version)
    if legacy_format_idx is None:
        errors.append(f"Unknown format: {sheet_type} v{sheet_version}")
        return errors

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
    optional_cols = get_optional_columns_by_section(cur, legacy_format_idx)

    # -- Check section presence ---------------------------------------------
    expected_sections = get_format_sections(cur, legacy_format_idx)
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
            # Settings keys may legitimately be absent: NULL DB values
            # are dropped on output, so missing Settings keys round-trip
            # cleanly. Header keys back NOT NULL DB fields and remain
            # required.
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

                # An empty QiitaID would pass the CHECK constraint but
                # leave external_project_id semantically meaningless.
                if section_name == SECTION_BIOINFORMATICS:
                    for row in file_section:
                        if row.get(COL_QIITA_ID) == "":
                            errors.append(
                                f"[{section_name}] {COL_QIITA_ID} cell is "
                                "empty; every project requires a non-empty value"
                            )
                            break

    return errors


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
        section_name: Name of the section being checked.
        required: Column names that must appear in the section.
        actual: Column names that were found in the parsed file.
        optional: Column names that may appear but are not required.
            Defaults to an empty set.
        allow_unrecognized: When True, suppress the "unexpected columns"
            error (for sections supporting extras carry-through).
        allow_missing: When True, suppress the "missing columns" error
            (for sections whose downstream consumer tolerates absence).
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
