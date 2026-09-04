"""Regenerate the committed native SQLite test files and their snapshots.

Run this by hand — it is deliberately NOT part of the pytest suite, so the
committed files stay a frozen oracle rather than a self-fulfilling one.
Rerun it after an intentional change to schema or population logic (review
the resulting .generated_snapshot.json diff to confirm the change) or when
adding a source CSV.

Each fixture is built to a temporary file, snapshotted, and promoted to the
committed path only when its logical snapshot differs from the one already
committed. Because the snapshot is logical (normalized SQL, sorted rows), a
rebuild that changes only SQLite's binary header — its per-build change
counter or library version — produces an identical snapshot and is discarded,
so no-op binary churn is never committed and the files stay reproducible
across the CI Python matrix without pinning SQLite.

Fixtures carry a content-derived fact suffix (see derive_native_facts): a
plain legacy load is a true preflight and keeps its bare source stem, while a
fixture that adds accessions or PacBio placement carries the matching token so
a consumer can pick the stage their code needs.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# Make the tests package importable when run as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_preflight.file_io import atomic_write  # noqa: E402
from run_preflight import (  # noqa: E402
    migrate_legacy_csv_to_db_file,
    set_biosample_accession,
    set_bioproject_accession,
)

from tests._helpers import (  # noqa: E402
    GOOD_LEGACY_GLOB,
    LEGACY_DATA_DIR,
    NATIVE_DATA_DIR,
    NATIVE_SNAPSHOT_SUFFIX,
    capture_db_snapshot,
    derive_native_facts,
    native_stem,
    open_db,
    snapshot_to_json,
)

# Source CSV for the accessioned (post-submission) PacBio fixture — a simple
# PacBio run that, once every project and sample carries a synthetic accession,
# is a runnable target for get_pacbio_sample_info.
_ACCESSIONED_PACBIO_SOURCE = "good_pacbio_metagv11.csv"

# Synthetic, fixed change_log.changed_at for setter-augmented fixtures. The
# column defaults to datetime('now'), which would otherwise make every
# regeneration diff on wall-clock time; freezing it keeps the fixture
# byte-reproducible.
_FROZEN_CHANGED_AT = "2000-01-01 00:00:00"


def _populate_all_accessions(conn) -> None:
    # Give every project a synthetic bioproject and every named sample a
    # synthetic biosample, so the accession readers return rows without raising.
    project_names = [
        r[0]
        for r in conn.execute("SELECT project_name FROM project ORDER BY project_idx")
    ]
    for i, name in enumerate(project_names, start=1):
        set_bioproject_accession(conn, f"PRJNA{i:06d}", project_name=name)

    sample_names = [
        r[0]
        for r in conn.execute(
            "SELECT sample_name FROM input_sample "
            "WHERE sample_name IS NOT NULL ORDER BY input_sample_idx"
        )
    ]
    for i, name in enumerate(sample_names, start=1):
        set_biosample_accession(conn, name, f"SAMN{i:08d}")


def _build_legacy(csv_path: Path, tmp_dir: Path) -> tuple[Path, str]:
    # Build the plain legacy load; its base stem is the CSV stem
    temp_db = tmp_dir / f"{csv_path.stem}.tmp.sqlite"
    migrate_legacy_csv_to_db_file(str(csv_path), str(temp_db))
    return temp_db, csv_path.stem


def _build_accessioned_pacbio(tmp_dir: Path) -> tuple[Path, str]:
    # Load a PacBio legacy CSV, then back-fill every accession post-load
    src = LEGACY_DATA_DIR / _ACCESSIONED_PACBIO_SOURCE
    temp_db = tmp_dir / "accessioned_pacbio.tmp.sqlite"
    migrate_legacy_csv_to_db_file(str(src), str(temp_db))
    with open_db(str(temp_db)) as conn:
        _populate_all_accessions(conn)
        # Freeze the audit timestamps the setters just wrote so the fixture
        # stays byte-reproducible across regenerations
        conn.execute("UPDATE change_log SET changed_at = ?", (_FROZEN_CHANGED_AT,))
        conn.commit()
    return temp_db, src.stem


def _promote_if_changed(temp_db: Path, base_stem: str) -> None:
    # Snapshot the freshly built DB and derive its fact-based committed name
    with open_db(str(temp_db)) as conn:
        snapshot = capture_db_snapshot(conn)
        facts = derive_native_facts(conn)
    json_text = snapshot_to_json(snapshot)
    stem = native_stem(base_stem, facts)
    target_db = NATIVE_DATA_DIR / f"{stem}.sqlite"
    target_snapshot = NATIVE_DATA_DIR / f"{stem}{NATIVE_SNAPSHOT_SUFFIX}"

    # Promote only when the logical snapshot differs (or the pair is missing),
    # so an unchanged rebuild leaves the committed binary and snapshot untouched
    up_to_date = (
        target_db.exists()
        and target_snapshot.exists()
        and target_snapshot.read_text() == json_text
    )
    if up_to_date:
        temp_db.unlink()
        print(f"unchanged {target_db.name}")
        return
    shutil.move(str(temp_db), str(target_db))
    atomic_write(str(target_snapshot), json_text)
    print(f"wrote {target_db.name}")


def main() -> None:
    NATIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # True-preflight fixtures: one plain load per good_ legacy CSV
        for csv_path in sorted(LEGACY_DATA_DIR.glob(GOOD_LEGACY_GLOB)):
            temp_db, base_stem = _build_legacy(csv_path, tmp_dir)
            _promote_if_changed(temp_db, base_stem)

        # Setter-augmented fixtures: not reproducible by a plain CSV load
        temp_db, base_stem = _build_accessioned_pacbio(tmp_dir)
        _promote_if_changed(temp_db, base_stem)


if __name__ == "__main__":
    main()
