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

- `Sample_Well` = ALWAYS `prs.prepped_well` (per-row final position)
- `destination_well_384` = ALWAYS `prs.prepped_well` (same, different name)
- `well_id_384` = ALWAYS `cs.compression_well` (original placement position)

For non-replicates all three are equal. For replicates, `well_id_384`
(compression_well) is shared across replicates while `Sample_Well` /
`destination_well_384` (prepped_well) varies per replicate. The v0 view
therefore cannot simply rename `Sample_Well` → `well_id_384`; it must source
`well_id_384` from `compression_sample.compression_well` via a join.

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

**Proposed schema change — `compression_sample` table:**
Insert a new `compression_sample` entity between `input_sample` and
`prepped_sample`. This table represents "this input sample was placed at
position X on the compression plate for this run":

```sql
CREATE TABLE compression_sample (
    compression_sample_idx    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_idx          INTEGER NOT NULL REFERENCES sequencing_run(run_idx),
    input_sample_idx INTEGER NOT NULL REFERENCES input_sample(input_sample_idx),
    well            TEXT NOT NULL   -- well_id_384 / Sample_Well
);
```

`prepped_sample` would then reference `compression_sample_idx` instead of
`run_idx` + `input_sample_idx` directly:

```sql
CREATE TABLE prepped_sample (
    prepped_sample_idx   INTEGER PRIMARY KEY AUTOINCREMENT,
    compression_sample_idx            INTEGER NOT NULL
        REFERENCES compression_sample(compression_sample_idx),
    prepped_well        TEXT NOT NULL,  -- destination_well_384
    sample_name             TEXT,
    well_description        TEXT
);
```

Benefits:

- `well_id_384` is stored once per input sample per run (normalized)
- Replicate detection becomes structural: a compression_sample with multiple
  prepped_samples is a replicate — no heuristics needed
- The existing `replicated_samples` view logic (COUNT > 1 per group)
  transfers directly, just grouping by `compression_sample_idx` instead of
  `input_sample_idx`
- For non-replicates: one compression_sample → one prepped_sample,
  `compression_sample.compression_well` = `prepped_sample.prepped_well`
- For replicates: one compression_sample → multiple prepped_samples,
  `compression_sample.compression_well` is shared, each `prepped_well` differs

This change is implemented. The `compression_sample` table exists in
`schema.sql`, all views join through it, and `db.py` populates it during
legacy parsing. Pre-v101 files with replicates are rejected because their
well semantics cannot be round-tripped correctly.

## Multi-lane model

### Schema structure

A sequencing_brief SQLite file represents one sequencing run.  A trigger
on `sequencing_run` enforces this; the rest of the multi-lane model
relies on it.  Within that one run, lane splits are modeled as:

- One `prepped_sample` per `(plate, orig_name, dest_well)` triple
  (the physical well on the compression plate).
- N rows in the platform-specific table (`illumina_sample` or
  `tellseq_sample`) — one per (prepped_sample × lane).  Each row
  carries its own surrogate primary key
  (`illumina_sample_idx` / `tellseq_sample_idx`) which serves as the
  stable, reproducible identifier for that row in the legacy
  sample-sheet Data section.
- `pacbio_sample` is unique by `prepped_sample_idx` because PacBio
  has no lane concept today.

### Lane splits vs replicates

- **Lane split**: same `(plate, orig_name, dest_well)` across CSV rows;
  only `Lane` differs.  Produces one `prepped_sample` and N
  `illumina_sample` (or `tellseq_sample`) rows.
- **Replicate**: same `(plate, orig_name)` but different `dest_well`
  per row.  Produces N separate `prepped_sample` rows, each with
  its own per-tube data (per-replicate variation in absquant /
  metatranscriptomic / extra columns is preserved).  Detected
  structurally by the `replicated_samples` view.

### Per-sample invariants

The i7/i5 index pair is per-`prepped_sample`: lane splits of one
sample reuse the same indexes across all loadings.  The
`illumina_sample_index_invariance` BEFORE INSERT trigger enforces that
all `illumina_sample` rows sharing a `prepped_sample_idx` carry
identical `i7_index_id`, `i7_sequence`, `i5_index_id`, and
`i5_sequence` values.  TellSeq has the analogous
`tellseq_sample_barcode_invariance` trigger on `barcode_id`.

Lane uniformity within a database is enforced by
`illumina_sample_lane_uniformity` and `tellseq_sample_lane_uniformity`:
all rows must have `lane` uniformly NULL (CSV with no Lane column) or
uniformly non-NULL (CSV with Lane on every row).  The triggers fire on
INSERT only; UPDATE bulk-transitions (e.g. setting NULL lanes to a
known value once it is known) are not blocked.

The triggers and invariants are intentionally minimal because the
project is migrating away from Illumina; further normalization would
add complication for technology that is on the way out.

### Path not taken: prepped_library normalization

A more denormalized model — separate `prepped_library` and
`prepped_library_run` entities, with lane moved to a per-(pool × run)
table such as `illumina_prepped_library_run` — was considered and
rejected.  Two reasons:

