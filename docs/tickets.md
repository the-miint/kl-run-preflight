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

_Tickets for additional legacy format support will be added here as needed._

---

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
