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
where v0/v101 use `well_id_384`. These are NOT aliases for the same data:

- `Sample_Well` = ALWAYS `cs.compression_well` (per-row final position)
- `destination_well_384` = ALWAYS `cs.compression_well` (same, different name)
- `well_id_384` = ALWAYS `cp.placement_well` (original placement position)

For non-replicates all three are equal. For replicates, `well_id_384`
(placement) is shared across replicates while `Sample_Well` /
`destination_well_384` (compression_well) varies per replicate. The v0 view
therefore cannot simply rename `Sample_Well` → `well_id_384`; it must source
`well_id_384` from `compression_placement.placement_well` via a join.

**Well columns in CSV files represent compression plate positions.** Input
plates are 96-well plates; multiple input plates are consolidated onto one
compression plate (384-well for Illumina, 96-well for PacBio). `Sample_Well`,
`well_id_384`, and `destination_well_384` all refer to positions on the
compression plate, not the source input plate. `input_sample.well` is for the
96-well input plate position (not currently available in CSVs).

**Replicate well semantics (v101 only; pre-v101 replicates are rejected):**
In v101 replicate files, `well_id_384` is the shared original compression
plate position (same across all replicates of a sample), while
`destination_well_384` is the per-replicate final position on the compression
plate.

**Proposed schema change — `compression_placement` table:**
Insert a new `compression_placement` entity between `input_sample` and
`compression_sample`. This table represents "this input sample was placed at
position X on the compression plate for this run":

```sql
CREATE TABLE compression_placement (
    placement_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES sequencing_run(run_id),
    input_sample_id INTEGER NOT NULL REFERENCES input_sample(input_sample_id),
    well            TEXT NOT NULL   -- well_id_384 / Sample_Well
);
```

`compression_sample` would then reference `placement_id` instead of
`run_id` + `input_sample_id` directly:

```sql
CREATE TABLE compression_sample (
    compression_sample_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    placement_id            INTEGER NOT NULL
        REFERENCES compression_placement(placement_id),
    compression_well        TEXT NOT NULL,  -- destination_well_384
    sample_name             TEXT,
    well_description        TEXT
);
```

Benefits:

- `well_id_384` is stored once per input sample per run (normalized)
- Replicate detection becomes structural: a placement with multiple
  compression_samples is a replicate — no heuristics needed
- The existing `replicated_samples` view logic (COUNT > 1 per group)
  transfers directly, just grouping by `placement_id` instead of
  `input_sample_id`
- For non-replicates: one placement → one compression_sample,
  `placement.placement_well` = `compression_sample.compression_well`
- For replicates: one placement → multiple compression_samples,
  `placement.placement_well` is shared, each `compression_well` differs

This change is implemented. The `compression_placement` table exists in
`schema.sql`, all views join through it, and `db.py` populates it during
legacy parsing. Pre-v101 files with replicates are rejected because their
well semantics cannot be round-tripped correctly.

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

## Total-sample-input metrics enforcement (ticket 016)

### Problem

Going forward, every absquant sample needs at least one "total sample input"
metric, but there are three different metric types. All absquant samples in a
run use the same metric type(s) — a run requires at least one type but may
require two or all three. Legacy omnibus files do not contain any of these
metrics, and the values cannot be inferred.

The long-term goal is that the SQLite DB (not the omnibus CSV) becomes the
canonical format. Old DBs will be migrated forward via patches, so only the
latest schema needs to be supported in code. The metrics and their enforcement
must be added to the schema so that new DBs cannot be populated with incomplete
data, while legacy loading remains possible.

#### Why custom migration code instead of an existing tool

The forward-migration pattern (versioned patches applied in sequence) is
industry-standard and supported by tools such as Alembic, Django migrations,
Flyway, and yoyo-migrations. This project implements the pattern from scratch
because:

- Most migration tools target long-running server databases and assume an
  external CLI step to apply migrations. This project uses SQLite as a
  **file format** — migration must happen transparently inside the
  application at file-open time, not as a separate invocation.
- The project's "schema as source of truth" principle means the SQL schema
  drives behavior. Tools that want to own migration state (tracking tables,
  config files, ORM model diffing) add machinery that conflicts with this
  principle.
