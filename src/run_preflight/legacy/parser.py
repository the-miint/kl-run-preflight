"""Parse a legacy CSV sample sheet into a dict of sections.

Omnibus files contain multiple logical sections delimited by [SectionName]
headers. Each section has one of three formats:

  - header_kv:    Key-value pairs, one per row  (e.g. [Header], [Settings])
  - values_only:  Bare values, one per row       (e.g. [Reads])
  - tabular:      Column header row + data rows  (e.g. [Data], [Contact])

The parser uses a section_formats mapping (section name → format string)
to decide how to parse each section.  This mapping is supplied by the
caller, typically obtained from the DB via ``db.get_section_formats``.

The parser returns a dict keyed by section name. Values are:
  - dict          for header_kv sections
  - list[str]     for values_only sections
  - list[dict]    for tabular sections
"""

from __future__ import annotations

import csv
import io

from ..constants import (
    ASSAY_AMPLICON,
    COL_EMAIL,
    COL_HUMAN_FILTERING,
    COL_LIBRARY_CONSTRUCTION_PROTOCOL,
    COL_QIITA_ID,
    COL_SAMPLE_PROJECT,
    COL_SC_SAMPLE_NAME,
    COL_SC_SAMPLE_TYPE,
    CONTEXT_TYPE_CONTROL_BLANK,
    CONTEXT_TYPE_CONTROL_KATHAROSEQ,
    FIELD_ASSAY,
    FIELD_DATE,
    FIELD_EXPERIMENT_NAME,
    FIELD_SHEET_TYPE,
    FIELD_SHEET_VERSION,
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
    SECTION_BIOINFORMATICS,
    SECTION_CONTACT,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_SAMPLE_CONTEXT,
    SHEET_TYPE_AMPLICON,
)
from ..db import get_amplicon_format_for_header

# Prefixes marking a control in an amplicon prep template's sample_name,
# matched case-insensitively because sheets differ on capitalisation:
# KatharoSeq controls appear as both "KATHARO." and "katharo.".
_CONTROL_PREFIXES: dict[str, str] = {
    "BLANK.": CONTEXT_TYPE_CONTROL_BLANK,
    "KATHARO.": CONTEXT_TYPE_CONTROL_KATHAROSEQ,
}

# Columns whose value is constant per project and lifted into [Bioinformatics].
_AMPLICON_PROJECT_COLUMN = "project_name"
_AMPLICON_PROTOCOL_COLUMN = "library_construction_protocol"
_AMPLICON_SAMPLE_NAME_COLUMN = "sample_name"
_AMPLICON_RUN_DATE_COLUMN = "run_date"


def parse_omnibus(filepath: str, section_formats: dict[str, str]) -> dict:
    """Read a legacy CSV sample sheet and return parsed sections.

    Thin wrapper around parse_omnibus_text that reads the file first.

    Args:
        filepath: Path to the omnibus CSV file on disk.
        section_formats: Mapping of section name to format string
            (e.g. {"Header": "header_kv", "Data": "tabular"}).

    Returns:
        dict: A mapping of section name to parsed content. The value type
        depends on the section format:
          - dict for key-value sections (e.g. Header, Settings)
          - list[str] for values-only sections (e.g. Reads)
          - list[dict] for tabular sections (e.g. Data, Contact)
    """
    text = read_omnibus_text(filepath)
    sections = parse_omnibus_text(text, section_formats)
    return sections


def read_omnibus_text(filepath: str) -> str:
    """Read an omnibus CSV file into text.

    The single place that decides how an omnibus CSV file on disk becomes
    a string. ``newline=""`` leaves embedded line endings intact so the
    csv module sees the file's own quoting and row breaks.

    Args:
        filepath: Path to the omnibus CSV file on disk.

    Returns:
        str: The full file content.
    """
    with open(filepath, newline="") as fh:
        text = fh.read()
    return text


