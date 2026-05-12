"""Patch NNN: <description>.

Rename this file to NNN_description.py (three-digit zero-padded prefix).
The migration runner calls apply(conn) and sets PRAGMA user_version
afterward — do not set user_version in this function.
"""

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    """Apply this patch to the database."""
    raise NotImplementedError("Replace this with patch logic")
