# kl-run_preflight

Normalized SQLite representation of the information package handed off from
the wet lab to the dry lab for sequencing data processing. Replaces the legacy
CSV "samplesheet" format with a relational schema that enforces
correctness constraints and simplifies data management.



**Supported formats:**

- `pacbio_absquant` v10, v11, v12
- `pacbio_metag` v10, v11
- `standard_metag` v90, v100, v101
- `standard_metag` v0 (which is really the first metaT)
- `abs_quant_metag` v10, v11
- `standard_metat` v10
- `tellseq_metag` v10
- `tellseq_absquant` v10
