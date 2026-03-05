# sql_samplesheet

## Naming

The package is named `sequencing_brief` because it represents the information
package that crosses the boundary from the wet lab (which prepares material for
sequencing) to the dry lab (which processes the sequencing data). It is *not*
the vendor-specific input file submitted to the sequencing facility — that is an
Illumina "sample sheet," a PacBio "manifest," etc. Historically this concept was
called a "sample sheet" because it originated from the Illumina term, but that
name is being retired to avoid conflating the internal handoff with any
vendor-specific format. The repository is currently named `sql_samplesheet`
pending rename.

## Project Overview

Encapsulate the "sample sheet" concept so that all consumers of sample sheets
access them through this project. Internally, represent sample sheets
in a normalized SQLite schema The current phase supports silent replacement of
existing sample sheet objects by providing round-tripping of all versions of the
legacy "omnibus" CSV files (parse → SQLite → reconstruct → diff)
so that existing sample sheet class structures in other code can be swapped for
SQL samplesheets without affecting their interaction with external producers and
consumers. Once all domain consumers use this project, omnibus CSVs will be sunset
in favor of SQLite files as the canonical sample sheet format, enabling stronger
correctness constraints and easier data management.

## Architecture

### Core vs transitional

The SQLite schema (`src/sequencing_brief/sql/schema.sql`) is the permanent, long-term core of the
project. It defines the normalized domain model: runs, plates, samples,
platform-specific extensions, and reference data.

The parser (`parser.py`), reconstructor (`reconstruct.py`), and
formatter (`formatting.py`) are transitional bridging code that exists to
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

### Key domain rules

- Controls have NULL `project_id` on `input_sample`; they inherit project
  association via `input_plate`
- `compression_sample.sample_name` is NULL when identical to
  `input_sample.sample_name`; populated only for replicates
- `run_id` column in reconstruction views is a filter column excluded from
  CSV output

### Transitional workflows

1) Read a legacy omnibus file into SQLite format:
    - run `parser.parse_omnibus` to parse the data from the input file into sections
    - run `db.create_db` to create a new SQLite database
    - run `validator.validate_omnibus` to check the validity of the input file format using per-section database views
    - only if the existing data validates, run `db.populate_db` to write it to the database

2) Write a legacy omnibus file from SQLite format:
    - load a SQLite db file
    - read the run_id available in the db; if there is more than one, error
    - run `reconstruct.reconstruct_omnibus` to generate the omnibus csv content
    - write the omnibus csv content to a csv file

3) Round-trip a legacy omnibus file through SQLite format (used only for testing)
    - run workflow 1
    - run workflow 2
    - minimally modify the input csv file (AFTER workflow 1 usage) to be a known-good:
        - replace any case of true and false with True and False
        - order columns in the Data table according to what is expected in a "valid" output
    - directly compare the raw text of the minimally modified known-good omnibus file to the output omnibus csv file

## Project Structure

No pyproject.toml yet. Source is in `src/sequencing_brief/`.
Tests are in `tests/`. SQL schema is in `src/sequencing_brief/sql/`.

| File | Role |
|------|------|
| `src/sequencing_brief/sql/schema.sql` | Provides full DDL: reference tables, legacy format registry, core domain tables, platform-specific tables, reconstruction views |
| `src/sequencing_brief/constants.py` | Holds all string-literal constants (section names, column names, platform strings) |
| `src/sequencing_brief/parser.py` | Parses omnibus CSV into dict of sections (header_kv, values_only, tabular) |
| `src/sequencing_brief/validate.py` | Validates parsed sections against the view registry |
| `src/sequencing_brief/db.py` | Creates SQLite DB from schema.sql, populates tables from parsed data |
| `src/sequencing_brief/reconstruct.py` | Rebuilds omnibus CSV from SQL views via the legacy format registry |
| `src/sequencing_brief/formatting.py` | Defines shared formatting (boolean columns, bcl_scrub_name) |
| `src/sequencing_brief/cli.py` | Specifies CLI entry point for round-trip testing |

## Testing

- Framework: **unittest** (not pytest)
- VS Code configured for unittest discovery
- Run tests: `python -m unittest discover -v -s . -p "test_*.py"`
- Tests are round-trip: parse real CSV → DB → reconstruct → compare to original
- Test data: real sample sheet CSVs in `tests/data/`
- `DEBUG_OUTPUT_DIR` in test_roundtrip.py writes DB + CSV to disk when set

## Imports

Tests import from the installed package (e.g.,
`from sequencing_brief.db import create_db`). Internal imports within
`src/sequencing_brief/` use relative imports
(e.g., `from .constants import ...`).

## Adding a New Legacy Format

1. Add format row to `legacy_samplesheet_format` in `sql/schema.sql`
2. Add `legacy_samplesheet_view` rows mapping sections to views
3. Create SQL views for each section (can reuse shared views like
   `omnibus_contact`, `omnibus_sample_context`)
4. Add optional column groups if needed
5. Update `db.py` population logic if the new format has new columns
6. Add round-trip test with a real sample CSV
