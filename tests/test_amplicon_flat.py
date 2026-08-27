"""Round-trip + typed-projection tests for the flat amplicon prep template.

The round-trip is byte-exact: parse the real EMP 16S sheet into a DB, reconstruct
it, and compare bytes. The typed-projection tests confirm the API-facing tables
(amplicon_sample, katharoseq_sample, input_sample.sample_type) are populated
alongside the verbatim store. Tests query tables directly, which production
consumers must not — that's fine here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from run_preflight.legacy.api import load_legacy_csv, open_file, save_legacy_csv
from run_preflight.legacy.flat import (
    load_flat_amplicon,
    looks_like_flat_amplicon,
    parse_flat_tsv,
    save_flat_amplicon,
)

_LEGACY = Path(__file__).parent / "data" / "legacy"
_SHEET = _LEGACY / "good_amplicon_16s_v1.txt"
# every amplicon prep-template fixture (varied layouts: 26-43 columns)
_FIXTURES = sorted(_LEGACY.glob("good_amplicon_*"))


@pytest.mark.parametrize("sheet", _FIXTURES, ids=lambda p: p.name)
def test_flat_amplicon_roundtrip_is_byte_exact(tmp_path, sheet):
    conn = load_flat_amplicon(str(sheet))
    try:
        out = tmp_path / "out.txt"
        save_flat_amplicon(conn, str(out))
    finally:
        conn.close()
    assert out.read_bytes() == sheet.read_bytes()


def test_first_class_entrypoints_roundtrip(tmp_path):
    """The flat format is reachable through the same public API as the omnibus
    formats: open_file / load_legacy_csv load it, save_legacy_csv writes it,
    byte-exact."""
    for loader in (open_file, load_legacy_csv):
        conn = loader(str(_SHEET))
        try:
            out = tmp_path / "out.txt"
            save_legacy_csv(conn, str(out))
        finally:
            conn.close()
        assert out.read_bytes() == _SHEET.read_bytes()


def test_ragged_row_fails_loud(tmp_path):
    """A malformed row (wrong column count) is rejected at parse, not silently
    mispopulated."""
    lines = _SHEET.read_text().split("\n")
    lines[1] = lines[1] + "\textra"  # first data row: 42 cols
    bad = tmp_path / "bad.txt"
    bad.write_text("\n".join(lines))
    with pytest.raises(ValueError, match="columns, expected 41"):
        parse_flat_tsv(str(bad))


def test_looks_like_flat_amplicon(tmp_path):
    assert looks_like_flat_amplicon(str(_SHEET)) is True
    other = tmp_path / "omnibus.csv"
    other.write_text("[Header]\nSheetType,standard_metag\n")
    assert looks_like_flat_amplicon(str(other)) is False


def test_typed_projection_populated(tmp_path):
    conn = load_flat_amplicon(str(_SHEET))
    try:
        cur = conn.cursor()
        # every row has an amplicon_sample with a 12-nt Golay barcode
        n_amplicon = cur.execute("SELECT count(*) FROM amplicon_sample").fetchone()[0]
        assert n_amplicon == 317
        assert cur.execute(
            "SELECT count(*) FROM amplicon_sample WHERE length(barcode) = 12"
        ).fetchone()[0] == 317
        # TubeCode is typed onto input_sample.matrix_tube_id (every sample)
        assert cur.execute(
            "SELECT count(*) FROM input_sample WHERE matrix_tube_id IS NOT NULL"
        ).fetchone()[0] == 317
        # vol_extracted_elution_ul is typed per-plate onto input_plate.elution_vol
        assert cur.execute(
            "SELECT count(DISTINCT elution_vol) FROM input_plate"
        ).fetchone()[0] >= 1
        # sample_type inference: blanks + katharoseq + standard
        types = dict(
            cur.execute(
                "SELECT st.name, count(*) FROM input_sample i "
                "JOIN sample_type st ON i.sample_type_idx = st.sample_type_idx "
                "GROUP BY st.name"
            ).fetchall()
        )
        assert types == {
            "standard": 225,
            "extraction_blank": 44,
            "katharoseq_cells_positive_control": 48,
        }
        # KatharoSeq is recognized via sample_type (above) and kept verbatim; it
        # is NOT redundantly written to katharoseq_sample by the flat ingest.
        assert cur.execute("SELECT count(*) FROM katharoseq_sample").fetchone()[0] == 0
    finally:
        conn.close()


def test_barcode_roster_reader(tmp_path):
    """get_amplicon_barcode_roster yields a per-sample roster WITHOUT requiring
    accessions (the demux-facing reader): 317 entries, 12-nt barcodes,
    barcodes_are_rc=True (EMP 515f), sample_type carried, biosample_accession
    None until assigned."""
    from run_preflight import get_amplicon_barcode_roster

    conn = load_flat_amplicon(str(_SHEET))
    try:
        roster = get_amplicon_barcode_roster(conn)
    finally:
        conn.close()
    assert len(roster) == 317
    assert all(len(e.barcode) == 12 for e in roster)
    assert all(e.barcodes_are_rc is True for e in roster)
    assert all(e.biosample_accession is None for e in roster)
    assert {e.sample_type for e in roster} == {
        "standard", "extraction_blank", "katharoseq_cells_positive_control"
    }
    blank = next(e for e in roster if e.sample_name.startswith("BLANK."))
    assert blank.sample_type == "extraction_blank"


def test_no_double_storage(tmp_path):
    """A typed column is stored ONLY in its typed home, never also verbatim; an
    untyped column is stored ONLY verbatim."""
    conn = load_flat_amplicon(str(_SHEET))
    try:
        cur = conn.cursor()
        # 'barcode' is typed (amplicon_sample) -> NOT in the verbatim store
        assert cur.execute(
            "SELECT count(*) FROM legacy_extra_column WHERE column_name='barcode'"
        ).fetchone()[0] == 0
        # 'sample_plate' is typed (input_plate.plate_name) -> NOT verbatim
        assert cur.execute(
            "SELECT count(*) FROM legacy_extra_column WHERE column_name='sample_plate'"
        ).fetchone()[0] == 0
        # 'primer' is untyped -> stored verbatim, once per prepped_sample
        assert cur.execute(
            "SELECT count(*) FROM legacy_extra_column WHERE column_name='primer'"
        ).fetchone()[0] == 317
    finally:
        conn.close()
