"""Format-detecting entry point that opens either a legacy omnibus CSV or a SQLite database file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .legacy.api import load_legacy_csv
from .migrate import open_db_file

# SQLite database files begin with this 16-byte magic header (see https://sqlite.org/fileformat.html)
_SQLITE_MAGIC = b"SQLite format 3\x00"


def open_file(path: str) -> sqlite3.Connection:
    """Open a sample sheet from either a legacy omnibus CSV or a SQLite DB file.

    Detects the format from the file's first 16 bytes: files beginning
    with the SQLite magic header are opened via *open_db_file*; everything
    else is treated as a legacy omnibus CSV via *load_legacy_csv*. Caller
    owns and must close the returned connection.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file is detected as legacy CSV but fails parsing
            or validation (re-raised from load_legacy_csv).
    """
    # Confirm the file exists before any read attempt so the error is unambiguous
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {path}")

    # Read just enough bytes to identify the SQLite magic header
    with p.open("rb") as fh:
        head = fh.read(len(_SQLITE_MAGIC))

    # Dispatch on detected format
    if head == _SQLITE_MAGIC:
        return open_db_file(path)
    return load_legacy_csv(path)
