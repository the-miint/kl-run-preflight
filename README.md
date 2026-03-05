# sequencing_brief

Normalized SQLite representation of the information package handed off from
the wet lab to the dry lab for sequencing data processing. Replaces the legacy
CSV "samplesheet" format with a relational schema that enforces
correctness constraints and simplifies data management.

### Why "sequencing_brief"?

This package represents the information that crosses the boundary from the wet
lab (which prepares material for sequencing) to the dry lab (which processes the
sequencing data). It is not the vendor-specific input file submitted to the
sequencing facility (such as an Illumina "sample sheet," a PacBio "manifest,"
etc.). The name distinguishes this internal handoff packet from any vendor format.


## Status

**Current phase:** Adding support for round-tripping legacy CSV samplesheet
files (parse → SQLite → reconstruct → prove same) for all `kl-metapool` samplesheet
formats.

**Supported formats:**

- `pacbio_absquant` v11
- `pacbio_metag` v11
- `standard_metag` v101

## Project structure

```
src/sequencing_brief/     # Installable Python package
    sql/schema.sql          # SQLite schema (DDL + seed data + reconstruction views)
    parser.py               # CSV samplesheet to sections dict parsing
    validate.py             # Validation of parsed sections against DB view registry
    db.py                   # Creation/population of SQLite database
    reconstruct.py          # SQLite db → CSV samplesheet
    formatting.py           # Shared CSV formatting utilities
    constants.py            # String-literal constants
    cli.py                  # CLI entry point for round-trip testing
tests/                      # Round-trip tests with example CSV samplesheets
docs/                       # Project documentation and tickets
```