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

# Boolean connection settings that a rollback does not undo, so the runner
# saves and restores each around patching
_PRAGMA_FOREIGN_KEYS = "foreign_keys"
_PRAGMA_LEGACY_ALTER_TABLE = "legacy_alter_table"
_RESTORED_PRAGMA_FLAGS = (_PRAGMA_FOREIGN_KEYS, _PRAGMA_LEGACY_ALTER_TABLE)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchemaVersionTooNewError(ValueError):
    """A database was written by a newer run_preflight than is installed.

    Subclasses ValueError so a caller that does not distinguish this
    case still catches it alongside the other bad-input errors.
    """


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


def _read_pragma_flag(conn: sqlite3.Connection, name: str) -> bool:
    """Return the current value of the boolean pragma *name*."""
    cur = conn.execute(f"PRAGMA {name}")
    row = cur.fetchone()
    return bool(row[0])


def _write_pragma_flag(conn: sqlite3.Connection, name: str, enabled: bool) -> None:
    """Set the boolean pragma *name*."""
    conn.execute(f"PRAGMA {name} = {'ON' if enabled else 'OFF'}")


def _foreign_key_violations(conn: sqlite3.Connection) -> set[tuple]:
    """Return every current foreign-key violation as a hashable row."""
    cur = conn.execute("PRAGMA foreign_key_check")
    rows = cur.fetchall()
    return set(rows)


def _load_py_patch(patch_num: int, patch_path: Path):
    """Import a ``.py`` patch module and return it.

    The module must define an ``apply(conn)`` entry point; the caller runs
    that inside the patch transaction.
    """
    # Load by file path rather than package import so patches need not be
    # importable modules of the run_preflight package.
    spec = importlib.util.spec_from_file_location(f"_patch_{patch_num:03d}", patch_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "apply"):
        raise AttributeError(
            f"Patch {patch_path.name} must define an apply(conn) function"
        )
    return module


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
        SchemaVersionTooNewError: If the database version exceeds the
            latest patch.
        ValueError: If a patch file is missing from the expected
            sequence.
    """
    resolved = _resolve_patches_dir(patches_dir)
    patches = _discover_patches(resolved)
    current = get_schema_version(conn)
    latest = max(patches.keys(), default=0)

    # Check for DB newer than code
    if current > latest:
        raise SchemaVersionTooNewError(
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

    A ``.py`` patch must define an ``apply(conn)`` entry point. Foreign-key
    enforcement is suspended while patches run, so a patch may rebuild a
    referenced table; a patch that introduces a foreign-key violation is
    rolled back, while violations already present are not attributed to it.
    The connection settings the runner saves are restored afterward,
    whether or not a patch fails.

    Args:
        conn: An open SQLite connection.
        patches_dir: Directory to scan.  Defaults to the built-in
            ``sql/patches/`` directory.

    Returns:
        int: The new schema version after all patches are applied.

    Raises:
        SchemaVersionTooNewError: If the database version exceeds the
            latest patch.
        ValueError: If a patch file is missing from the expected
            sequence.
        sqlite3.IntegrityError: If a patch introduces a foreign-key
            violation.
        RuntimeError: If patches are pending and the connection has an
            open transaction.
    """
    pending = get_pending_patches(conn, patches_dir)

    # A current database needs no connection-setting changes
    if not pending:
        current_version = get_schema_version(conn)
        return current_version

    # Refuse an open transaction: SQLite ignores the FK pragma inside one,
    # and running a patch script would commit the caller's pending writes
    if conn.in_transaction:
        raise RuntimeError(
            "Cannot apply patches; the connection has an open transaction"
        )

    # Suspend FK enforcement for the patches
    saved_flags = {
        name: _read_pragma_flag(conn, name) for name in _RESTORED_PRAGMA_FLAGS
    }
    _write_pragma_flag(conn, _PRAGMA_FOREIGN_KEYS, False)
    try:
        for patch_num, patch_path in pending:
            # Apply the patch body and stamp its version inside a single
            # transaction: SQLite DDL is transactional and PRAGMA user_version
            # participates in it, so a statement failing part-way rolls the
            # whole patch back rather than stranding a half-applied schema at
            # the prior version (which would make every later open re-run and
            # re-fail it).
            try:
                violations_before = _foreign_key_violations(conn)
                if patch_path.suffix == ".sql":
                    # executescript commits any open transaction first, then
                    # runs the script verbatim, leaving its BEGIN open
                    patch_sql = patch_path.read_text()
                    conn.executescript(f"BEGIN;\n{patch_sql}")
                elif patch_path.suffix == ".py":
                    module = _load_py_patch(patch_num, patch_path)
                    conn.execute("BEGIN")
                    module.apply(conn)

                # Refuse to commit a patch that broke referential integrity
                violations_after = _foreign_key_violations(conn)
                new_violations = sorted(violations_after - violations_before, key=str)
                if new_violations:
                    raise sqlite3.IntegrityError(
                        f"Patch {patch_path.name} left foreign-key violations: "
                        f"{new_violations}"
                    )
                conn.execute(f"PRAGMA user_version = {patch_num}")
                conn.execute("COMMIT")
            except Exception:
                # Undo the partially-applied patch so a later retry starts clean
                conn.rollback()
                raise
    finally:
        for name, enabled in saved_flags.items():
            _write_pragma_flag(conn, name, enabled)

    # Return the last applied patch's version without re-reading the PRAGMA
    final_version = pending[-1][0]
    return final_version
