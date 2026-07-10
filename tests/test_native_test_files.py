"""Guards for the committed native-format test files.

Enforces five invariants over ``tests/data/native/``: every good_ legacy
CSV has a committed true-preflight ``.sqlite`` + snapshot (coverage); the
native directory is internally paired (every ``.sqlite`` has a snapshot and
vice versa); each committed ``.sqlite`` reproduces its committed snapshot
(consistency); freshly loading each good_ legacy CSV reproduces its
true-preflight snapshot (correctness); and every committed ``.sqlite``'s
fact-suffix matches the data it actually holds (labelling). A source CSV may
back more than one fixture — a bare true-preflight load plus setter-augmented
fixtures carrying a fact suffix. A failure names the offending file and points
at the regenerator, so a forgotten regeneration fails loudly rather than
leaving a silent gap.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from run_preflight import (
    PacbioSampleRow,
    PlatformSampleInfo,
    get_pacbio_sample_info,
    migrate_legacy_csv_to_db_file,
)

from ._helpers import (
    GOOD_LEGACY_GLOB,
    LEGACY_DATA_DIR,
    NATIVE_DATA_DIR,
    NATIVE_SNAPSHOT_SUFFIX,
    capture_db_snapshot,
    derive_native_facts,
    facts_in_native_stem,
    open_db,
    snapshot_to_json,
)

_REGEN_HINT = "run scripts/generate_native_test_files.py to regenerate"


def _sqlite_path(stem: str) -> Path:
    return NATIVE_DATA_DIR / f"{stem}.sqlite"


def _snapshot_path(stem: str) -> Path:
    return NATIVE_DATA_DIR / f"{stem}{NATIVE_SNAPSHOT_SUFFIX}"


def _snapshot_json_of(db_path: Path) -> str:
    # Capture the canonical snapshot JSON for a DB file on disk
    with open_db(str(db_path)) as conn:
        snapshot = capture_db_snapshot(conn)
    return snapshot_to_json(snapshot)


class TestNativeTestFiles(unittest.TestCase):
    def test_every_good_legacy_csv_has_native_pair(self):
        # Coverage: each good_ legacy CSV requires a committed .sqlite + snapshot
        missing = []
        for csv_path in sorted(LEGACY_DATA_DIR.glob(GOOD_LEGACY_GLOB)):
            for partner in (_sqlite_path(csv_path.stem), _snapshot_path(csv_path.stem)):
                if not partner.exists():
                    missing.append(partner.name)
        self.assertEqual(
            missing, [], f"missing native files ({_REGEN_HINT}): {missing}"
        )

    def test_native_files_are_paired(self):
        # Pairing: every .sqlite has a snapshot and every snapshot has a .sqlite
        sqlite_stems = {p.stem for p in NATIVE_DATA_DIR.glob("*.sqlite")}
        snapshot_stems = {
            p.name[: -len(NATIVE_SNAPSHOT_SUFFIX)]
            for p in NATIVE_DATA_DIR.glob(f"*{NATIVE_SNAPSHOT_SUFFIX}")
        }
        self.assertEqual(
            sqlite_stems,
            snapshot_stems,
            f"unpaired native files ({_REGEN_HINT}): "
            f"sqlite-only={sqlite_stems - snapshot_stems}, "
            f"snapshot-only={snapshot_stems - sqlite_stems}",
        )

    def test_committed_sqlite_matches_its_snapshot(self):
        # Consistency: each committed .sqlite reproduces its committed snapshot,
        # catching a hand-edited or stale binary the reviewer cannot read
        for db_path in sorted(NATIVE_DATA_DIR.glob("*.sqlite")):
            snapshot_path = _snapshot_path(db_path.stem)
            with self.subTest(native=db_path.name):
                self.assertTrue(
                    snapshot_path.exists(),
                    f"{db_path.name} has no snapshot ({_REGEN_HINT})",
                )
                self.assertEqual(
                    _snapshot_json_of(db_path),
                    snapshot_path.read_text(),
                    f"{db_path.name} does not match its snapshot ({_REGEN_HINT})",
                )

    def test_legacy_backed_sqlite_matches_fresh_load(self):
        # Correctness: freshly loading each good_ CSV reproduces the committed
        # snapshot, catching population regressions the round-trip cannot
        for csv_path in sorted(LEGACY_DATA_DIR.glob(GOOD_LEGACY_GLOB)):
            snapshot_path = _snapshot_path(csv_path.stem)
            with self.subTest(csv=csv_path.name):
                self.assertTrue(
                    snapshot_path.exists(),
                    f"{csv_path.name} has no native snapshot ({_REGEN_HINT})",
                )
                # Load into a throwaway DB so the committed file is untouched
                with tempfile.TemporaryDirectory() as tmp:
                    fresh_db = Path(tmp) / f"{csv_path.stem}.sqlite"
                    migrate_legacy_csv_to_db_file(str(csv_path), str(fresh_db))
                    fresh_json = _snapshot_json_of(fresh_db)
                self.assertEqual(
                    fresh_json,
                    snapshot_path.read_text(),
                    f"fresh load of {csv_path.name} differs from its committed "
                    f"snapshot ({_REGEN_HINT})",
                )

    def test_native_filename_matches_derived_facts(self):
        # Labelling: a fixture's fact-suffix must match the data it holds, so a
        # consumer selecting by suffix cannot be misled by a mislabelled file
        for db_path in sorted(NATIVE_DATA_DIR.glob("*.sqlite")):
            with self.subTest(native=db_path.name):
                with open_db(str(db_path)) as conn:
                    actual_facts = derive_native_facts(conn)
                declared_facts = facts_in_native_stem(db_path.stem)
                self.assertEqual(
                    declared_facts,
                    actual_facts,
                    f"{db_path.name} fact-suffix {declared_facts} does not match "
                    f"its contents {actual_facts} ({_REGEN_HINT})",
                )


class TestAccessionedFixtureIsRunnable(unittest.TestCase):
    """The accessioned PacBio fixture is a runnable get_pacbio_sample_info target."""

    def test_get_pacbio_sample_info(self):
        # A true-preflight fixture raises (no accessions); this post-submission
        # fixture is the shipped example that reads cleanly through the reader
        db_path = _sqlite_path("good_pacbio_metagv11.accessioned")
        with open_db(str(db_path)) as conn:
            result = get_pacbio_sample_info(conn)
        expected = [
            PlatformSampleInfo(
                1,
                "standard",
                "SAMN00000001",
                "PRJNA000001",
                [],
                PacbioSampleRow(
                    barcode_id="bc3011",
                    twist_adaptor_id="16_UDI_1_A01_F--16_UDI_1_A01_R",
                    syndna_is_twisted=None,
                    smrt_cell_well_sample_id=None,
                    movie_context_id=None,
                ),
            ),
            PlatformSampleInfo(
                2,
                "standard",
                "SAMN00000002",
                "PRJNA000001",
                [],
                PacbioSampleRow(
                    barcode_id="bc0112",
                    twist_adaptor_id="16_UDI_2_B01_F--16_UDI_2_B01_R",
                    syndna_is_twisted=None,
                    smrt_cell_well_sample_id=None,
                    movie_context_id=None,
                ),
            ),
            PlatformSampleInfo(
                3,
                "extraction_blank",
                "SAMN00000003",
                "PRJNA000001",
                [],
                PacbioSampleRow(
                    barcode_id="bc9992",
                    twist_adaptor_id="16_UDI_5_E01_F--16_UDI_5_E01_R",
                    syndna_is_twisted=None,
                    smrt_cell_well_sample_id=None,
                    movie_context_id=None,
                ),
            ),
        ]
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
