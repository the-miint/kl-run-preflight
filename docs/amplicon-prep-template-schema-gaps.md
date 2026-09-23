# Amplicon prep template — what we store, and what's deferred

The EMP amplicon prep template is a flat, tab-delimited sheet (one row per
sample), unlike the sectioned Illumina/PacBio omnibus sheets. It is **free-form**:
studies vary the column set and order. It loads through the same parse → validate
→ populate → reconstruct path as the omnibus sheets.

## Formats and resolution

The sheet carries no `SheetType`, so its layout is resolved from the column header
(`db.get_amplicon_format_for_header`, widest typed-column match). Three layouts
are registered, spanning the real variety seen so far:

| Format | 384-well | Distinguishing columns |
|---|---|---|
| `amplicon` v1 | `well_id_384` | tube + plate tier + `control_description` |
| `amplicon` v2 | `well_id` | narrow — no tube/plate-detail tier |
| `amplicon` v3 | `well_id_384` | wide **without** `control_description` |

`control_description` is derived from `sample_type`, so its presence can't be a
data-based optional — it distinguishes v1 from v3. `Kathseq_RackID` /
`number_of_cells` are an optional group on all three. A new layout that no
registered format matches raises at load, naming its header — register another
format (a small, expected treadmill).

Recognised columns are typed into their schema homes (see the `amplicon_v{1,2,3}_data`
views in `sql/schema.sql` for the authoritative column→home mapping); every other
column round-trips **verbatim** via `legacy_extra_column`. Round-trip is
**data-exact** — same column set and per-row values; a flat sheet's reconstruction
emits a canonical column order and does not preserve the source's order.

## Deferred

- **More amplicon assays.** `barcodes_are_rc` is stored on `amplicon_run`,
  inferred from the primer at ingest (`db._barcodes_are_rc_for_primer`), but only
  the EMP 515f assay is recognised — an unrecognised primer raises. Extend that
  mapping (and the format registry) when another amplicon assay is supported.

## Qiita handoff (barcode roster)

Qiita's golay-demux needs a per-sample `(prep_sample_idx, barcode,
barcodes_are_rc)` roster. Qiita mints `prep_sample.idx` from a `biosample_idx`, so
the bridge is the **biosample accession**, not a shared idx: preflight
`sample_name` / `input_sample.biosample_accession` → Qiita biosample →
`prep_sample.idx`. Flow: register the study's biosamples in Qiita → write
accessions back into the preflight DB (`set_biosample_accession`) → at submit,
read `get_amplicon_sample_info` (its `AmpliconSampleRow` carries `barcode` and
`barcodes_are_rc`) and join on `biosample_accession`.
