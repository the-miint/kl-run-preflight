"""Shared formatting utilities for omnibus CSV reconstruction.

These helpers handle the translation between Python/SQLite types and the
string representations expected in omnibus CSV files.
"""

import re

from ..constants import (
    COL_BARCODES_ARE_RC,
    COL_CONTAINS_REPLICATES,
    COL_HUMAN_FILTERING,
    COL_SYNDNA_IS_TWISTED,
    FIELD_REVERSE_COMPLEMENT,
)

# Columns whose DB values are 0/1 but whose CSV representation is True/False.
BOOLEAN_COLUMNS = {
    COL_HUMAN_FILTERING,
    COL_CONTAINS_REPLICATES,
    COL_SYNDNA_IS_TWISTED,
    COL_BARCODES_ARE_RC,
}

# Columns stored as integers in the DB but written as bare integers in CSV
# (not True/False).  E.g. ReverseComplement is "0" or "1".
INTEGER_BOOL_COLUMNS = {FIELD_REVERSE_COMPLEMENT}


def bcl_scrub_name(name: str) -> str:
    """Sanitise a sample name for BCL conversion.

    Replaces any character that isn't alphanumeric, hyphen, or underscore
    with an underscore — matching the bcl2fastq convention.

    Args:
        name: The raw sample name to sanitise.

    Returns:
        str: A copy of *name* with disallowed characters replaced by
        underscores.
    """
    return re.sub(r"[^0-9a-zA-Z\-\_]+", "_", name)


def format_value(val, col_name: str) -> str:
    """Format a single DB cell for CSV output.

    Rules:
      - Boolean columns → "True" / "False" (empty if NULL)
      - Integer-bool columns → "0" / "1"
      - NULL → empty string
      - Floats that are whole numbers → no decimal (e.g. 110.0 → "110")
      - Everything else → str()

    Args:
        val: The value retrieved from SQLite (may be None, int, float,
            or str).
        col_name: The CSV column name, used to decide which formatting
            rule to apply.

    Returns:
        str: The formatted string representation suitable for writing to
        a CSV cell.
    """
    if col_name in BOOLEAN_COLUMNS:
        return "" if val is None else ("True" if val else "False")

    if col_name in INTEGER_BOOL_COLUMNS:
        return str(int(val)) if val is not None else ""

    if val is None:
        return ""

    if isinstance(val, float):
        # Emit "110" instead of "110.0" when the fractional part is zero.
        return str(int(val)) if val == int(val) else str(val)

    return str(val)
