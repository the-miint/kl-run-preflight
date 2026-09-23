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

- **Amplicon prep templates load through the shared pipeline.** A flat,
  section-less prep template is now parsed by `parse_amplicon_prep`, which
  regroups its single table into the same sections a sectioned sheet parses
  into, then validated and populated by the same code. Its format is resolved
  from the column header alone — these sheets state no SheetType — by matching
  each registered layout's typed columns against it and taking the widest
  match; an unrecognised layout fails to load naming its header, rather than
  being silently absorbed. `legacy/flat.py`, which reimplemented parsing,
  population, and reconstruction alongside the omnibus path, is deleted, and
  no `INSERT` statement remains anywhere under `legacy/`.

- **A format declares whether its replicate well semantics round-trip**
  (`replicates_supported`, schema patch `002`). The loader previously rejected
  replicates by comparing a bare version number against 101, which is only
  meaningful inside one format family — amplicon v1 sorts below it while being
  unrelated to `standard_metag` v0/v90/v100. Only those three declare 0,
  asserted exactly by a guard test.

- **A format declares which Data column holds each fact the loader needs**
  (schema patch `002`): `sample_name_column`, `plate_column`, `project_column`,
  `well_description_column`, and `well_column` on `legacy_samplesheet_format`.
  The defaults are the omnibus vocabulary that all fourteen omnibus formats
  share, so only the amplicon format overrides them; the well column genuinely
  varies across the omnibus formats and is stated on every row. A guard test
  fails, naming the format and column, if a declared name is absent from that
  format's own Data view.

- **The legacy format registry declares how each format loads** (schema patch
  `002`). `legacy_samplesheet_format` gains `platform_idx`,
  `default_instrument_type`, and `sample_kind` — the platform a file of that
  format describes, the instrument to record when the file states none, and
  which `<kind>_sample` table holds its per-sample rows. `sample_kind` is NULL
  for the amplicon format, which has no platform-specific sample rows: an
  amplicon run carries a single in-line Golay barcode, not an i5/i7 pair. A
  guard test fails, naming the format, if any row is incomplete or names a
  kind with no table.

