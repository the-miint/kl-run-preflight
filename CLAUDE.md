# run_preflight

## Project Overview

Encapsulate the "run preflight" concept (the information package handed off from
the wet lab to the dry lab for sequencing data processing) so that all consumers
access it through this project. Internally, represent run preflights in a
normalized SQLite schema. The current phase supports silent replacement of
existing legacy CSV sample sheet objects by providing round-tripping of all
versions of the legacy "omnibus" CSV files (parse → SQLite → reconstruct → diff)
so that existing legacy CSV sample sheet class structures in other code can be
swapped for SQL run preflights without affecting their interaction with external
producers and consumers. Once all domain consumers use this project, omnibus CSVs
will be sunset in favor of SQLite run preflights as the canonical format,
enabling stronger correctness constraints and easier data management.

## Architecture

The SQLite schema (`src/run_preflight/sql/schema.sql`) is the permanent core of
the project; the `legacy/` parser, reconstructor, and formatter are
transitional bridging code for the omnibus CSV format during migration. The
schema drives behavior: a data-driven legacy format registry describes each
format's sections, views, and optional columns, so adding a format is mostly
schema rows and views rather than new Python.

`schema_v0.sql` is the frozen baseline and is never edited; every schema change
is a numbered patch under `sql/patches/` plus an edit to `schema.sql`, and
`tests/test_schema_drift.py` enforces that v0 plus all patches equals
`schema.sql`.

See `docs/architecture.md` for the domain model, the platform-vs-library-prep
distinction, key domain rules, the native and legacy load / write / round-trip
workflows, the frozen-baseline rationale, and the design reasoning behind the
schema.

## Project Structure

Build config is in `pyproject.toml`. Source is in `src/run_preflight/`.
Tests are in `tests/`. SQL schema is in `src/run_preflight/sql/`. Dev-only
scripts are in `scripts/`; design notes are in `docs/`.

| File | Role |
|------|------|
| `src/run_preflight/__init__.py` | Defines the consumer API surface via `__all__`; exposes single-call convenience entry points only, never pipeline steps |
| `src/run_preflight/sql/schema.sql` | Provides full DDL: reference tables, legacy format registry, core domain tables, platform-specific tables, reconstruction views |
| `src/run_preflight/constants.py` | Holds all string-literal constants (section names, column names, platform strings) |
| `src/run_preflight/db.py` | Creates SQLite DB from schema.sql, populates tables from parsed data |
| `src/run_preflight/file_io.py` | Reads and writes native SQLite files and blobs, writes bcl-convert v1 sample sheets, and provides `atomic_write`, the staged-write primitive every write path in the project uses |
| `src/run_preflight/migrate.py` | Applies numbered schema patches and reports schema version |
| `src/run_preflight/updates.py` | Applies post-fill updates to a filled preflight, logging each change to `change_log` |
| `src/run_preflight/sql/patches/` | Holds the numbered schema patches applied on load |
| `src/run_preflight/sql/schema_v0.sql` | Frozen v0 baseline; never edited, since live v0 databases exist |
| `src/run_preflight/legacy/__init__.py` | Defines `LegacyExtraColumnWarning`, raised when unrecognized `[Data]` columns are carried through verbatim |
| `src/run_preflight/legacy/api.py` | Provides consumer-facing wrappers (load_file, load_legacy_csv, load_legacy_csv_text, save_legacy_csv, save_legacy_sample_id_map_csv, migrate_legacy_csv_to_db_file) over the load and write pipelines |
| `src/run_preflight/legacy/parser.py` | Parses omnibus CSV into dict of sections (header_kv, values_only, tabular); `read_omnibus_text` is the one place deciding how a CSV file on disk becomes text |
| `src/run_preflight/legacy/validate.py` | Validates parsed sections against the view registry |
| `src/run_preflight/legacy/reconstruct.py` | Rebuilds omnibus CSV from SQL views via the legacy format registry |
| `src/run_preflight/legacy/formatting.py` | Defines shared formatting (boolean columns, bcl_scrub_name) |
| `src/run_preflight/legacy/roundtrip.py` | Packages load + write + normalize as test/dev helpers for byte-comparing reconstructed output against the original |

## API Naming

Entry points are named by direction; the medium goes in the suffix, never
in the verb.

- `load_*` reads a run preflight in, whatever the source: `load_file`
  (format-detecting), `load_db_file`, `load_db_bytes`, `load_legacy_csv`,
  `load_legacy_csv_text`
- `save_*` writes to a path: `save_db_file`, `save_legacy_csv`,
  `save_bclconvert_v1_csv`, `save_legacy_sample_id_map_csv`
- `dump_*` serializes to bytes without touching the filesystem:
  `dump_db_bytes`

Do not introduce a fourth verb for a direction that already has one.
`open_file` survives only as a deprecated alias for `load_file`.

## Changelog

When a unit of work is completed, add an entry to the `[Unreleased]` section of
`CHANGELOG.md` (root) under the appropriate heading (`Added`, `Changed`,
`Deprecated`, `Fixed`, `Removed`) before considering the work done. The file
follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Testing

- Framework: **pytest**
- Run tests: `pytest`
- Round-trip tests (load real CSV → DB → write CSV → byte-compare to the
  original) cover the legacy formats end to end, but are a small share of the
  suite; most tests are per-module unit tests
- `tests/test_schema_drift.py` asserts `schema_v0.sql` plus all patches builds
  the same database as `schema.sql`
- Test data: real legacy CSV sample sheets in `tests/data/legacy/`; committed
  native SQLite files and their JSON snapshots in `tests/data/native/`
- The `roundtrip_via_api` helper in `legacy/roundtrip.py` runs load + write + normalize against a per-test temp dir
- Native fixtures are regenerated by `scripts/generate_native_test_files.py` (run manually) and guarded by `tests/test_native_test_files.py`

## Imports

Tests import from the installed package (e.g.,
`from run_preflight.db import create_db`). Internal imports within
`src/run_preflight/` use relative imports
(e.g., `from .constants import ...`).

## Adding a New Legacy Format

1. Add format row to `legacy_samplesheet_format` in `sql/schema.sql`
2. Add `legacy_samplesheet_view` rows mapping sections to views
3. Create SQL views for each section (can reuse shared views like
   `omnibus_contact`, `omnibus_sample_context`)
4. Add optional column groups if needed
5. Update `db.py` population logic if the new format has new columns
6. Add round-trip test with a real sample CSV
