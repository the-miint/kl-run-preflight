# Changelog

All notable changes to run_preflight are documented in this file. The
authoritative record of *how* each change was made is the git history; this
file summarizes *what* changed and *why* at a level useful to consumers of the
package.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project has not yet cut a versioned release: the SQLite schema is still
stabilizing and the legacy omnibus CSV format remains the canonical interchange
format during migration. All changes therefore live under **[Unreleased]**
until the first release is tagged.

## [Unreleased]

### Added

- **EMP amplicon prep-template support.** Adds the EMP amplicon prep template — a
  flat TAB-delimited sheet (no `[Header]`/`SheetType`), a different style from the
  sectioned omnibus sheets, whose column set/order varies between studies.
  `legacy/flat.py` is **header-driven**: it accepts whatever columns a sheet has,
  types the ones it recognises into their schema homes, keeps the rest verbatim in
  the existing `legacy_extra_column`, and reconstructs in the sheet's own column
  order (persisted on `processing_run.flat_column_order`, patch `004`) —
  **byte-exact**, reachable through the same public entrypoints as the other
  formats (`open_file`/`load_legacy_csv`/`save_legacy_csv`), registered as
  `amplicon` v1 (patch `002`). Recognised columns: the Golay barcode → the new
  `amplicon_sample` table (one per prepped_sample), `vol_extracted_elution_ul` →
  `input_plate.elution_vol`, `sample_name`/`well`/`sample_plate`/`project_name`/
  etc. → their existing tables, and blank/KatharoSeq → `input_sample.sample_type`
  from the `BLANK.`/`KATHARO.` name prefix. Nothing is stored twice. A
  `get_amplicon_barcode_roster` reader returns the per-sample `(sample_name,
  biosample_accession, barcode, barcodes_are_rc, sample_type)` a demux consumer
  needs (not accession-gated). Round-trip fixtures cover six real layouts (26–43
  columns) from kl-metapool. See `docs/amplicon-prep-template-schema-gaps.md` for
  the column→schema mapping and open normalization questions.
- **`input_sample.matrix_tube_id`** (nullable) — the physical matrix/tube barcode,
  moved off `katharoseq_sample.tube_code` since it is a per-sample fact, not
  KatharoSeq-specific (schema patch `003`).
- Nullable `smrt_cell_well_sample_id` column on `pacbio_sample` recording the SMRT Cell
  position, constrained to `<1|2>_<A-D>01` (`GLOB '[12]_[A-D]01'`), plus a nullable
  `movie_context_id` column, both surfaced by a new `run_pacbio_sample` view mirroring
  `run_illumina_sample`. Shipped as the
  first schema patch (`sql/patches/001_*`); `schema_v0.sql` is now the frozen
  baseline for databases already in the wild, so every schema change flows
  through a patch from here on.
- `get_pacbio_sample_info`, returning per-`pacbio_sample` biosample and
  bioproject accession info keyed by `pacbio_sample_idx` (control/secondary and
  do-not-use handling identical to `get_illumina_sample_info`). Both info
  functions return a `PlatformSampleInfo` NamedTuple per sample, carrying the
  sample's `sample_type` (the DB `sample_type.name`, e.g. `standard` /
  `extraction_blank`), its biosample and bioproject accessions, and the
  platform-specific columns as a `PacbioSampleRow` / `IlluminaSampleRow`
  `kind_row` — so a consumer gets the accession info, the sample type, and the
  run-specific sample fields in one call. The `PacbioSampleRow.syndna_is_twisted`
  column, a SQLite `BOOLEAN` stored as `0`/`1`/`NULL`, is surfaced to consumers
  as `bool | None`. **Breaking:** `get_illumina_sample_info`'s return changes
  from a bare tuple to a `PlatformSampleInfo` NamedTuple, so existing code
  unpacking it positionally must be updated.
