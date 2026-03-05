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