1. The added complication did not justify itself for technology the
   project is moving away from.  Illumina-specific normalization
   machinery would have to be designed, populated, queried, and tested
   only to be removed when the legacy Illumina path is sunset.
2. It did not satisfy the requirement that every row in a sample
   sheet's data table have a stable, reproducible identifier.
   Collapsing N lane-split CSV rows into one `prepped_sample` plus
   N junction rows means the per-row identifier would have to come
   from the junction table, fragmenting the row-ID concept across
   platform-specific child tables.  The chosen design instead keeps
   one row per CSV row in the platform-specific table and uses that
   table's surrogate PK as the per-row identifier.

### Legacy population rules

- One `prepped_sample` per `(plate, orig_name, dest_well)` triple.
  CSV rows identical except for `Lane` collapse to one
  `prepped_sample` with N platform-table rows.
- Lane-split rows must agree on every column except `Lane`.  Disagreement
  on any other column (per-tube absquant values, extra columns, indexes)
  is rejected at populate time with `ValueError`.
- Replicates (same `(plate, orig_name)`, different `dest_well`) keep
  separate `prepped_sample` and per-tube rows; per-replicate
  variation in per-tube columns is preserved.

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

## Data completeness enforcement — per-capability triggers

### Problem

New columns will be added to the schema over time that represent genuinely
new information not present in older briefs. Each such column is required
for a specific downstream capability (e.g. absquant qualification) but must
remain nullable so that legacy briefs — which never collected the data — can
still be loaded.

A single global `enforce_strict` flag does not scale: a version-one brief
loaded into a version-two schema should be held to version-one constraints
but exempted from version-two constraints. A global toggle cannot express
"enforce A and B but not C."

### Why enforcement must live in the database

The SQLite brief file is the artifact that travels — it may be emailed,
stored in an external database, or processed by tools other than the Python
code in this project. Any enforcement that exists only in Python is invisible
to those external consumers and can be bypassed by any code path that writes
to the database directly. Constraints must be intrinsic to the file so that
invalid data is rejected regardless of which tool performs the write.

### Chosen design — per-capability derived views

Capabilities are derived from actual sample data rather than declared up
front.  Each capability has its own leaf view that checks whether any
sample in the run has a non-null value for the corresponding metric
column.  A union view (`run_capability`) aggregates all leaf views so
consumers can ask "what can this run do?" with a single query.

Example structure:

```sql
-- Leaf view: "can this run do absquant_mass?"
CREATE VIEW run_capability_absquant_mass AS
SELECT DISTINCT cs.run_idx
FROM metagenomic_absquant_sample ma
JOIN prepped_sample prs ON ma.prepped_sample_idx = prs.prepped_sample_idx
JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
WHERE ma.extracted_sample_mass_g IS NOT NULL;

-- Union view: "what can this run do?"
CREATE VIEW run_capability AS
SELECT run_idx, 'absquant_mass' AS capability_name
    FROM run_capability_absquant_mass
UNION ALL
SELECT run_idx, 'absquant_volume'
    FROM run_capability_absquant_volume
UNION ALL
SELECT run_idx, 'absquant_surface_area'
    FROM run_capability_absquant_surface_area;
```

Key properties:

- **Derived, not stored:** No `run_capability` table or `capability`
  reference table exists.  Capabilities are computed at query time from
  the sample data, so they can never be out of sync.
- **Sparse-data tolerant:** Controls (blanks with no DNA) and failed
  samples naturally have NULL metric values.  The view only requires at
  least one non-null value per run, so sparse data is handled correctly.
- **Additive:** Adding a new capability means adding one leaf view and
  one UNION ALL clause to the union view.
- **No enforcement triggers:** Insert-time enforcement via BEFORE INSERT
  triggers was removed because metric columns are legitimately NULL for
  controls and failed samples, making per-row non-null constraints too
  strict.

### Why custom migration code instead of an existing tool

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

### Alternatives considered and rejected

**Global `enforce_strict` flag with a single gated trigger:** A `db_metadata`
table with an `enforce_strict` boolean, checked by a single trigger containing
all constraint checks. Does not scale: cannot enforce version-one constraints
while exempting version-two constraints. The flag is either on or off for
everything.

**Application-only validation (post-load Python check):** Placing constraints
in Python (e.g., a validation pass at the end of `populate_db`) means the
database itself permits invalid data. Any tool that writes to the SQLite file
without going through `populate_db` bypasses enforcement entirely. Since the
brief file travels independently of the Python code, this leaves it
unprotected.

**Normalized child table (`sample_total_input_metric`):** A table with
`(prepped_sample_idx, metric_type_id, value)` rows is cleaner from a
normalization standpoint, but "every sample has a row for each declared
metric" is a cross-row completeness constraint that cannot be enforced by
a per-row INSERT trigger.

**Finalize flag on `sequencing_run`:** An `is_finalized` column with a
trigger on UPDATE could check completeness at a single checkpoint. However,
this allows invalid data to sit in the DB until finalization, requires every
population path to remember to finalize, and forces consumers to filter on
the flag to avoid reading incomplete runs.

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

