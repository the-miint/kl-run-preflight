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

### Core vs transitional

The SQLite schema (`src/run_preflight/sql/schema.sql`) is the permanent, long-term core of the
project. It defines the normalized domain model: runs, plates, samples,
platform-specific extensions, and reference data.

The parser (`legacy/parser.py`), reconstructor (`legacy/reconstruct.py`), and
formatter (`legacy/formatting.py`) are transitional bridging code that exists to
support the legacy omnibus CSV format during the migration period.

### Schema as source of truth

The DB schema drives behavior, not Python code. The **legacy format registry**
(`legacy_samplesheet_format`, `legacy_samplesheet_view`,
`legacy_samplesheet_optional_columns`) is a data-driven dispatch mechanism:
the DB itself describes which sections a format contains, which SQL view
produces each section, and which columns are optional. Both validation and
reconstruction read this registry at runtime. Adding a new legacy format means
adding rows and views to `schema.sql` — no new Python code paths are needed
unless the format introduces structurally new data.

### Platforms vs library prep protocols

There are only two sequencing platforms: **Illumina** and **PacBio**. These are
the physical instruments that perform sequencing. **TellSeq is a library prep
protocol, not a sequencing platform** — TellSeq-prepared samples are sequenced
on Illumina instruments. A TellSeq run therefore uses `illumina_run` config
(read lengths, override cycles, etc.) because the instrument_type is Illumina. The
`tellseq_sample` table captures the TellSeq-specific per-sample barcode, but
the run-level configuration is Illumina.

### Key domain rules

- Controls have NULL `project_id` on `input_sample`; they inherit project
  association via `input_plate`
- `prepped_sample.sample_name` is NULL when identical to
  `input_sample.sample_name`; populated only for replicates
- `run_id` column in reconstruction views is a filter column excluded from
  CSV output

### Transitional workflows

Consumer-facing entry points are exposed at the package root (see
`__init__.py`); the per-step pipeline below describes the internal
implementation.

1) Read a legacy omnibus file into SQLite format:

    - **Consumer call:** `migrate_legacy_csv_to_sqlite(csv_path, db_path)`
    - Internally: `db.create_db` → `db.get_section_formats` →
      `parser.parse_omnibus` → `validate.validate_omnibus` →
      `db.populate_db` (raises `ValueError` and removes the partial DB
      file if validation fails)

2) Write a legacy omnibus file from SQLite format:

    - **Consumer call:** `write_legacy_csv(db_path, csv_path)`
    - Internally: `migrate.open_db` → look up the single `processing_run`
      (raises `ValueError` if zero or multiple) →
      `reconstruct.reconstruct_omnibus` → write text to file

3) Round-trip a legacy omnibus file through SQLite format (used only for testing)

    - run workflow 1
    - run workflow 2
    - normalize the input csv file (AFTER workflow 1 usage) to produce a known-good:
        - replace FALSE/TRUE with False/True
        - strip trailing .0 from whole-number floats (e.g. 1.0 → 1)
        - reorder columns in tabular sections to match reconstruction order
        - ensure a trailing newline
    - directly compare the raw text of the normalized known-good omnibus file to the output omnibus csv file
    - the `roundtrip_via_api` helper in `legacy/roundtrip.py` packages
      the load + write + normalize sequence for tests and dev scripts

## Project Structure

Build config is in `pyproject.toml`. Source is in `src/run_preflight/`.
Tests are in `tests/`. SQL schema is in `src/run_preflight/sql/`.

| File | Role |
|------|------|
| `src/run_preflight/sql/schema.sql` | Provides full DDL: reference tables, legacy format registry, core domain tables, platform-specific tables, reconstruction views |
| `src/run_preflight/constants.py` | Holds all string-literal constants (section names, column names, platform strings) |
| `src/run_preflight/db.py` | Creates SQLite DB from schema.sql, populates tables from parsed data |
| `src/run_preflight/legacy/api.py` | Provides consumer-facing wrappers (migrate_legacy_csv_to_sqlite, write_legacy_csv) over the load and write pipelines |
| `src/run_preflight/legacy/parser.py` | Parses omnibus CSV into dict of sections (header_kv, values_only, tabular) |
| `src/run_preflight/legacy/validate.py` | Validates parsed sections against the view registry |
| `src/run_preflight/legacy/reconstruct.py` | Rebuilds omnibus CSV from SQL views via the legacy format registry |
| `src/run_preflight/legacy/formatting.py` | Defines shared formatting (boolean columns, bcl_scrub_name) |
| `src/run_preflight/legacy/roundtrip.py` | Packages load + write + normalize as test/dev helpers for byte-comparing reconstructed output against the original |

## Changelog

When a unit of work is completed, add an entry to the `[Unreleased]` section of
`CHANGELOG.md` (root) under the appropriate Added/Changed/Fixed/Removed heading
before considering the work done. The file follows the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Testing

- Framework: **pytest**
- Run tests: `pytest`
- Tests are round-trip: load real CSV → DB → write CSV → compare to original
- Test data: real legacy CSV sample sheets in `tests/data/`
- The `roundtrip_via_api` helper in `legacy/roundtrip.py` runs load + write + normalize against a per-test temp dir

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