- **The amplicon prep template is free-form; three layouts are registered.** The
  sheet carries no `SheetType` and studies vary its column set and order, so the
  layout is resolved from the column header alone (widest typed-column match).
  Three `amplicon` formats span the real-world variety (schema patch `002`): v1
  wide (`well_id_384`, tube/plate tier, `control_description`), v2 narrow
  (`well_id`), and v3 wide without `control_description`. `Kathseq_RackID` /
  `number_of_cells` are an optional column group on all three; any unrecognized
  column round-trips verbatim via `legacy_extra_column`, and a header no format
  matches raises at load, naming its columns. Round-trip is **data-exact** — the
  same column set and per-row values; a flat sheet reconstructs in a canonical
  column order (not the source's), and its fields are written raw
  (`_RawRowWriter`) because flat fields carry literal quotes/commas that CSV
  quoting would corrupt.
- **The amplicon prep template has committed native fixtures.** The
  native-fixture guards previously covered only `good_*.csv`; they now cover
  every `good_*` legacy sheet, so each real amplicon layout carries a committed
  `.sqlite` and snapshot subject to the same coverage, pairing, consistency,
  correctness, and labelling invariants as the omnibus sheets.

- **`amplicon_run` holds the amplicon facts that are constant across a run**
  (schema patch `002`): `primer`, `linker`, `target_gene`, `target_subfragment`,
  `pcr_primers`, `sequencing_meth`, and `barcodes_are_rc`, all `NOT NULL`. It is a
  workflow table rather than a platform one — the same facts would describe an
  amplicon run on PacBio. The prep template does not record barcode orientation,
  so `barcodes_are_rc` is inferred from the primer at ingest (fail-loud on an
  unrecognised primer) and stored once per run, mirroring
  `illumina_run.barcodes_are_rc` rather than being re-derived by every reader.
- **`input_plate` carries the plate-tier prep facts** `primer_plate`, `plating`,
  `extractionkit_lot`, `extraction_robot`, `platemap_generation_date`, and
  `plate_contents_description`, all nullable. The last is deliberately not named
  `experiment_design_description`: plates and projects are many-to-many, so a
  plate is a crossing axis rather than a finer grain, and the project-level
  column stays authoritative.
- **The legacy format registry records each format's file shape.**
  `legacy_samplesheet_format.delimiter` and `has_section_labels` let a format
  describe its own serialization instead of the reader inferring it. Both are
  `NOT NULL` with defaults matching the omnibus formats, which are
  comma-delimited and section-labelled.
- **Guard test for the section-format registry.** Section formats are folded
  into one `{section_name: section_format}` mapping before any format is known,
  so registering one section name under two formats would resolve by row order
  and change how unrelated files parse. A test now fails, naming the offending
  section, if that ever happens.

- **`amplicon_sample` modelled as a workflow table.** The in-line Golay barcode
  is added to a sample during amplicon PCR and is platform-independent — an
  amplicon-on-PacBio sample would carry the same one — so `amplicon_sample` now
  sits with `metagenomic_absquant_sample` and `metatranscriptomic_sample` rather
  than with the platform tables, keyed on `prepped_sample_idx` with no surrogate.
  `amplicon` is correspondingly no longer a `PlatformSpecificSampleKind`, which
  is the platform/library-prep axis.

- **`get_amplicon_sample_info`**, the accession-gated per-sample reader for an
  amplicon run — the single reader a golay-demux consumer needs. Returns one
  `PlatformSampleInfo` per `amplicon_sample` (keyed by `prepped_sample_idx`,
  since amplicon_sample has no surrogate key), carrying the biosample and
  bioproject accessions, `sample_type`, and an `AmpliconSampleRow` with the Golay
  `barcode` and `barcodes_are_rc`; like the platform readers it raises if a
  required accession is NULL. It reuses the shared accession-resolution helper via
  a new per-row `source_names()` classmethod, so amplicon shares that one
  implementation without being pulled into `PlatformSpecificSampleKind` (it has no
  i5/i7 platform row). Adds the `run_amplicon_sample` view (schema patch `002`).
- `load_db_file` and `load_db_bytes`, which read a native run preflight into a
  detached in-memory connection: pending schema patches are applied to the copy,
  so the source file or blob is never written. Paired with `dump_db_bytes`,
  which serializes a connection to database-file bytes, they let a consumer hold
  a run preflight as an opaque blob and decide separately whether to keep an
  edit. Both reject input lacking the SQLite file header with a `ValueError`
  naming the offending input, where a raw deserialize would report only a bare
  `sqlite3.DatabaseError: file is not a database` (or, for empty input, a
  `MemoryError`). Input that carries the header but is truncated or otherwise
  unreadable gets past that check and raises `sqlite3.DatabaseError`, which
  both entry points document.
- `load_file`, which loads a run preflight from either supported format by
  detecting the file's type, replacing `open_file`. A native file is sniffed and
  read through a single handle, so the bytes loaded are the ones the header
  check saw.
- `load_legacy_csv_text`, which parses legacy sample sheet content already
  decoded to text — either shape, sectioned or flat — so a consumer holding the
  content in memory need not write a temporary file. It is the text counterpart to `load_db_bytes`, and it takes
  `str` rather than `bytes` because the caller owns any decision about how its
  bytes became text.
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

### Removed

- **`good_amplicon_replicate.txt` fixture.** It was synthetic but
  unmarked, used US-style slash dates where every real sheet uses ISO, and
  contained no replicates at all — eight rows with eight distinct `orig_name`
  values — while asserting `contains_replicates` in four spellings.

### Changed

- **Prep-template facts land in typed homes rather than a verbatim store.**
  The prep template previously typed only nine columns, keeping the rest in
  `legacy_extra_column`; run-constant facts now live on `amplicon_run`,
  plate-constant facts on `input_plate`, the 96-well position on
  `input_sample.well`, the tube barcode on `input_sample.matrix_tube_id`, and
  the Golay barcode on `amplicon_sample`, leaving only the genuinely free-form
  columns verbatim.

- **KatharoSeq and blank controls are typed from their name prefix,
  case-insensitively.** A `KATHARO.` / `BLANK.` prefix types the control
  whatever its capitalisation; when the sheet also carries `Kathseq_RackID` and
  `number_of_cells`, those land on `katharoseq_sample`. Matching is otherwise
  literal: a name such as `BLANK2.2A` is deliberately not treated as a blank,
  because whether a numbered prefix marks one is a question about that sheet's
  convention rather than something the loader should infer.

- **A sheet whose `control_description` disagrees with its sample names is
  rejected at load.** The Data view regenerates that column from the sample
  type, so a disagreeing source value would otherwise be silently rewritten.

- **Round-trip normalization is delimiter-aware, and its whole-number rule is
  scoped to a cell.** Applied to the whole text it rewrote sample names that
  embed their own values, turning `katharo.ADAPT.21.E11.18000.0` into
  `…18000`.

- **`validate_omnibus` is now `validate_sections`**, and
  `processing_run.source_column_order` is gone — column order is recovered
  from the format's view, so no sheet needs its own header persisted.

- **Reconstruction views may carry a reserved `prepped_sample_idx`.** View
  introspection already hid `run_idx` from the output; it now hides any reserved
  column, and a view with no printable key of its own can carry the row's
  identity for ordering and for matching carried-through columns. Previously
  that identity came from `Sample_ID`, which every omnibus view happens to
  define as the primary key — a coincidence no format without a `Sample_ID`
  column could rely on.

- **A `[Reads]`-less source no longer fails to load.** `illumina_run`'s read
  lengths became nullable so a document recording no run configuration could
  still carry a row, but the loader still indexed the section unconditionally.
  Absent read lengths are now stored as NULL.

- **`prepped_sample.sample_name` is populated only when it differs** from the
  input sample's name, as the column's contract states. It was written on every
  row of any sheet carrying an `orig_name` column.

- **The loader reads Data columns by the names the format declares, and no
  longer sniffs the well column out of the file.** `populate_db` previously
  hardcoded `Sample_Name`, `Sample_Plate`, `Sample_Project`, and
  `Well_description`, and chose the well column by testing whether
  `well_id_384` happened to be present. Both assumed every format speaks the
  omnibus vocabulary. The seeded names reproduce exactly what was hardcoded and
  sniffed, verified by every committed native snapshot regenerating unchanged
  apart from its schema version.

- **The loader reads a file's platform, instrument, and per-sample table from
  the registry instead of inferring them from its name.** `populate_db`
  previously substring-matched `SheetType` for `"pacbio"` and `"tellseq"` to
  decide the platform, the instrument, which `_populate_*_sample` to call, and
  whether to write an `illumina_run` row — so a naming coincidence was
  load-bearing, and a future format containing either token would silently take
  that path. Dispatch now goes through a `sample_kind`-keyed map of the same
  functions, which are themselves unchanged. The seeded values reproduce what
  the inference produced, verified by every committed native snapshot
  regenerating identically apart from its schema version.

- **`katharoseq_sample.number_of_cells` is `REAL`, not `INTEGER`** (schema patch
  `002`). KatharoSeq serial dilutions reach fractional cell counts — `38.4` and
  `7.68` both occur in real prep templates — which `INTEGER` silently truncated.

- **Every Illumina run now has an `illumina_run` record.** A run loaded from the
  amplicon prep template is sequenced on Illumina but the prep template
  records no run configuration, so it previously produced a database with
  `platform = Illumina` and no `illumina_run` row — the first break in an
  invariant that held across every other preflight. The amplicon loader now
  inserts an `illumina_run` row whose configuration columns are all NULL,
  meaning "this ran on Illumina; this document does not state the run config".
  A guard test loads every legacy sheet — sectioned and flat — and fails if any
  run's platform and its run-config table disagree.
- **`illumina_run.read1_length` / `read2_length` are nullable** (schema patch
  `002`), so a run whose source document omits read lengths can still carry an
  `illumina_run` row. NULL expresses "not recorded", which `0` cannot. The patch
  rebuilds the table, since SQLite cannot drop `NOT NULL` in place, dropping and
  recreating the four dependent views around the rebuild.
- **Native fixture snapshots record `user_version`.** The snapshots exist to make
  the opaque `.sqlite` diffs reviewable, but omitted the schema version — the one
  piece of structure held outside `sqlite_master`. A database stale in version
  alone compared equal, and the version bump behind a fixture regeneration was
  invisible in review. `capture_db_snapshot` now captures it.
- **`input_sample.matrix_tube_id`** (nullable) — the physical matrix/tube barcode,
  moved off `katharoseq_sample.tube_code` since it is a per-sample fact, not
  KatharoSeq-specific (schema patch `002`).
- **Breaking:** `open_db_file` is removed. It connected directly to the caller's
  file and committed schema patches into it, so merely reading a stored preflight
  rewrote it — silently today, because patch `001` is the only patch and a
  current file needs no work, and universally the day patch `002` ships.
  Replacing it with `load_db_file` makes every load path detached and leaves
  `save_db_file`, which keeps its name, as the one call that reaches disk. The
  schema upgrade is no longer sticky: a file behind the patch set stays behind
  until someone saves it, which is the point of the change rather than a side
  effect.
- **Breaking:** loading a file of either format now returns a detached in-memory
  connection. Previously a legacy CSV yielded a detached connection while a
  SQLite file yielded a file-backed one whose edits persisted without any save,
  so a caller handling both formats could not write one correct save path.
- Loading a native file reads its raw bytes instead of connecting to it, which
  skips SQLite's crash recovery. A hot journal left by a writer that died
  mid-transaction is no longer replayed, so the database loads in its
  un-rolled-back state: against a 50,000-row table SIGKILLed mid-update, the
  load reports 49,707 rows carrying the uncommitted change where a
  `sqlite3.connect` would have rolled all of them back and reported none. The
  image is torn rather than merely stale, so a file left behind by a crashed
  writer needs checking before it is trusted.
- `save_db_file` writes serialized bytes instead of calling `Connection.backup`.
  `backup` retries indefinitely when the source connection holds an uncommitted
  write transaction, which hangs the caller outright — and does so
  uninterruptibly, since it blocks in C holding the GIL — measured on CPython
  3.14, where a committed source returns in under a millisecond and an
  uncommitted one survives SIGINT and needs SIGKILL. A plain byte write has no
  such failure mode.
- Every file this package writes — `save_db_file`, `save_legacy_csv`,
  `save_bclconvert_v1_csv`, and `save_legacy_sample_id_map_csv` — now goes
  through `atomic_write`, which stages the content in a temporary file in the
  target's own directory and renames it into place. A write that fails partway
  leaves the caller's existing file untouched instead of truncated, so the
  no-clobber posture that governs the load paths now covers the write paths too.
- Staging and renaming changes what a write does to a symlink or a hardlink.
  A plain write followed the link and updated its target, and updated every
  name pointing at a hardlinked file; the rename replaces the link itself with
  a regular file, leaving the old target untouched, and breaks the hardlink so
  the other names keep the previous content. Anywhere a stable pointer such as
  `latest.csv` is kept, the pointer is now the file that gets replaced.
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
  a bare `table ... already exists`. The path is now refused up front, by name,
  and the check tests the path itself rather than what it resolves to, so a
  symlink is refused whether or not it currently points at anything.
- **Breaking:** the minimum supported Python is now 3.11, up from 3.9. The
  detached-load implementation is built on `sqlite3.Connection.serialize` and
  `.deserialize`, which are 3.11+. `environment.yml` now declares the floor
  too, so a local `conda env create` resolves an interpreter the package can
  actually run on rather than whatever conda picks.
- Written files carry the permissions an ordinary write would have given them:
  an existing file keeps its own mode, and a new one gets the default creation
  mode narrowed by the process umask. Staging would otherwise have decided the
  result, since `mkstemp` creates its file at `0600`, so the mode a plain write
  would have produced is resolved and applied before the rename.
- The lint rule set is declared explicitly in `pyproject.toml` as
  `select = ["E4", "E7", "E9", "F"]` rather than inherited, because ruff's
  implicit default selection changes between releases, which would otherwise
  keep changing what CI enforces without any change to this project.
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

### Deprecated

- `open_file` is now a thin alias for `load_file` and emits a
  `DeprecationWarning`. Every read entry point is named `load_*`, and `open`
  implied a handle on the caller's file that no load path has returned since
  the detached-load change. The alias delegates, so behaviour is identical.

### Fixed

- CI lint enforces a stated ruff rule set (`E4`, `E7`, `E9`, `F`) rather than
  inheriting ruff's implicit default, which shifts between ruff releases and so
  silently changed what CI required from one run to the next.
- The CI Python matrix exercises the versions it names. The workflow's
  interpreter pin rewrote only a literal `python=3.9` entry, which the unpinned
  `python` in `environment.yml` did not match, so the substitution was a no-op
  and every matrix job installed the same interpreter. The rewrite now matches a
  pinned or unpinned entry and fails loudly when it does not take.
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
