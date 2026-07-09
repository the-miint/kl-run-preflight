"""Regenerate the committed native SQLite test files and their snapshots.

Run this by hand — it is deliberately NOT part of the pytest suite, so the
committed files stay a frozen oracle rather than a self-fulfilling one.
Rerun it after an intentional change to schema or population logic (review
the resulting .generated_snapshot.json diff to confirm the change) or when
adding a source CSV. For each good_ legacy CSV it writes the paired native
.sqlite; for every native .sqlite present (legacy-backed or native-only) it
writes the derived snapshot image the test suite byte-compares against.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the tests package importable when run as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_preflight import migrate_legacy_csv_to_db_file  # noqa: E402

from tests._helpers import (  # noqa: E402
    GOOD_LEGACY_GLOB,
    LEGACY_DATA_DIR,
    NATIVE_DATA_DIR,
    NATIVE_SNAPSHOT_SUFFIX,
    capture_db_snapshot,
    open_db,
    snapshot_to_json,
)


def _regenerate_sqlite_from_legacy() -> None:
    # Rebuild the native .sqlite for every good_ legacy CSV
    for csv_path in sorted(LEGACY_DATA_DIR.glob(GOOD_LEGACY_GLOB)):
        db_path = NATIVE_DATA_DIR / f"{csv_path.stem}.sqlite"
        migrate_legacy_csv_to_db_file(str(csv_path), str(db_path))
        print(f"wrote {db_path.name}")


def _regenerate_snapshots() -> None:
    # Derive a snapshot image for every native .sqlite (legacy-backed or not)
    for db_path in sorted(NATIVE_DATA_DIR.glob("*.sqlite")):
        with open_db(str(db_path)) as conn:
            snapshot = capture_db_snapshot(conn)
        snapshot_path = NATIVE_DATA_DIR / f"{db_path.stem}{NATIVE_SNAPSHOT_SUFFIX}"
        snapshot_path.write_text(snapshot_to_json(snapshot))
        print(f"wrote {snapshot_path.name}")


def main() -> None:
    NATIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _regenerate_sqlite_from_legacy()
    _regenerate_snapshots()


if __name__ == "__main__":
    main()
