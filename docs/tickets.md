# run_preflight: Implementation Tickets

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

### 020: Add `rebuild_table` helper for SQLite 12-step table rebuilds

**Goal:** Provide a reusable helper in `migrate.py` that executes the SQLite
12-step table rebuild process, so `.py` migration patches can perform
structural table changes (rename columns, change types, add/remove constraints)
that `ALTER TABLE` cannot handle.

**Design rationale:** SQLite's `ALTER TABLE` only supports renaming tables,
renaming columns, and adding columns. Any other structural change (dropping
columns, changing types, modifying constraints) requires the official 12-step
rebuild pattern documented at https://www.sqlite.org/lang_altertable.html.

**Scope:**

- `src/run_preflight/migrate.py`: add `rebuild_table(conn, table, new_ddl,
  column_mapping)` function (~40 lines) implementing:
  1. `PRAGMA foreign_keys = OFF`
  2. `SAVEPOINT rebuild`
  3. `CREATE TABLE` with new DDL (temp name)
  4. Copy data with column mapping
  5. `DROP TABLE` old
  6. `ALTER TABLE` rename new to old name
  7. Recreate indexes from `sqlite_schema`
  8. Recreate triggers from `sqlite_schema`
  9. Recreate views from `sqlite_schema`
  10. `PRAGMA foreign_key_check`
  11. `RELEASE rebuild`
  12. `PRAGMA foreign_keys = ON`
- `tests/test_migrate.py`: add `TestRebuildTable` class with tests for schema
  preservation (indexes, views), column mapping, and foreign key integrity

**Exclusions:**

- No actual schema-change patches using `rebuild_table` (those belong to the
  tickets that introduce the schema changes)

**Acceptance criteria:**

- `rebuild_table` preserves dependent indexes, triggers, and views
- `rebuild_table` correctly maps columns from old to new schema
- `rebuild_table` passes `PRAGMA foreign_key_check` after rebuild
- All existing tests pass unchanged

**Estimated net line change:** ~80 lines across `migrate.py` and
`test_migrate.py`

## Consumer API

_Tickets for the stable API that domain consumers will migrate to will be added here once the infrastructure and format coverage are in place._

---

# Completed

| Ticket | Description | Key Results |
|--------|-------------|-------------|
| 001 | Add `.gitignore` | Standard Python `.gitignore` at project root |
| 002 | Restructure to `src/run_preflight/` package layout | `src/` layout, schema inside package, root `__init__.py` removed, CLAUDE.md at root |
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
| 014 | Support arbitrary extra columns in legacy Data sections | `legacy_extra_column` table; extra column detection/storage in `populate_db`; alphabetical extra column reconstruction; `compression_sample` table normalizing well semantics; pre-v101 replicate rejection; round-trip tests for v100 with/without replicates |
| 015 | Round-trip abs_quant_metag v11; make Lane required for Illumina | Format registry rows reusing existing views + `omnibus_sample_context`; removed `contains_lane` optional column rows from all Illumina formats; removed `CHECK_CONTAINS_LANE`; round-trip test passes |
| 017 | Round-trip standard_metat v10 | Format registry + `omnibus_standard_metat_v10_data` view layering on v0 base; `_populate_metatranscriptomic_sample` in `db.py`; `COL_TOTAL_RNA_CONC` constant; round-trip test passes |
| 018 | Round-trip tellseq_metag v10 and tellseq_absquant v10 | `lane INTEGER` on `tellseq_sample`; `omnibus_tellseq_metag_v10_data` and `omnibus_tellseq_absquant_v10_data` views; `_populate_tellseq_sample` in `db.py`; `is_tellseq` sub-dispatch within Illumina branch; two round-trip tests pass |
| 019 | Add database migration infrastructure | `migrate.py` with version tracking, patch discovery, SQL/Python dispatch, `open_db`; `PRAGMA user_version` stamping in `create_db`; `roundtrip.py` uses `open_db`; `sql/patches/` directory; 16 migration tests pass |
| 016 | Add capability flag infrastructure and per-capability triggers | `capability` reference table, `run_capability` junction table, `run_derived_capability` view, three BEFORE INSERT triggers, `_populate_run_capabilities` in `db.py`, `CAP_ABSQUANT_*` constants; superseded by ticket 021 |
| 021 | Replace stored `run_capability` table with derived per-capability views | Removed `capability` table, `run_capability` table, 3 triggers, `_populate_run_capabilities`, `CAP_ABSQUANT_*` constants, `CAPABILITY_COLUMN_MAP`; added `run_capability_absquant_mass/volume/surface_area` leaf views, `run_capability` union view; `run_derived_capability` unchanged; tests rewritten to verify derivation from sample data; architecture.md updated |
| 023 | Multi-lane support via platform-table surrogate PKs | Added `illumina_sample_id` / `tellseq_sample_id` / `pacbio_sample_id` surrogate PKs; demoted `prepped_sample_id` to non-unique FK on illumina/tellseq with `UNIQUE(prepped_sample_id, COALESCE(lane,-1))` indexes; added 5 BEFORE INSERT triggers (i5/i7 invariance, barcode invariance, illumina/tellseq lane uniformity, one-run-per-DB); added `_check_per_tube_consistency` and `(plate, orig_name, dest_well)` cache layer in `populate_db`; new `tests/test_multilane.py` (12 tests); new `good_multilane_synthetic.csv` round-trip fixture; deleted tickets 022a/b in favor of this design; `docs/architecture.md` rewritten with "Path not taken: prepped_library normalization" |
| 024 | Unify Illumina Settings views; widen v0 and v90 to all three keys | Replaced `omnibus_standard_metag_v{0,90,101}_settings` with a single `omnibus_illumina_settings` view exposing `ReverseComplement` + `MaskShortReads` + `OverrideCycles`; all 9 Illumina format registry rows repointed; patch `004_unify_illumina_settings_view.sql`; new fixtures `good_standard_metagv90_w_all_settings.csv` and `good_standard_metagv0_w_all_settings.csv` plus round-trip tests; new validator test asserts v90 accepts all three keys |
| 025 | Reset schema-zero baseline; relax `reverse_complement` NOT NULL | Collapsed patches 001–004 into `schema_v0.sql` so `schema_v0.sql` matches `schema.sql`; removed the four patch files; relaxed `illumina_run.reverse_complement` to nullable (was `BOOLEAN NOT NULL DEFAULT 0`) to fix the input-absent-vs-default-emit round-trip asymmetry; populate now passes `nullable=True`; new fixture `good_standard_metagv90_no_reverse_complement.csv` with round-trip test; validator test asserts absent `ReverseComplement` stays NULL in DB |
| 026 | Relax `input_sample.sample_name` NOT NULL; require sample_name OR biosample_accession | Dropped `NOT NULL` from `input_sample.sample_name` in `schema.sql` + `schema_v0.sql` (no patch — `schema_v0` edited in lockstep since no patches were pending); added table-level `CHECK (sample_name IS NOT NULL OR biosample_accession IS NOT NULL)`; refreshed the stale `biosample_accession` comment; new `TestInputSampleCheck` class in `test_updates.py` (4 tests) verifies the CHECK rejects both-NULL and accepts sample_name-only, biosample_accession-only, and both-non-null |
