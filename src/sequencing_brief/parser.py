"""Parse an omnibus sample-sheet CSV into a dict of sections.

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

from .constants import (
    FORMAT_HEADER_KV,
    FORMAT_TABULAR,
    FORMAT_VALUES_ONLY,
)


def parse_omnibus(filepath: str, section_formats: dict[str, str]) -> dict:
    """Read an omnibus sample-sheet CSV and return parsed sections.

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
    with open(filepath, newline="") as fh:
        return parse_omnibus_text(fh.read(), section_formats)


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
