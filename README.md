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

- `pacbio_absquant` v10, v11
- `pacbio_metag` v10, v11
- `standard_metag` v90, v100, v101
- `standard_metag` v0 (which is really the first metaT)
- `abs_quant_metag` v10, v11
- `standard_metat` v10
- `tellseq_metag` v10
- `tellseq_absquant` v10

## Project structure

```