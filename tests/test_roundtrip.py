"""Round-trip tests: parse CSV → populate DB → reconstruct CSV → compare."""

from __future__ import annotations

import unittest
from pathlib import Path

from sequencing_brief.legacy.roundtrip import roundtrip

DATA_DIR = Path(__file__).parent / "data"


class TestRoundTrip(unittest.TestCase):
    def test_good_pacbio_absquantv11(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_pacbio_absquantv11.csv"),
            "good_pacbio_absquantv11",
        )
        self.assertEqual(original, reconstructed)

    def test_pacbio_v11_absquant_unpooled(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "pacbio_v11_absquant_unpooled_sample_sheet.csv"),
            "pacbio_absquant_v11_unpooled",
        )
        self.assertEqual(original, reconstructed)

    def test_skin_replicates_novaseq(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "Test1_Skin_replicates_15459_novaseq.csv"),
            "standard_metag_v101_replicates_novaseq",
        )
        self.assertEqual(original, reconstructed)

    def test_celeste_adaptation_novaseq(self):
        original, reconstructed = roundtrip(
            str(
                DATA_DIR
                / "YYYY_MM_DD_Celeste_Adaptation_12986_16_17_18_21_matrix_samplesheet_novaseq.csv"
            ),
            "standard_metag_v101_novaseq",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_metagv11(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_pacbio_metagv11.csv"),
            "pacbio_metag_v11",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_metagv10(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_pacbio_metagv10.csv"),
            "pacbio_metag_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_pacbio_absquantv10(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_pacbio_absquantv10.csv"),
            "pacbio_absquant_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv90(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_standard_metagv90.csv"),
            "standard_metag_v90",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv0_really_metat(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_standard_metagv0_really_metat.csv"),
            "standard_metag_v0_really_metat",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metagv100_wo_replicates(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_standard_metagv100_wo_replicates.csv"),
            "standard_metag_v100_wo_replicates",
        )
        self.assertEqual(original, reconstructed)

    def test_good_abs_quant_metagv10(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_abs_quant_metagv10.csv"),
            "abs_quant_metag_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_abs_quant_metagv11(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_abs_quant_metagv11.csv"),
            "abs_quant_metag_v11",
        )
        self.assertEqual(original, reconstructed)

    def test_good_standard_metatv10(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_standard_metatv10.csv"),
            "standard_metat_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_tellseq_metagv10(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_tellseq_metagv10.csv"),
            "tellseq_metag_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_good_tellseq_absquantv10(self):
        original, reconstructed = roundtrip(
            str(
                DATA_DIR
                / "Tellseq_absquant_samplesheet_spp_novaseqxplus_set_col19to24.csv"
            ),
            "tellseq_absquant_v10",
        )
        self.assertEqual(original, reconstructed)

    def test_standard_metagv100_w_replicates_rejected(self):
        with self.assertRaises(ValueError, msg="v101 or later"):
            roundtrip(
                str(DATA_DIR / "good_standard_metagv100_w_replicates.csv"),
                "standard_metag_v100_w_replicates_rejected",
            )

    def test_good_multilane_synthetic(self):
        original, reconstructed = roundtrip(
            str(DATA_DIR / "good_multilane_synthetic.csv"),
            "good_multilane_synthetic",
        )
        self.assertEqual(original, reconstructed)


if __name__ == "__main__":
    unittest.main()