def parse_omnibus_text(text: str, section_formats: dict[str, str]) -> dict:
    """Parse omnibus CSV content from a string and return parsed sections.

    Args:
        text: The full CSV content as a string.
        section_formats: Mapping of section name to format string
            (e.g. {"Header": "header_kv", "Data": "tabular"}).

    Returns:
        dict: A mapping of section name to parsed content. The value type
        depends on the section format:
          - dict for key-value sections (e.g. Header, Settings)
          - list[str] for values-only sections (e.g. Reads)
          - list[dict] for tabular sections (e.g. Data, Contact)
    """
    sections: dict = {}
    current_section: str | None = None
    current_header: list[str] | None = None
    current_rows: list = []

    reader = csv.reader(io.StringIO(text))
    for row in reader:
        # Skip blank rows.
        if not row or all(cell.strip() == "" for cell in row):
            continue

        first = row[0].strip()

        # Detect section boundary — e.g. "[Header]".
        if is_section_header(first):
            # Flush the previous section before starting a new one.
            if current_section is not None:
                sections[current_section] = _finalize_section(
                    current_section,
                    current_header,
                    current_rows,
                    section_formats,
                )
            current_section = extract_section_name(first)
            current_header = None
            current_rows = []
            continue

        # Accumulate rows within the current section.
        if current_section is not None:
            # Strip whitespace and trailing empty cells.
            cleaned = strip_entries(row)
            while cleaned and cleaned[-1] == "":
                cleaned.pop()

            # Determine how to accumulate based on section format
            fmt = section_formats.get(current_section, FORMAT_TABULAR)
            if fmt in (FORMAT_HEADER_KV, FORMAT_VALUES_ONLY):
                # KV and values-only rows are always appended as-is.
                current_rows.append(cleaned)
            elif current_header is None:
                # First non-blank row in a tabular section is the header.
                current_header = cleaned
            else:
                # Subsequent rows are data.
                current_rows.append(cleaned)

    # Flush the final section.
    if current_section is not None:
        sections[current_section] = _finalize_section(
            current_section,
            current_header,
            current_rows,
            section_formats,
        )

    return sections


def is_section_header(stripped_line):
    return stripped_line.startswith("[") and stripped_line.endswith("]")


def extract_section_name(stripped_line):
    return stripped_line[1:-1]


def strip_entries(a_row):
    return [cell.strip() for cell in a_row]


def _finalize_section(
    name: str,
    header: list[str] | None,
    rows: list,
    section_formats: dict[str, str],
):
    """Convert raw row lists into the appropriate Python structure.

    Args:
        name: The section name (e.g. "Header", "Data"), used to determine
            the parsing strategy.
        header: Column header names for tabular sections, or None for
            key-value and values-only sections.
        rows: The accumulated raw row lists for this section.
        section_formats: Mapping of section name to format string.

    Returns:
        dict | list[str] | list[dict]: Parsed section content whose type
        depends on the section format (key-value, values-only, or tabular).
    """
    fmt = section_formats.get(name, FORMAT_TABULAR)

    if fmt == FORMAT_HEADER_KV:
        # Build an ordered dict from key-value rows.
        result = {}
        for row in rows:
            if len(row) >= 2:
                result[row[0]] = row[1]
            elif len(row) == 1:
                result[row[0]] = ""
        return result

    if fmt == FORMAT_VALUES_ONLY:
        # Flatten to a simple list of strings (one value per row).
        return [row[0] for row in rows if row]

    # Tabular: zip each data row against the header to produce a list of dicts.
    result = []
    for row in rows:
        record = {}
        for i, col in enumerate(header):
            record[col] = row[i] if i < len(row) else ""
        result.append(record)
    return result


def control_context_type_for(sample_name: str) -> str | None:
    """Return the SampleContext type for a control, or None for a real sample.

    Matching is case-insensitive but otherwise literal. A name such as
    "BLANK2.2A" is deliberately NOT matched: whether a numbered prefix marks a
    blank is a question about the sheet's convention, not one to settle here.
    """
    upper = sample_name.upper()
    for prefix, context_type in _CONTROL_PREFIXES.items():
        if upper.startswith(prefix):
            return context_type
    return None


