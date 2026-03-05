# sequencing_brief: Implementation Tickets

**Aims:**

1. Establish standard Python project structure and tooling
2. Round-trip all legacy omnibus CSV formats through SQLite losslessly
3. Expose a stable API so domain consumers can migrate off direct omnibus access
4. Sunset omnibus CSVs in favor of SQLite as the canonical format

---

# Open Tickets

## Project Infrastructure

### TICKET-003: Add `pyproject.toml` and Make the Project Installable

**Priority:** P0 | **Deps:** 002

**Goal:** Make the project pip-installable with declared dependencies and dev tooling.

**Scope:**

- Create `pyproject.toml` with:
  - setuptools backend
  - `versioningit` for version management (`dynamic = ["version"]`, `[tool.versioningit.write]` targeting `src/sequencing_brief/_version.py`)
  - `requires-python` matching current minimum
  - runtime dependencies (none beyond stdlib currently)
  - `[project.optional-dependencies]` dev group: `pytest`, `ruff`
  - `[tool.ruff]` with `target-version`
  - `[tool.pytest.ini_options]` with `testpaths = ["tests"]`
  - `[tool.setuptools.package-data]` including `sql/*.sql`

**Exclusions:** Does not switch tests from unittest to pytest style (TICKET-004). Does not run ruff on existing code.

**AC:**

- [ ] `pip install -e ".[dev]"` succeeds
- [ ] `python -c "import sequencing_brief"` works after install
- [ ] `schema.sql` is included in the installed package
- [ ] `pytest` discovers and runs all existing tests
- [ ] `ruff check .` runs without configuration errors

**Affected files:** New `pyproject.toml`, new `src/sequencing_brief/_version.py`

**Estimated net line change:** ~50

---

### TICKET-004: Switch Test Runner from unittest to pytest

**Priority:** P1 | **Deps:** 003

**Goal:** Adopt pytest as the test runner to get better assertion diffs and enable fixtures/parameterization as the test suite grows.

**Scope:**

- Update `.vscode/settings.json`: set `pytestEnabled: true`, `unittestEnabled: false`
- Update CLAUDE.md Testing section: change run command to `pytest`, remove unittest-specific notes

**Exclusions:** Does not rewrite existing test classes to pytest style — pytest runs `unittest.TestCase` tests natively. Test rewrites can happen incrementally as tests are touched.

**AC:**

- [ ] `pytest` discovers and passes all existing tests
- [ ] VS Code test explorer shows tests via pytest

**Affected files:** `.vscode/settings.json`, `CLAUDE.md`

**Estimated net line change:** ~10

---

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