- `set_pacbio_sample_run_details`, setting the post-creation PacBio
  `smrt_cell_well_sample_id` and/or `movie_context_id` on one `pacbio_sample`,
  addressed by `sample_name` or `pacbio_sample_idx` (a `sample_name` matching
  more than one row raises, directing the caller to `pacbio_sample_idx`).
  Exposes a public `UNCHANGED` sentinel so a field can be left untouched,
  distinct from `None` which clears it; invalid `smrt_cell_well_sample_id`
  values are rejected by the column CHECK.

- Do-not-use flags on `input_sample` (two-state hard floor) and
  `prepped_sample` (two-state per-replicate override: set = exclude this
  replicate, NULL = inherit the input flag), populated at legacy ingest by
  detecting a `.donotuse.` dot-delimited token (case-insensitive) in sample
  names. Settable for native runs via `set_input_sample_do_not_use` (by index
  or biosample accession, the latter flagging all matches in one transaction;
  `value=False` clears the flag) and `set_prepped_sample_do_not_use`
  (`value=True` flags, `value=None` clears to inherit; `False` is rejected).
  Sample fetchers (`get_illumina_sample_rows`, `get_illumina_sample_info`,
  `get_input_sample_project_info`) and the forward writers
  (`save_bclconvert_v1_csv`, `save_legacy_sample_id_map_csv`) exclude flagged
  samples by default and accept `include_do_not_use=True` to return them.
  `save_legacy_csv` and `save_db_file` always include flagged records.
- Standard Python project scaffolding: a root `.gitignore` and an installable
  `pyproject.toml` (setuptools + versioningit, generated `_version.py`,
  `environment.yml`, and a GitHub Actions CI workflow).
- Lossless round-trip support for the PacBio Metag v10 omnibus format, with the
  v11 format refactored to layer on top of the new v10 base view.
- Lossless round-trip support for the standard_metag v0 and v90 formats via
  layered SQL views (v90 base → v0 renames → v101 column additions) and shared
  Illumina header/reads views; `parse_omnibus` now takes section formats
  supplied by the DB through `get_section_formats`.
- Lossless round-trip support for the abs_quant_metag v11, standard_metat v10,
  tellseq_metag v10, and tellseq_absquant v10 formats, each reusing shared views
  where possible plus a format-specific data view and population helper.
- Support for arbitrary extra columns in legacy Data sections via a
  `legacy_extra_column` table, with alphabetical reconstruction; a
  `compression_sample` table normalizing well semantics between `input_sample`
  and `prepped_sample`.
- Database migration infrastructure (`migrate.py`): `PRAGMA user_version`
  stamping, patch discovery, SQL/Python patch dispatch, and an `open_db` entry
  point used by the round-trip helpers.
- Derived per-capability views: leaf views (`run_capability_absquant_mass` /
  `_volume` / `_surface_area`) unioned into a `run_capability` view, with a
  `run_derived_capability` view exposing `(run_idx, capability_family, version)`
  tuples. Derivation reads non-null sample metrics directly, so controls and
  failed samples with legitimately NULL metrics are handled correctly.
- Multi-lane support through per-platform surrogate primary keys
  (`illumina_sample_id` / `tellseq_sample_id` / `pacbio_sample_id`),
  `UNIQUE(prepped_sample_id, COALESCE(lane,-1))` indexes, per-tube consistency
  triggers (i5/i7, barcode, lane uniformity, one-run-per-DB), and a synthetic
  multi-lane round-trip fixture.
- Committed native-format test files under `tests/data/native/`: for every
  good_ legacy CSV, a SQLite database plus a JSON snapshot of its full
  structure and contents, produced by `scripts/generate_native_test_files.py`.
  The `.sqlite` files give downstream consumers ready-to-use native
  run-preflight inputs; `tests/test_native_test_files.py` enforces that every
  good_ legacy CSV has a native pair, that the native directory stays paired
  (`.sqlite` ↔ snapshot), and that each committed `.sqlite` matches both its
  snapshot and a fresh load of its source CSV.