After ticket 015, Lane is a required column for all Illumina formats. The
`contains_lane` optional-column rows were removed from every Illumina entry in
the format registry, and the `CHECK_CONTAINS_LANE` check function was removed
from the parser.

## Schema version vs data completeness

### The distinction

Schema version and data completeness are two independent axes. Migrating an
old database forward to the latest schema (adding columns, tables, and views)
is always safe and mechanical — new columns receive NULL values. The database
then has a place for the data, but the data itself does not exist. Schema
migration makes old databases **structurally identical** to new ones but does
not make them **informationally equivalent**, because the new fields were never
collected.

Concretely:

- **Schema version** determines which tables, columns, views, and constraints
  exist. Controlled by `PRAGMA user_version` and the patch sequence in
  `sql/patches/`. The goal of "one schema version in the codebase" is feasible
  and eliminates branching in access patterns.
- **Data completeness** determines which analyses a given brief can support.
  A brief migrated from an older format may have NULLs in columns that a
  current consumer requires. No amount of schema migration can fill in
  information that was never recorded.

### Design decision — two-tier derived capability model

Capabilities are split into two tiers, both derived from sample data:

- **Column-level capabilities** (per-capability views + `run_capability`
  union view): One view per metric (e.g. `run_capability_absquant_mass`),
  each returning `(run_idx)` for runs that have at least one sample with a
  non-null value in the corresponding column.  The union view
  `run_capability` aggregates all leaf views into
  `(run_idx, capability_name)` rows.
- **Consumer-level capabilities** (derived via `run_derived_capability`
  view): Higher-level flags that downstream analysis code checks before
  proceeding (e.g. `absquant_v1`).  Defined as SQL expressions over the
  `run_capability` union view.  Not stored as rows — computed at query
  time.

Key properties:

- **Fully derived:** No stored capability rows exist.  Both tiers are
  views over the actual sample data, so capabilities can never be out of
  sync with the data.
- **Sparse-data tolerant:** Controls and failed samples have NULL metric
  values.  The views require only one non-null value per run to detect a
  capability, so sparse data is handled correctly.
- **Consumer-driven:** The set of consumer-level capabilities is driven by
  what downstream analysis requires, not by the brief's original format
  version.  A consumer asks "does this brief have capability X?" rather
  than "is this brief new enough?"
- **Additive:** Adding a new column-level capability means adding one leaf
  view and one UNION ALL clause.  Adding a new consumer-level capability
  means adding a UNION clause to `run_derived_capability`.
- **Schema as source of truth:** The definition of each capability lives
  in SQL, not in Python.

The `run_derived_capability` view uses a `(capability_family, version)`
structure rather than a flat capability name.  This supports queries like
"what is the highest version of absquant that this brief supports?" without
string parsing:

```sql
SELECT MAX(version) FROM run_derived_capability
WHERE run_idx = ? AND capability_family = 'absquant'
```

Each version within a family is a separate UNION clause.  A brief that
satisfies version N also has rows for all versions below N, because higher
versions are defined as supersets of lower versions' requirements.  This
means `MAX(version)` always gives the highest supported version.

Consumer queries:

- "Can this run do absquant mass?"

  `SELECT 1 FROM run_capability_absquant_mass WHERE run_idx = ?`
- "What can this run do?"

  `SELECT capability_name FROM run_capability WHERE run_idx = ?`
- "Does this brief support absquant v1 or higher?"

  `SELECT 1 FROM run_derived_capability WHERE run_idx = ? AND capability_family = 'absquant' AND version >= 1`
- "What is the highest absquant version?"

  `SELECT MAX(version) FROM run_derived_capability WHERE run_idx = ? AND capability_family = 'absquant'`

## Numeric measurement precision — deferred

Numeric measurement columns (concentrations, masses, volumes, surface
areas) are stored as SQLite REAL. REAL is a binary float, so the textual
form written by reconstruction does not always equal the source literal:
trailing zeros after the decimal point are lost (e.g. a source value of
`0.110` round-trips to `0.11`).

In scientific data, trailing zeros after a decimal point are significant
figures and carry information about measurement precision. The current
REAL storage discards that information silently.

This is recorded as a known limitation to be revisited with a domain
expert before legacy-CSV sunset. Candidate resolutions include:

- storing affected columns as TEXT to preserve the source literal exactly
- attaching a per-column precision or significant-figures attribute
- accepting the precision loss and documenting it as a brief-format
  limitation

The right choice depends on what downstream consumers actually require
from these values, which has not yet been determined.

Affected columns (currently REAL):

- `input_plate.elution_vol`
- `metagenomic_absquant_sample.syndna_pool_mass_ng`
- `metagenomic_absquant_sample.extracted_gdna_concentration`
- `metagenomic_absquant_sample.sequenced_sample_gdna_mass_ng`
- `metagenomic_absquant_sample.extracted_sample_mass_g`
- `metagenomic_absquant_sample.extracted_sample_volume_ul`
- `metagenomic_absquant_sample.extracted_sample_surface_area_cm2`
- `metatranscriptomic_sample.total_rna_concentration_ng_ul`