def _split_prep_template(text: str, delimiter: str) -> list[list[str]]:
    """Split flat prep-template content into rows, rejecting a malformed sheet.

    The format has no quoting and no embedded delimiters or newlines, so a
    manual split is exact.

    Raises:
        ValueError: If the content has no data rows, or any row's width
            differs from the header's.
    """
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    rows = [line.split(delimiter) for line in lines]
    if len(rows) < 2:
        raise ValueError(
            "prep template has a header but no sample rows; nothing to load"
        )

    width = len(rows[0])
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            raise ValueError(
                f"prep template line {line_number} has {len(row)} columns, "
                f"expected {width}"
            )
    return rows


def parse_amplicon_prep(text: str, conn) -> dict:
    """Parse flat amplicon prep-template content and return the same sections
    a sectioned sheet parses into.

    The sheet is one table with no [Section] labels, so the sections other
    than Data are lifted out of its columns: project-grain facts become
    Bioinformatics and Contact rows, and controls -- identified by their
    sample_name prefix -- become SampleContext rows. Data keeps the sheet's
    own column names, which the format declares.

    Raises:
        ValueError: If the content is malformed or its header matches no
            registered amplicon format.
    """
    cur = conn.cursor()
    delimiter = cur.execute(
        "SELECT delimiter FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? LIMIT 1",
        (SHEET_TYPE_AMPLICON,),
    ).fetchone()[0]

    rows = _split_prep_template(text, delimiter)
    header, data_rows = rows[0], rows[1:]
    _, version = get_amplicon_format_for_header(cur, header)
    data = [dict(zip(header, row)) for row in data_rows]

    # Project-grain facts repeat on every row; collapse them to one entry each.
    first_by_project: dict[str, dict] = {}
    for row in data:
        first_by_project.setdefault(row[_AMPLICON_PROJECT_COLUMN], row)

    # The Qiita study id is the trailing token of the project name, which is
    # the only place these sheets record it.
    bioinformatics = [
        {
            COL_SAMPLE_PROJECT: project_name,
            COL_QIITA_ID: project_name.rsplit("_", 1)[-1],
            # The sheet records nothing about human filtering; the section
            # carries the schema's default so it matches its view.
            COL_HUMAN_FILTERING: "True",
            COL_LIBRARY_CONSTRUCTION_PROTOCOL: row[_AMPLICON_PROTOCOL_COLUMN],
        }
        for project_name, row in first_by_project.items()
    ]

    # The sheet records no contact address; the column exists so the section
    # matches its view, and an empty value round-trips as an empty one.
    contact = [
        {COL_SAMPLE_PROJECT: project_name, COL_EMAIL: ""}
        for project_name in first_by_project
    ]

    sample_context = []
    for row in data:
        context_type = control_context_type_for(row[_AMPLICON_SAMPLE_NAME_COLUMN])
        if context_type is not None:
            sample_context.append(
                {
                    COL_SC_SAMPLE_NAME: row[_AMPLICON_SAMPLE_NAME_COLUMN],
                    COL_SC_SAMPLE_TYPE: context_type,
                }
            )

    first_row = data[0]
    return {
        SECTION_HEADER: {
            FIELD_SHEET_TYPE: SHEET_TYPE_AMPLICON,
            FIELD_SHEET_VERSION: str(version),
            FIELD_ASSAY: ASSAY_AMPLICON,
            FIELD_EXPERIMENT_NAME: first_row[_AMPLICON_PROJECT_COLUMN],
            FIELD_DATE: first_row.get(_AMPLICON_RUN_DATE_COLUMN, ""),
        },
        SECTION_DATA: data,
        SECTION_BIOINFORMATICS: bioinformatics,
        SECTION_CONTACT: contact,
        SECTION_SAMPLE_CONTEXT: sample_context,
    }
