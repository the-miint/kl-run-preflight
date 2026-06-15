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

- Do-not-use flags on `input_sample` (hard floor) and `prepped_sample`
  (per-replicate override), populated at legacy ingest by detecting a
  `.donotuse.` dot-delimited token (case-insensitive) in sample names, and
  settable for native runs via `set_input_sample_do_not_use` (by index or
  biosample accession, the latter flagging all matches) and
  `set_prepped_sample_do_not_use`. Sample fetchers
  (`get_illumina_sample_rows`, `get_illumina_sample_info`,
  `get_input_sample_project_info`) exclude flagged samples by default and
  accept `include_do_not_use=True` to return them.
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

### Changed

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

- Narrowed `cursor.lastrowid` handling at INSERT sites to eliminate Pyright
  `reportArgumentType` warnings.

[Unreleased]: https://github.com/the-miint/kl-run-preflight/commits/main
