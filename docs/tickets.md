# sequencing_brief: Implementation Tickets

**Aims:**

1. Establish standard Python project structure and tooling
2. Round-trip all legacy omnibus CSV formats through SQLite losslessly
3. Expose a stable API so domain consumers can migrate off direct omnibus access
4. Sunset omnibus CSVs in favor of SQLite as the canonical format

---

# Open Tickets

## Project Infrastructure

## Format Coverage

### Ticket 014 — Support arbitrary extra columns in legacy Data sections

- **Goal:** Store and round-trip arbitrary extra columns that appear in the
  Data section of legacy omnibus files, so that no sample-level data is lost
  during parse → SQLite → reconstruct
- **Scope:**
  - `src/sequencing_brief/sql/schema.sql` — add `legacy_extra_column` table
  - `src/sequencing_brief/db.py` — in `populate_db`, detect Data columns not
    recognized by the format's view and insert them into
    `legacy_extra_column`; add helper to determine the "known" Data column
    set for a format
  - `src/sequencing_brief/reconstruct.py` — in `reconstruct_omnibus`, query
    `legacy_extra_column` for extra column names, append them alphabetically
    after known columns, merge extra values into each Data row
  - `tests/` — add round-trip test using a CSV with at least one extra column
    (example source:
    `fork-kl-metapool/metapool/tests/data/sheet_wo_replicates.csv`, which
    has `an_optional_carried_column`)
- **Exclusions:**
  - Non-Data sections (Bioinformatics, Contact, SampleContext, etc.)
  - Native (non-legacy) runs — `legacy_extra_column` is legacy-only
- **Acceptance criteria:**
  - `legacy_extra_column` table exists with PK
    `(compression_sample_id, column_name)` and a `column_value TEXT` column
  - Parsing a CSV with extra Data columns populates `legacy_extra_column`
    with one row per extra column per sample
  - Reconstruction appends extra columns alphabetically after the known
    columns in the Data section
  - Round-trip test passes: parse → DB → reconstruct → compare (after
    normalizing column order)
- **Estimated net line change:** ~120 lines across all files

## Code Quality

## Consumer API

_Tickets for the stable API that domain consumers will migrate to will be added here once the infrastructure and format coverage are in place._

---

# Completed

| Ticket | Description | Key Results |
|--------|-------------|-------------|
| 001 | Add `.gitignore` | Standard Python `.gitignore` at project root |
| 002 | Restructure to `src/sequencing_brief/` package layout | `src/` layout, schema inside package, root `__init__.py` removed, CLAUDE.md at root |
| 003 | Add `pyproject.toml` and make the project installable | `pyproject.toml` with setuptools + versioningit, `_version.py`, `environment.yml`, GitHub Actions CI workflow |
| 004 | Switch test runner from unittest to pytest | Removed `.vscode/settings.json`, updated CLAUDE.md testing section |
| 005 | Round-trip PacBio Metag v10 | `omnibus_pacbio_metag_v10_data` base view, refactored v11 to layer on top, format registry rows, round-trip test |
| 007 | Consolidate view introspection and make writers pure formatters | `_introspect_view` replaces `_get_view_columns` + `_view_has_run_id`; writers are pure formatters with no DB access; `_WRITERS` dead code removed |
| 008 | Extract boolean-string parsing helper in `db.py` | `_parse_bool_str` replaces four ad-hoc conversions; supports nullable for `syndna_is_twisted`; defaults standardized to `"True"`/`"False"` |
| 009 | Use or remove `_lookup_id` in `db.py` | Wired `assay_type` and `sequencing_platform` lookups through `_lookup_id`; legacy format lookup unchanged (two-column match, nullable) |
| 010 | Narrow `cursor.lastrowid` types in `db.py` | Added `assert cur.lastrowid is not None` at 5 INSERT sites to eliminate Pyright `reportArgumentType` warnings |
| 011 | Move `_get_view_columns` to a shared module | Moved `introspect_view` and `get_view_columns` (public names) to `db.py`; updated imports in `reconstruct.py` and `validate.py` |
| 012 | Add `run_id` to shared views and remove substring dispatch | `omnibus_contact` and `omnibus_sample_context` now include `run_id`; `_query_view` uses uniform `WHERE run_id = ?` filtering for all views |
| 013 | Round-trip standard_metag v0 and v90 | Layered SQL views (v90 base → v0 renames → v101 adds columns); shared Illumina header/reads views; `parse_omnibus` accepts `section_formats` from DB; `get_section_formats` in `db.py`; column reordering normalization in round-trip tests; two new round-trip tests pass |
