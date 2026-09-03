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

- `load_db_file` and `load_db_bytes`, which read a native run preflight into a
  detached in-memory connection: pending schema patches are applied to the copy,
  so the source file or blob is never written. Paired with `output_db_bytes`,
  which serializes a connection to database-file bytes, they let a consumer hold
  a run preflight as an opaque blob and decide separately whether to keep an
  edit. Both reject input lacking the SQLite file header with a `ValueError`
  naming the problem, rather than the `MemoryError` a raw deserialize produces;
  input that carries the header but is truncated or otherwise unreadable gets
  past that check and raises `sqlite3.DatabaseError`, which both entry points
  document.
- `SchemaVersionTooNewError`, raised when a database's schema version exceeds the
  shipped patch set. It subclasses `ValueError`, so an existing handler still
  catches it, but a consumer can now tell "this file came from a newer
  run_preflight" apart from a malformed request. The neighbouring
  "patch sequence has missing files" case stays a bare `ValueError`: it reports a
  defect in the installed package, not a property of the caller's input.
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
  `save_legacy_csv` and `output_db_file` always include flagged records.
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

- **Breaking:** `open_db_file` is removed and `save_db_file` is renamed to
  `output_db_file`. `open_db_file` connected directly to the caller's file and
  committed schema patches into it, so merely reading a stored preflight rewrote
  it — silently today, because patch `001` is the only patch and a current file
  needs no work, and universally the day patch `002` ships. Replacing it with
  `load_db_file` makes every load path detached and leaves `output_db_file` as
  the one call that reaches disk. The schema upgrade is no longer sticky: a file
  behind the patch set stays behind until someone saves it, which is the point of
  the change rather than a side effect.
- **Breaking:** `open_file` now returns a detached in-memory connection for both
  input formats. Previously a legacy CSV yielded a detached connection while a
  SQLite file yielded a file-backed one whose edits persisted without any save,
  so a caller handling both formats could not write one correct save path.
- `output_db_file` writes serialized bytes instead of calling `Connection.backup`.
  `backup` retries indefinitely when the source connection holds an uncommitted
  write transaction, which hangs the caller outright — and does so
  uninterruptibly, since it blocks in C holding the GIL. A plain byte write has
  no such failure mode.
- Every file this package writes — `output_db_file`, `save_legacy_csv`,
  `save_bclconvert_v1_csv`, and `save_legacy_sample_id_map_csv` — now goes
  through `atomic_write`, which stages the content in a temporary file in the
  target's own directory and renames it into place. A write that fails partway
  leaves the caller's existing file untouched instead of truncated, so the
  no-clobber posture that governs the load paths now covers the write paths too.
- **Breaking:** `migrate_legacy_csv_to_db_file` no longer deletes `db_path` on
  failure. Its cleanup ran in a `finally` that also covered the CSV load, so a
  validation error destroyed whatever file already sat at `db_path` even though
  nothing had been written there. With the write now atomic, a partial database
  can never appear at that path, so the cleanup had nothing left to clean and the
  data-loss path went with it.
- **Breaking:** `create_db` now raises `FileExistsError` when a file already
  exists at the requested path. Its docstring claimed an existing file "will be
  overwritten by SQLite's default behaviour", which was untrue — `sqlite3.connect`
  opens such a file, and the unguarded schema DDL then failed partway through with
  a bare `table ... already exists`. The path is now refused up front, by name.
- **Breaking:** the minimum supported Python is now 3.11, up from 3.9. The
  detached-load implementation is built on `sqlite3.Connection.serialize` and
  `.deserialize`, which are 3.11+.
- Written files now carry the permissions an ordinary write would have given
  them: an existing file keeps its own mode, and a new one gets the default
  creation mode narrowed by the process umask. `atomic_write` previously forced
  every output to `0644`, which re-permissioned a deliberately restricted
  target on overwrite and widened new files past what the caller's umask asked
  for.
- `load_db_file` checks the SQLite file header off its first read instead of
  after pulling the whole file into memory, so a large non-database input is
  rejected without the needless read. The rejection now names the offending
  path rather than the generic `blob`.
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
