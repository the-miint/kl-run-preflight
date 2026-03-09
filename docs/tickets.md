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


## Code Quality

## Schema Evolution

### 016: Add total-sample-input metrics with run-level enforcement

**Goal:** Add three total-sample-input metric columns to
`metagenomic_absquant_sample` with an absquant-specific run-level declaration
table and a gated BEFORE INSERT trigger, so that new DBs reject incomplete
absquant data while legacy loading bypasses the constraint.

**Design rationale:** See "Total-sample-input metrics enforcement" section in
`docs/architecture.md`.

**Blocked on:** The names and data types of the three total-sample-input
metric columns have not yet been provided. Implementation cannot begin until
these are known.

**Scope:**

- `schema.sql`: add `db_metadata` table (seeded `enforce_strict = '1'`), add
  `absquant_run_required_metric` table, add three metric columns to
  `metagenomic_absquant_sample`, add gated BEFORE INSERT trigger
- `db.py`: wrap `populate_db` inserts with `enforce_strict` toggle
  (`'0'` before, `'1'` after)
- `constants.py`: add column-name constants for the three metrics
- Tests: verify trigger blocks NULL when strict, allows NULL when permissive,
  round-trip tests still pass

**Exclusions:**

- Reconstruction views for the new columns (no legacy format outputs them)
- Migration patches for old DBs (separate future ticket)

**Acceptance criteria:**

- `db_metadata` table exists with `enforce_strict` defaulting to `'1'`
- Inserting an absquant sample with a NULL declared-required metric fails when
  `enforce_strict = '1'`
- Inserting an absquant sample with a NULL declared-required metric succeeds
  when `enforce_strict = '0'`
- All existing round-trip tests pass unchanged
- `populate_db` restores `enforce_strict` to `'1'` after legacy loading

**Estimated net line change:** ~60 lines across `schema.sql`, `db.py`,
`constants.py`, and tests

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
| 014 | Support arbitrary extra columns in legacy Data sections | `legacy_extra_column` table; extra column detection/storage in `populate_db`; alphabetical extra column reconstruction; `compression_placement` table normalizing well semantics; pre-v101 replicate rejection; round-trip tests for v100 with/without replicates |
| 015 | Round-trip abs_quant_metag v11; make Lane required for Illumina | Format registry rows reusing existing views + `omnibus_sample_context`; removed `contains_lane` optional column rows from all Illumina formats; removed `CHECK_CONTAINS_LANE`; round-trip test passes |
| 017 | Round-trip standard_metat v10 | Format registry + `omnibus_standard_metat_v10_data` view layering on v0 base; `_populate_metatranscriptomic_sample` in `db.py`; `COL_TOTAL_RNA_CONC` constant; round-trip test passes |
| 018 | Round-trip tellseq_metag v10 and tellseq_absquant v10 | `lane INTEGER` on `tellseq_sample`; `omnibus_tellseq_metag_v10_data` and `omnibus_tellseq_absquant_v10_data` views; `_populate_tellseq_sample` in `db.py`; `is_tellseq` sub-dispatch within Illumina branch; two round-trip tests pass |
