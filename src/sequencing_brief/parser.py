"""Parse an omnibus sample-sheet CSV into a dict of sections.

Omnibus files contain multiple logical sections delimited by [SectionName]
headers. Each section has one of three formats:

  - header_kv:    Key-value pairs, one per row  (e.g. [Header], [Settings])
  - values_only:  Bare values, one per row       (e.g. [Reads])
  - tabular:      Column header row + data rows  (e.g. [Data], [Contact])

The parser auto-detects format by section name and returns a dict keyed
by section name. Values are:
  - dict          for header_kv sections
  - list[str]     for values_only sections
  - list[dict]    for tabular sections
"""

from __future__ import annotations

import csv

from .constants import SECTION_HEADER, SECTION_READS, SECTION_SETTINGS

# Section names whose rows are key-value pairs (col 0 = key, col 1 = value).
KV_SECTIONS = {SECTION_HEADER, SECTION_SETTINGS}

# Section names whose rows are bare values (one meaningful value per row).
VALUES_ONLY_SECTIONS = {SECTION_READS}


def parse_omnibus(filepath: str) -> dict:
    """Read an omnibus sample-sheet CSV and return parsed sections.

    Args:
        filepath: Path to the omnibus CSV file on disk.

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

    with open(filepath, newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            # Skip blank rows.
            if not row or all(cell.strip() == "" for cell in row):
                continue

            first = row[0].strip()

            # Detect section boundary — e.g. "[Header]".
            if first.startswith("[") and first.endswith("]"):
                # Flush the previous section before starting a new one.
                if current_section is not None:
                    sections[current_section] = _finalize_section(
                        current_section, current_header, current_rows
                    )
                current_section = first[1:-1]
                current_header = None
                current_rows = []
                continue

            # Accumulate rows within the current section.
            if current_section is not None:
                # Strip whitespace and trailing empty cells.
                cleaned = [cell.strip() for cell in row]
                while cleaned and cleaned[-1] == "":
                    cleaned.pop()

                if current_section in KV_SECTIONS:
                    # KV rows are always appended as-is.
                    current_rows.append(cleaned)
                elif current_section in VALUES_ONLY_SECTIONS:
                    # Values-only rows are always appended as-is.
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
                current_section, current_header, current_rows
            )

    return sections


def _finalize_section(
    name: str,
    header: list[str] | None,
    rows: list,
):
    """Convert raw row lists into the appropriate Python structure.

    Args:
        name: The section name (e.g. "Header", "Data"), used to determine
            the parsing strategy.
        header: Column header names for tabular sections, or None for
            key-value and values-only sections.
        rows: The accumulated raw row lists for this section.

    Returns:
        dict | list[str] | list[dict]: Parsed section content whose type
        depends on the section format (key-value, values-only, or tabular).
    """
    if name in KV_SECTIONS:
        # Build an ordered dict from key-value rows.
        result = {}
        for row in rows:
            if len(row) >= 2:
                result[row[0]] = row[1]
            elif len(row) == 1:
                result[row[0]] = ""
        return result

    if name in VALUES_ONLY_SECTIONS:
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