- The total implementation is ~80 lines of Python: scan a directory of
  numbered SQL files, compare against `PRAGMA user_version`, execute in
  order. The logic is simple enough that an external dependency costs more
  in coupling and maintenance than it saves.
- yoyo-migrations is the closest fit (raw SQL, no ORM), but still carries
  connection-management and rollback-tracking infrastructure designed for
  server databases, not embedded file formats.
- If future patches require complex data transformations beyond what SQL
  can express, adopting yoyo-migrations at that point would be
  straightforward since the file-based patch numbering convention is
  compatible.

### Chosen design — flat columns with run-level declaration and gated trigger

- Store the three metrics as **nullable columns directly on
  `metagenomic_absquant_sample`** rather than in a normalized child table.
  Flat columns keep consumer queries simple (no joins or pivots to read
  metric values).
- Add an **`absquant_run_required_metric`** table (or similar) declaring which
  metric types a given absquant run requires — rows are
  `(run_id, metric_name)`. At least one row must exist for absquant runs.
  This table is absquant-specific rather than generic; if other run types
  later need a similar mechanism, a separate table is easy to add.
- Add a **`db_metadata`** key-value table with an `enforce_strict` flag,
  seeded to `'1'` (strict by default). This flag gates enforcement so that
  the DB starts strict and `populate_db` can temporarily relax it during
  legacy loading without altering the schema (no DROP/CREATE TRIGGER).
- Add a **gated BEFORE INSERT trigger** on `metagenomic_absquant_sample`.
  For each of the three metric columns, the trigger joins from
  `NEW.compression_sample_id` back through `compression_sample` →
  `compression_placement` to the run, checks `run_required_metric` to see
  if that metric is declared as required by `absquant_run_required_metric`,
  and raises an error if the value is NULL — but only when
  `enforce_strict = '1'`.
- **`populate_db`** sets `enforce_strict` to `'0'` before legacy loading and
  back to `'1'` afterward. This way `populate_db` remains a pure data writer
  that never modifies the schema structure. The DB is strict for its entire
  life except during the brief legacy-loading window.

### Alternatives considered and rejected

**Normalized child table (`sample_total_input_metric`):** A table with
`(compression_sample_id, metric_type_id, value)` rows is cleaner from a
normalization standpoint, but "every sample has a row for each declared
metric" is a cross-row completeness constraint that cannot be enforced by
a per-row INSERT trigger. Enforcement would require either a deferred
"finalize" step or application-level validation, both of which weaken the
"schema as source of truth" guarantee.

**Finalize flag on `sequencing_run`:** A `is_finalized` column with a
trigger on UPDATE could check completeness at a single checkpoint. However,
this allows invalid data to sit in the DB until finalization, requires every
population path to remember to finalize, and forces consumers to filter on
the flag to avoid reading incomplete runs.

**Application-only validation:** Placing the constraint in Python (e.g., a
check at the end of `populate_db`) means the DB itself permits incomplete
data. Any code path that writes directly to the DB — manual SQL, a future
API, a migration script — could bypass the check. This violates the
project's "schema as source of truth" principle.

**DROP/CREATE TRIGGER toggle in `populate_db`:** Instead of a metadata flag,
`populate_db` could drop the trigger before loading and recreate it after.
This works but means `populate_db` reaches into the schema structure rather
than just writing data. The metadata flag approach keeps `populate_db` as a
pure data writer.

## Already supported (have round-trip tests)

| Class | SheetType / Version | Platform |
|-------|-------------------|----------|
| PacBioAbsquantSampleSheetv11 | pacbio_absquant v11 | PacBio |
| PacBioMetagSampleSheetv11 | pacbio_metag v11 | PacBio |
| PacBioMetagSampleSheetv10 | pacbio_metag v10 | PacBio |
| PacBioAbsquantSampleSheetv10 | pacbio_absquant v10 | PacBio |
| MetagenomicSampleSheetv101 | standard_metag v101 | Illumina |
| MetagenomicSampleSheetv90 | standard_metag v90 | Illumina |
| MetatranscriptomicSampleSheetv0 | standard_metag v0 | Illumina |
| MetagenomicSampleSheetv100 | standard_metag v100 | Illumina |
| AbsQuantSampleSheetv10 | abs_quant_metag v10 | Illumina |
| AbsQuantSampleSheetv11 | abs_quant_metag v11 | Illumina |
| MetatranscriptomicSampleSheetv10 | standard_metat v10 | Illumina |
| TellseqMetagSampleSheetv10 | tellseq_metag v10 | Illumina (TellSeq prep) |
| TellseqAbsquantMetagSampleSheetv10 | tellseq_absquant v10 | Illumina (TellSeq prep) |

