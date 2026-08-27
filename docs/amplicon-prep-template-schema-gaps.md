# Amplicon prep template — what we store, and what's deferred

The EMP amplicon prep template is a flat, tab-delimited sheet (one row per
sample), unlike the sectioned Illumina/PacBio omnibus sheets. Its column set and
order vary between studies; the fixtures in `tests/data/legacy/good_amplicon_*`
span six real layouts (26–43 columns).

The parser is **header-driven**: it accepts whatever columns a sheet has, types
the ones it recognises into their schema homes, and keeps the rest **verbatim** in
`legacy_extra_column`. Nothing is stored twice. The sheet's column order is
persisted on `processing_run.flat_column_order`, so it reconstructs byte-exactly.
Consumers use the package API, never the tables directly, so this internal split
is private and can be restructured later without data loss.

## Typed today

| column | home |
|---|---|
| `sample_name` | `input_sample.sample_name` |
| `barcode` | `amplicon_sample.barcode` (new table) |
| `well_id_96` | `input_sample.well` |
| `well_id_384` | `compression_sample.compression_well` |
| `well_description` | `prepped_sample.well_description` |
| `TubeCode` | `input_sample.matrix_tube_id` (new, nullable) |
| `sample_plate` | `input_plate.plate_name` |
| `vol_extracted_elution_ul` | `input_plate.elution_vol` |
| `project_name` | `project.project_name` |

Blank / KatharoSeq controls are typed as `input_sample.sample_type`
(`extraction_blank` / `katharoseq_cells_positive_control`), inferred from the
`BLANK.` / `KATHARO.` `sample_name` prefix.

Everything else is kept verbatim. A few run-constant columns
(`library_construction_protocol`, `instrument_model`, `platform`) are also copied
into the `NOT NULL` columns of `project` / `processing_run` to satisfy those
constraints, but the verbatim copy is the source of truth on reconstruction.

## Deferred (pick up later)

- **KatharoSeq detail → `katharoseq_sample`.** The KatharoSeq sheets carry
  `number_of_cells` and `Kathseq_RackID` as real columns; today they're kept
  verbatim. Type them onto `katharoseq_sample` (its intended home).
- **Assay definition → typed columns.** `target_gene` / `target_subfragment`
  (e.g. `16S rRNA` / `V4`) are finer than `assay_type = Amplicon` and identify the
  barcode set. Worth typing.
- **`barcodes_are_rc` → stored.** Currently *derived* at read time in
  `get_amplicon_barcode_roster` from the 515F primer (`515F` ⇒ EMP 515rcbc set);
  not stored. A typed assay definition (above) would give it a home.
- **Plate-level columns without a home.** `run_date` and
  `experiment_design_description` vary per plate here, but their schema homes
  (`processing_run.run_date`, `project.experiment_design_description`) are
  per-run/per-project; `extractionkit_lot`, `extraction_robot`, `primer_plate`
  have no typed home at all. Typing these cleanly needs per-plate homes.
- **Run-constant metadata deduplication.** The verbatim store repeats run-constant
  columns on every row. Fine for now; a run-level home would de-duplicate them.

## Qiita handoff (barcode roster)

Qiita's golay-demux needs a per-sample `(prep_sample_idx, barcode,
barcodes_are_rc)` roster. Qiita mints `prep_sample.idx` from a `biosample_idx`, so
the bridge is the **biosample accession**, not a shared idx: preflight
`sample_name` / `input_sample.biosample_accession` → Qiita biosample →
`prep_sample.idx`. Flow: register the study's biosamples in Qiita → write
accessions back into the preflight DB (`set_biosample_accession`) → at submit,
read `get_amplicon_barcode_roster` and join on `biosample_accession`.
