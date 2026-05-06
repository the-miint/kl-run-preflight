"""Shared string-literal constants for the package.

Centralises section names, column/field names, section format types,
platform strings, and other repeated literals so that each value is
defined in exactly one place.
"""

# ---------------------------------------------------------------------------
# Section names
# ---------------------------------------------------------------------------

SECTION_HEADER = "Header"
SECTION_DATA = "Data"
SECTION_BIOINFORMATICS = "Bioinformatics"
SECTION_CONTACT = "Contact"
SECTION_SAMPLE_CONTEXT = "SampleContext"
SECTION_READS = "Reads"
SECTION_SETTINGS = "Settings"


# ---------------------------------------------------------------------------
# Section format types (legacy_samplesheet_view.section_format values)
# ---------------------------------------------------------------------------

FORMAT_HEADER_KV = "header_kv"
FORMAT_VALUES_ONLY = "values_only"
FORMAT_TABULAR = "tabular"


# ---------------------------------------------------------------------------
# Header-section field names (key-value pairs in [Header])
# ---------------------------------------------------------------------------

FIELD_SHEET_TYPE = "SheetType"
FIELD_SHEET_VERSION = "SheetVersion"
FIELD_ASSAY = "Assay"
FIELD_INVESTIGATOR_NAME = "Investigator Name"
FIELD_EXPERIMENT_NAME = "Experiment Name"
FIELD_DATE = "Date"
FIELD_DESCRIPTION = "Description"


# ---------------------------------------------------------------------------
# Settings-section field names (key-value pairs in [Settings])
# ---------------------------------------------------------------------------

FIELD_REVERSE_COMPLEMENT = "ReverseComplement"
FIELD_MASK_SHORT_READS = "MaskShortReads"
FIELD_OVERRIDE_CYCLES = "OverrideCycles"


# ---------------------------------------------------------------------------
# Tabular column names (shared across Data, Bioinformatics, Contact)
# ---------------------------------------------------------------------------

COL_SAMPLE_PROJECT = "Sample_Project"
COL_SAMPLE_NAME = "Sample_Name"
COL_SAMPLE_PLATE = "Sample_Plate"
COL_SAMPLE_WELL = "Sample_Well"
COL_SAMPLE_ID = "Sample_ID"
COL_QIITA_ID = "QiitaID"
COL_EMAIL = "Email"
COL_HUMAN_FILTERING = "HumanFiltering"
COL_FORWARD_ADAPTER = "ForwardAdapter"
COL_REVERSE_ADAPTER = "ReverseAdapter"
COL_BARCODES_ARE_RC = "BarcodesAreRC"
COL_LIBRARY_CONSTRUCTION_PROTOCOL = "library_construction_protocol"
COL_EXPERIMENT_DESIGN_DESCRIPTION = "experiment_design_description"
COL_CONTAINS_REPLICATES = "contains_replicates"


# ---------------------------------------------------------------------------
# Data-section column names (fields specific to [Data] rows)
# ---------------------------------------------------------------------------

COL_WELL_ID_384 = "well_id_384"
COL_WELL_DESCRIPTION = "Well_description"
COL_TOTAL_RNA_CONC = "total_rna_concentration_ng_ul"
COL_VOL_EXTRACTED_ELUTION = "vol_extracted_elution_ul"
COL_ORIG_NAME = "orig_name"
COL_DESTINATION_WELL_384 = "destination_well_384"
COL_LANE = "Lane"

# Illumina index columns
COL_I7_INDEX_ID = "I7_Index_ID"
COL_INDEX = "index"
COL_I5_INDEX_ID = "I5_Index_ID"
COL_INDEX2 = "index2"

