# Legacy Format Coverage Plan

## Source of truth for class details

`/Users/amandabirmingham/Work/Repositories/fork-kl-metapool/metapool/sample_sheet.py`

## Excluded classes

- Abstract bases (can't be directly instantiated): `KLSampleSheet`,
  `KLSampleSheetWithReplicates`, `KLSampleSheetWithSampleContext`,
  `KLTellSeqSampleSheet`, `PacBioSampleSheet`,
  `PacBioSampleSheetWithTwistAdapters`
- `AmpliconSampleSheet` — excluded per user instruction
- `MetagenomicSampleSheetv102` — excluded per user instruction

## Column ordering rule

`_ORDERED_BY_DATA_COLUMNS` in sample_sheet.py controls whether Data section
column order is enforced. Only `KLSampleSheetWithSampleContext` (v101+) and
`MetatranscriptomicSampleSheetv10` set this to True. ALL older formats (v90,
v100, v0, AbsQuantv10) have `_ORDERED_BY_DATA_COLUMNS = False`, meaning column
order is arbitrary/incidental.

**Consequence for column ORDER:** We do NOT need to match the original column
order for older formats. Column order differences (v90 Lane-first, v90 reversed
Contact columns) are incidental, not structural. We output in our canonical
order and normalize the input CSV to match.

**Column NAME differences handled by view layering:** v90 uses `Sample_Well`
where v101 uses `well_id_384` — same data (`ins.well`), different alias. The
v90 base view uses `Sample_Well`; v101 layers on top and renames it to
`well_id_384` in its SELECT. This is trivial SQL aliasing — no separate views
or format-dependent normalization needed.

**View layering pattern (same as PacBio v10→v11):** v90 is the base Data view;
v0 can share it (same columns); v101 layers on top adding `orig_name`,
`destination_well_384`, and renaming `Sample_Well` → `well_id_384`.
Similarly for Bioinformatics: v90/v0 base (no `contains_replicates`); v101
layers on top adding `contains_replicates`.

**`_normalize_csv` changes needed for older formats:**

- Reorder columns in tabular sections to canonical view output order
- Boolean case normalization (already exists)
- Whole-number float normalization (already exists)
- Keep normalization format-agnostic — do NOT branch on format type

## Already supported (have round-trip tests)

| Class | SheetType / Version | Platform |
|-------|-------------------|----------|
| PacBioAbsquantSampleSheetv11 | pacbio_absquant v11 | PacBio |
| PacBioMetagSampleSheetv11 | pacbio_metag v11 | PacBio |
| PacBioMetagSampleSheetv10 | pacbio_metag v10 | PacBio |
| PacBioAbsquantSampleSheetv10 | pacbio_absquant v10 | PacBio |
| MetagenomicSampleSheetv101 | standard_metag v101 | Illumina |

## Test files

- v0: `tests/data/good_standard_metagv0_really_metat.csv` (already in repo)
- v90: copy from `/Users/amandabirmingham/Work/Repositories/fork-kl-metapool/metapool/tests/data/good_standard_metagv90.csv`

## Remaining to support (10 classes in 5 groups)

### Group A — Illumina, no replicates, no SampleContext

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| MetagenomicSampleSheetv90 | standard_metag v90 | Settings: only ReverseComplement. No replicates, no SampleContext |
| MetatranscriptomicSampleSheetv0 | standard_metag v0 | Settings: ReverseComplement + MaskShortReads. Metatranscriptomic assay. No replicates, no SampleContext |

### Group B — Illumina, replicates, no SampleContext

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| MetagenomicSampleSheetv100 | standard_metag v100 | Has replicates but no SampleContext |
| AbsQuantSampleSheetv10 | abs_quant_metag v10 | Replicates + AbsQuant Data columns, no SampleContext |

### Group C — Illumina, replicates, SampleContext

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| AbsQuantSampleSheetv11 | abs_quant_metag v11 | Same as v101 + AbsQuant Data columns |

### Group D — TellSeq

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| TellseqMetagSampleSheetv10 | tellseq_metag v10 | Barcode_ID instead of index columns; has Reads + Settings; has SampleContext |
| TellseqAbsquantMetagSampleSheetv10 | tellseq_absquant v10 | Same + AbsQuant Data columns |

### Group E — Metatranscriptomic v10

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| MetatranscriptomicSampleSheetv10 | standard_metat v10 | Extra Data columns (total_rna_concentration_ng_ul, vol_extracted_elution_ul). _ORDERED_BY_DATA_COLUMNS = True. No SampleContext |

## Key dimensions of variation across all formats

### Sections present

- All Illumina: Header, Reads, Settings, Data, Bioinformatics, Contact
  - Some add SampleContext (v101, AbsQuantv11)
- PacBio: Header, Data, Bioinformatics, Contact, SampleContext (no Reads/Settings)
- TellSeq: Header, Reads, Settings, Data, Bioinformatics, Contact, SampleContext

### Bioinformatics columns

- Base: Sample_Project, QiitaID, BarcodesAreRC, ForwardAdapter, ReverseAdapter,
  HumanFiltering, library_construction_protocol, experiment_design_description
- With replicate support: base + contains_replicates
- PacBio: Sample_Project, QiitaID, HumanFiltering,
  library_construction_protocol, experiment_design_description,
  contains_replicates (NO BarcodesAreRC, ForwardAdapter, ReverseAdapter)

### Data columns (canonical order)

- Illumina base: Sample_ID, Sample_Name, Sample_Plate, well_id_384,
  I7_Index_ID, index, I5_Index_ID, index2, Sample_Project, Well_description,
  Lane
- Illumina with replicates adds (optional): orig_name, destination_well_384
  (before Lane)
- TellSeq: Sample_ID, Sample_Name, Sample_Plate, well_id_384, Barcode_ID,
  Sample_Project, Well_description
- PacBio v10: Sample_ID, Sample_Name, Sample_Plate, Sample_Well, Barcode_ID,
  Sample_Project, Well_description
- PacBio v11: PacBio v10 + TwistAdaptorId
- AbsQuant adds: mass_syndna_input_ng, extracted_gdna_concentration_ng_ul,
  vol_extracted_elution_ul, syndna_pool_number
- TwistAbsquant adds: syndna_is_twisted
- MetatranscriptomicSampleSheetv10: base + total_rna_concentration_ng_ul,
  vol_extracted_elution_ul

### Settings variations

- v90: ReverseComplement only
- v0: ReverseComplement, MaskShortReads
- v101+: ReverseComplement, MaskShortReads, OverrideCycles

### Optional column groups

- Replicates: orig_name, destination_well_384
- Katharoseq: Kathseq_RackID, TubeCode, katharo_description, number_of_cells,
  platemap_generation_date, project_abbreviation, vol_extracted_elution_ul,
  well_id_96
