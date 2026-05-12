"""Round-trip tests: load CSV → DB → write CSV → byte-compare to original."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from run_preflight import load_legacy_csv
from run_preflight.legacy.roundtrip import roundtrip_via_api

DATA_DIR = Path(__file__).parent / "data"


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        # Per-test scratch dir for the intermediate DB and reconstructed CSV
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _assert_roundtrips(self, csv_name: str):
        normalized, reconstructed = roundtrip_via_api(DATA_DIR / csv_name, self.tmp_dir)
        self.assertEqual(normalized, reconstructed)

    def test_good_pacbio_absquantv11(self):
        self._assert_roundtrips("good_pacbio_absquantv11.csv")

    def test_pacbio_v11_absquant_unpooled(self):
        self._assert_roundtrips("pacbio_v11_absquant_unpooled_sample_sheet.csv")

    def test_skin_replicates_novaseq(self):
        self._assert_roundtrips("Test1_Skin_replicates_15459_novaseq.csv")

    def test_celeste_adaptation_novaseq(self):
        self._assert_roundtrips(
            "YYYY_MM_DD_Celeste_Adaptation_12986_16_17_18_21_matrix_samplesheet_novaseq.csv"
        )

    def test_good_pacbio_metagv11(self):
        self._assert_roundtrips("good_pacbio_metagv11.csv")

    def test_good_pacbio_metagv10(self):
        self._assert_roundtrips("good_pacbio_metagv10.csv")

    def test_good_pacbio_absquantv10(self):
        self._assert_roundtrips("good_pacbio_absquantv10.csv")

    def test_good_standard_metagv90(self):
        self._assert_roundtrips("good_standard_metagv90.csv")

    def test_good_standard_metagv0_really_metat(self):
        self._assert_roundtrips("good_standard_metagv0_really_metat.csv")

    def test_good_standard_metagv100_wo_replicates(self):
        self._assert_roundtrips("good_standard_metagv100_wo_replicates.csv")

    def test_good_abs_quant_metagv10(self):
        self._assert_roundtrips("good_abs_quant_metagv10.csv")

    def test_good_abs_quant_metagv11(self):
        self._assert_roundtrips("good_abs_quant_metagv11.csv")

    def test_good_standard_metatv10(self):
        self._assert_roundtrips("good_standard_metatv10.csv")

    def test_good_tellseq_metagv10(self):
        self._assert_roundtrips("good_tellseq_metagv10.csv")

    def test_good_tellseq_absquantv10(self):
        self._assert_roundtrips(
            "Tellseq_absquant_samplesheet_spp_novaseqxplus_set_col19to24.csv"
        )

    def test_standard_metagv100_w_replicates_rejected(self):
        # Pre-v101 files with replicates are unsupported and must be rejected
        # at load time before any DB writes happen
        csv_path = DATA_DIR / "good_standard_metagv100_w_replicates.csv"
        db_path = self.tmp_dir / "rejected.db"
        with self.assertRaisesRegex(
            ValueError, r"Replicates in legacy version.*v101 or later"
        ):
            load_legacy_csv(str(csv_path), str(db_path))

    def test_good_multilane_synthetic(self):
        self._assert_roundtrips("good_multilane_synthetic.csv")

    def test_good_pacbio_absquantv12_synthetic(self):
        # Exercises sample_volume_ul (one of the four optional v12 metric
        # columns) and the alias map's interaction with extra-column
        # detection.  Real-world v12 files only carry
        # calc_mass_sample_aliquot_input_g; this synthetic verifies that
        # other metric columns also round-trip cleanly.
        self._assert_roundtrips("good_pacbio_absquantv12_synthetic.csv")


if __name__ == "__main__":
    unittest.main()