# PacBio / TellSeq shared data column
COL_BARCODE_ID = "barcode_id"
# PacBio data columns
COL_TWIST_ADAPTOR_ID = "twist_adaptor_id"
COL_SYNDNA_IS_TWISTED = "syndna_is_twisted"
# absquant columns
COL_MASS_SYNDNA_INPUT = "mass_syndna_input_ng"
COL_EXTRACTED_GDNA_CONC = "extracted_gdna_concentration_ng_ul"
COL_SYNDNA_POOL_NUMBER = "syndna_pool_number"
# absquant shared column (required for all absquant capabilities)
COL_SEQUENCED_SAMPLE_GDNA_MASS = "sequenced_sample_gdna_mass_ng"
# absquant total-sample-input metric columns
COL_EXTRACTED_SAMPLE_MASS = "extracted_sample_mass_g"
COL_EXTRACTED_SAMPLE_VOLUME = "extracted_sample_volume_ul"
COL_EXTRACTED_SAMPLE_SURFACE_AREA = "extracted_sample_surface_area_cm2"

# Legacy CSV → DB column aliases.  Some legacy formats use different column
# names for the same underlying data.  Keys are the CSV header names; values
# are the canonical DB constant names used throughout this codebase.
# NOTE: the reconstruction views in schema.sql hard-code the reverse mapping
# (DB → CSV) in their column aliases.  If you change an entry here, update the
# corresponding view alias to match.
LEGACY_COLUMN_ALIASES: dict[str, str] = {
    "calc_mass_sample_aliquot_input_g": COL_EXTRACTED_SAMPLE_MASS,
    "sample_volume_ul": COL_EXTRACTED_SAMPLE_VOLUME,
    "sample_surface_area_cm2": COL_EXTRACTED_SAMPLE_SURFACE_AREA,
}


# ---------------------------------------------------------------------------
# SampleContext column names (lowercase, distinct from Data columns)
# ---------------------------------------------------------------------------

COL_SC_SAMPLE_NAME = "sample_name"
COL_SC_SAMPLE_TYPE = "sample_type"


# ---------------------------------------------------------------------------
# Internal / filter column (in SQL views but excluded from CSV output)
# ---------------------------------------------------------------------------

COL_RUN_ID = "run_id"


# ---------------------------------------------------------------------------
# Platform and sequencer strings
# ---------------------------------------------------------------------------

PLATFORM_PACBIO = "PacBio"
PLATFORM_ILLUMINA = "Illumina"
SEQUENCER_PACBIO_REVIO = "Pacbio_Revio"
SEQUENCER_UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# Sample-type strings (values in the sample_type reference table)
# ---------------------------------------------------------------------------

SAMPLE_TYPE_STANDARD = "standard"


# ---------------------------------------------------------------------------
# SampleContext type mappings (source value -> DB sample_type name)
# ---------------------------------------------------------------------------

CONTEXT_TYPE_CONTROL_BLANK = "control blank"
CONTEXT_TYPE_CONTROL_KATHAROSEQ = "control katharoseq"
DB_TYPE_EXTRACTION_BLANK = "extraction_blank"
DB_TYPE_KATHAROSEQ_POSITIVE = "katharoseq_cells_positive_control"

CONTEXT_TYPE_MAP = {
    CONTEXT_TYPE_CONTROL_BLANK: DB_TYPE_EXTRACTION_BLANK,
    CONTEXT_TYPE_CONTROL_KATHAROSEQ: DB_TYPE_KATHAROSEQ_POSITIVE,
}


# ---------------------------------------------------------------------------
# Check-function name strings (legacy_samplesheet_optional_columns)
# ---------------------------------------------------------------------------

# Values must use the `check_` prefix to flag them as function-handle
# strings rather than CSV column names — they are stored in the
# `check_function` field of legacy_samplesheet_optional_columns rows.
CHECK_CONTAINS_REPLICATES = "check_contains_replicates"
CHECK_CONTAINS_KATHAROSEQ = "check_contains_katharoseq"
CHECK_HAS_SEQUENCED_GDNA_MASS = "check_has_sequenced_gdna_mass"
CHECK_HAS_EXTRACTED_SAMPLE_MASS = "check_has_extracted_sample_mass"
CHECK_HAS_EXTRACTED_SAMPLE_VOLUME = "check_has_extracted_sample_volume"
CHECK_HAS_EXTRACTED_SAMPLE_SURFACE_AREA = "check_has_extracted_sample_surface_area"
