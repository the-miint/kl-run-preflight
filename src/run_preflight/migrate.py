"""Schema migration infrastructure for SQLite database files.

Applies numbered patch files (SQL or Python) to bring existing databases
forward to the latest schema version.  Version tracking uses SQLite's
built-in ``PRAGMA user_version``.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

_PATCHES_DIR = Path(__file__).resolve().parent / "sql" / "patches"
_PATCH_PATTERN = re.compile(r"^(\d{3})_.+\.(sql|py)$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_patches_dir(patches_dir: Path | None) -> Path:
    """Return *patches_dir* or the default built-in directory."""
    return patches_dir if patches_dir is not None else _PATCHES_DIR


def _discover_patches(patches_dir: Path) -> dict[int, Path]:
    """Return a mapping of patch number to file path.

    Only files matching the ``NNN_description.sql`` or
    ``NNN_description.py`` pattern are included.
    """
    patches: dict[int, Path] = {}
    for entry in sorted(patches_dir.iterdir()):
        m = _PATCH_PATTERN.match(entry.name)
        if m:
            patches[int(m.group(1))] = entry
    return patches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Read the current schema version from the database.

    Args:
        conn: An open SQLite connection.

    Returns:
        int: The value of ``PRAGMA user_version``.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    return row[0] if row else 0


def get_latest_version(patches_dir: Path | None = None) -> int:
    """Return the highest patch number found in the patches directory.

    Scans for files matching ``NNN_description.sql`` or
    ``NNN_description.py``.  Returns 0 if the directory is empty or
    contains no valid patch files.

    Args:
        patches_dir: Directory to scan.  Defaults to the built-in
            ``sql/patches/`` directory.

    Returns:
        int: The highest patch number, or 0 if none exist.
    """
    resolved = _resolve_patches_dir(patches_dir)
    patches = _discover_patches(resolved)
    return max(patches.keys(), default=0)


def get_pending_patches(
    conn: sqlite3.Connection,
    patches_dir: Path | None = None,
) -> list[tuple[int, Path]]:
    """Return the ordered list of patch files that need to be applied.

    Compares the database's current ``user_version`` against available
    patch files and returns those with a number greater than the current
    version.  Raises if there is a gap in the patch sequence or if the
    database version exceeds the latest patch.

    Args:
        conn: An open SQLite connection.
        patches_dir: Directory to scan.  Defaults to the built-in
            ``sql/patches/`` directory.

    Returns:
        list[tuple[int, Path]]: (patch_number, file_path) pairs in
        ascending version order.

    Raises:
        ValueError: If a patch file is missing from the expected
            sequence or the database version exceeds the latest patch.
    """
    resolved = _resolve_patches_dir(patches_dir)
    patches = _discover_patches(resolved)
    current = get_schema_version(conn)
    latest = max(patches.keys(), default=0)

    # Check for DB newer than code
    if current > latest:
        raise ValueError(
            f"Database version {current} exceeds latest patch {latest}; "
            f"update the run_preflight package"
        )

    # Validate no gaps in the patch sequence
    if patches:
        expected_range = range(1, latest + 1)
        missing = [n for n in expected_range if n not in patches]
        if missing:
            raise ValueError(
                f"Patch sequence has missing files: "
                f"{', '.join(f'{n:03d}' for n in missing)}"
            )

    # Return only patches newer than the current version
    pending = [(n, patches[n]) for n in sorted(patches.keys()) if n > current]
    return pending


def apply_patches(
    conn: sqlite3.Connection,
    patches_dir: Path | None = None,
) -> int:
    """Apply all pending patches to the database in order.

    For ``.sql`` patches, executes via ``executescript``.  For ``.py``
    patches, loads the module by file path and calls its
    ``apply(conn)`` function.  After each patch succeeds, sets
    ``PRAGMA user_version`` to the patch number.

    Args:
        conn: An open SQLite connection.
        patches_dir: Directory to scan.  Defaults to the built-in
            ``sql/patches/`` directory.

    Returns:
        int: The new schema version after all patches are applied.
    """
    pending = get_pending_patches(conn, patches_dir)

    for patch_num, patch_path in pending:
        # Dispatch based on file type
        if patch_path.suffix == ".sql":
            conn.executescript(patch_path.read_text())
        elif patch_path.suffix == ".py":
            # Load the module by file path (not package import)
            spec = importlib.util.spec_from_file_location(
                f"_patch_{patch_num:03d}", patch_path
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Validate patch defines the required entry point
            if not hasattr(module, "apply"):
                raise AttributeError(
                    f"Patch {patch_path.name} must define an apply(conn) function"
                )
            module.apply(conn)

        # Runner owns version stamping
        conn.execute(f"PRAGMA user_version = {patch_num}")

    # Return final version without re-reading PRAGMA when patches were applied
    return pending[-1][0] if pending else get_schema_version(conn)


def open_db(
    db_path: str,
    patches_dir: Path | None = None,
) -> sqlite3.Connection:
    """Open an existing SQLite database and apply any pending patches.

    Enables foreign-key enforcement, checks the schema version, and
    applies patches as needed.

    Args:
        db_path: Filesystem path to the SQLite database file.
        patches_dir: Directory to scan for patches.  Defaults to the
            built-in ``sql/patches/`` directory.

    Returns:
        sqlite3.Connection: An open connection at the latest schema
        version with foreign-key enforcement enabled.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    apply_patches(conn, patches_dir)
    return conn