- Content-derived stage signalling for native fixtures: each committed
  `.sqlite` carries a fact-based filename suffix — bare for a true preflight,
  `.accessioned` once NCBI accessions are populated — derived from its contents
  and guarded against drift, so a consumer can pick a fixture matching the
  stage their code needs. Includes an accessioned PacBio fixture that reads
  cleanly through `get_pacbio_sample_info` (a true-preflight fixture raises,
  by design, until its accessions are set).

### Changed

- Reorganized test data into `tests/data/legacy/` (legacy omnibus CSVs) and
  `tests/data/native/` (native SQLite files and snapshots); renamed four
  real-world-named good CSVs to the `good_` convention and the
  reject-by-design pre-v101-replicates CSV to an `unsupported_` prefix so it
  is excluded from the good_ sweep.
- Collapsed `get_illumina_sample_info` and `get_pacbio_sample_info` onto one
  parameterized helper keyed by a `PlatformSpecificSampleKind` (`illumina` /
  `pacbio` / `tellseq`), deriving each kind's table, primary-key column, and
  run view by naming convention rather than a hand-maintained lookup.
- Renamed `update_lane`'s `platform` parameter to `sample_kind` and its
  internal lane-target lookup to the Illumina-platform sample kinds
  (`illumina`, `tellseq`), correcting the prior labelling of TellSeq (a library
  prep, not a platform) as a platform. **Breaking:** callers passing
  `platform=` by keyword must switch to `sample_kind=`.
- Restructured the repository into a `src/run_preflight/` package layout, with
  the SQL schema living inside the package.
- Switched the test runner from `unittest` to `pytest`.
- Consolidated view introspection into a single `introspect_view` /
  `get_view_columns` pair in `db.py`, making the reconstruction writers pure
  formatters with no DB access.
- Centralized boolean-string parsing into `_parse_bool_str` (nullable-aware for
  `syndna_is_twisted`) and routed `assay_type` / `sequencing_platform` lookups
  through `_lookup_id`.
- Added `run_id` to the shared `omnibus_contact` and `omnibus_sample_context`
  views so `_query_view` filters uniformly on `run_id`, removing the prior
  substring-based view dispatch.
- Made the `Lane` column required for all Illumina formats.
- Unified the three per-version Illumina Settings views into a single
  `omnibus_illumina_settings` view exposing `ReverseComplement`,
  `MaskShortReads`, and `OverrideCycles` for all Illumina formats.
- Reset the schema-zero baseline so `schema_v0.sql` matches `schema.sql`, and
  relaxed `illumina_run.reverse_complement` to nullable so an absent value
  round-trips without emitting a default.
- Relaxed `input_sample.sample_name` to nullable, adding a table-level
  `CHECK (sample_name IS NOT NULL OR biosample_accession IS NOT NULL)`.
- Renamed the `project.qiita_id` DB column to `external_project_id`, preserving
  the `QiitaID` / `primary_qiita_study` / `secondary_qiita_studies` CSV emit
  aliases and carrying the change to existing DBs via a rename patch.

### Fixed

- Schema patch files under `sql/patches/` are now included in the built
  package, so migrations apply from an installed wheel rather than only from an
  editable checkout.
- Schema patches now apply atomically: each patch body and its `user_version`
  stamp run in one transaction, so a patch failing part-way rolls back instead
  of stranding a half-migrated database that re-fails on every later open.
- The database snapshot used by the drift and native-file guards now captures
  each table's normalized definition, so CHECK, COLLATE, and table-level
  constraints are compared rather than silently ignored.
- Reconstruction now emits tabular Data rows in a deterministic lane-major
  order (by `Lane`, then insertion order), matching the metapool writer's
  layout. Previously multi-lane sheets round-tripped with samples grouped
  and their lanes adjacent, which differed from the source row order.
- Narrowed `cursor.lastrowid` handling at INSERT sites to eliminate Pyright
  `reportArgumentType` warnings.

[Unreleased]: https://github.com/the-miint/kl-run-preflight/commits/main