## Test files

- v0: `tests/data/good_standard_metagv0_really_metat.csv`
- v90: `tests/data/good_standard_metagv90.csv`
- v100: `tests/data/good_standard_metagv100_wo_replicates.csv`
- abs_quant_metag v10: `tests/data/good_abs_quant_metagv10.csv`
- abs_quant_metag v11: `tests/data/good_abs_quant_metagv11.csv`
- standard_metat v10: `tests/data/good_standard_metatv10.csv`
- tellseq_metag v10: `tests/data/good_tellseq_metagv10.csv`
- tellseq_absquant v10: `tests/data/Tellseq_absquant_samplesheet_spp_novaseqxplus_set_col19to24.csv`
- v100 with extra columns (for ticket 014):
  `fork-kl-metapool/metapool/tests/data/good_standard_metagv100_w_replicates.csv`
  and `fork-kl-metapool/metapool/tests/data/sheet_wo_replicates.csv`

### Group A — Illumina, no replicates, no SampleContext — SUPPORTED

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| MetagenomicSampleSheetv90 | standard_metag v90 | Settings: only ReverseComplement. No replicates, no SampleContext |
| MetatranscriptomicSampleSheetv0 | standard_metag v0 | Settings: ReverseComplement + MaskShortReads. Metatranscriptomic assay. No replicates, no SampleContext |

### Group B — Illumina, replicates, no SampleContext — SUPPORTED

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| MetagenomicSampleSheetv100 | standard_metag v100 | Has replicates but no SampleContext |
| AbsQuantSampleSheetv10 | abs_quant_metag v10 | Replicates + AbsQuant Data columns, no SampleContext |

### Group D — TellSeq — SUPPORTED

| Class | SheetType / Version | Notes |
|-------|-------------------|-------|
| TellseqMetagSampleSheetv10 | tellseq_metag v10 | Barcode_ID instead of index columns; has Reads + Settings; has SampleContext |
| TellseqAbsquantMetagSampleSheetv10 | tellseq_absquant v10 | Same + AbsQuant Data columns |

### Group E — Metatranscriptomic v10 — SUPPORTED

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

**Settings deletion at write time:** The `_SETTINGS` class attribute defines the
maximum set of settings for a format, but `_add_metadata_to_sheet` deletes
settings based on context:

- **Amplicon assay:** unconditionally deletes MaskShortReads and OverrideCycles
- **Sequencer-specific:** reads `delete_settings` from
  `config/sequencer_types.yml`. Only **iSeq** defines this, deleting
  MaskShortReads and OverrideCycles

This means any Illumina format's class may define all three settings, but an
actual file may contain fewer. All v100+ classes inherit all three settings from
`KLSampleSheet._SETTINGS`, yet iSeq-targeted files will only have
ReverseComplement. The reconstruction handles this by skipping NULL DB values
in `_write_header_kv`.

### Optional column groups

- Replicates: orig_name, destination_well_384
- Katharoseq: Kathseq_RackID, TubeCode, katharo_description, number_of_cells,
  platemap_generation_date, project_abbreviation, vol_extracted_elution_ul,
  well_id_96

### Lane column

`Lane` is NOT part of `_data_columns` for any class. It is injected dynamically
at write time by `KLSampleSheet.write()` when a `lane` parameter is passed, and
by `_add_data_to_sheet()` which iterates over a `lanes` list. This means any
format's files may or may not contain a `Lane` column depending on how the file
was generated.

Currently, v100 and abs_quant_metag v10 treat Lane as a required column (present
in both test files). This should be demoted to an optional column as part of
ticket 014 (extra column support), since real-world files exist without Lane
(e.g. `sheet_wo_replicates.csv`, `good_standard_metagv100_w_replicates.csv`).
