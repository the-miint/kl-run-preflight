-- ============================================================
-- Sequencing Sample Sheet Schema v3
-- ============================================================

PRAGMA foreign_keys = ON;

-- ============================================================
-- Reference Tables
-- ============================================================

CREATE TABLE assay_type (
    assay_type_idx   INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE
);

CREATE TABLE sequencing_platform (
    platform_idx     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE
);

CREATE TABLE sample_type (
    sample_type_idx  INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE
);

INSERT INTO assay_type (name) VALUES ('Metagenomic');
INSERT INTO assay_type (name) VALUES ('Metatranscriptomic');
INSERT INTO assay_type (name) VALUES ('Amplicon');

INSERT INTO sequencing_platform (name) VALUES ('Illumina');
INSERT INTO sequencing_platform (name) VALUES ('PacBio');

INSERT INTO sample_type (name) VALUES ('standard');
INSERT INTO sample_type (name) VALUES ('extraction_blank');
INSERT INTO sample_type (name) VALUES ('katharoseq_cells_positive_control');

-- ============================================================
-- Legacy Format Registry
-- ============================================================

CREATE TABLE legacy_section_format (
    format_name TEXT PRIMARY KEY
);

INSERT INTO legacy_section_format (format_name) VALUES ('header_kv');
INSERT INTO legacy_section_format (format_name) VALUES ('tabular');
INSERT INTO legacy_section_format (format_name) VALUES ('values_only');

CREATE TABLE legacy_samplesheet_format (
    legacy_format_idx    INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_sheet_type   TEXT NOT NULL,
    legacy_version      INTEGER NOT NULL,
    UNIQUE(legacy_sheet_type, legacy_version)
);

CREATE TABLE legacy_samplesheet_view (
    legacy_format_idx    INTEGER NOT NULL
        REFERENCES legacy_samplesheet_format(legacy_format_idx),
    section_name        TEXT NOT NULL,
    section_order       INTEGER NOT NULL,
    view_name           TEXT NOT NULL,
    section_format      TEXT NOT NULL DEFAULT 'tabular'
        REFERENCES legacy_section_format(format_name),
    PRIMARY KEY (legacy_format_idx, section_name)
);

CREATE TABLE legacy_samplesheet_optional_columns (
    legacy_format_idx    INTEGER NOT NULL
        REFERENCES legacy_samplesheet_format(legacy_format_idx),
    section_name        TEXT NOT NULL,
    group_name          TEXT NOT NULL,
    column_names        TEXT NOT NULL,       -- comma-separated column names
    check_function      TEXT NOT NULL,       -- function name to determine presence
    insert_after        TEXT,                -- column after which to insert (NULL = append)
    PRIMARY KEY (legacy_format_idx, section_name, group_name)
);

-- Format: pacbio_absquant v11
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('pacbio_absquant', 11);

INSERT INTO legacy_samplesheet_view VALUES
    (1, 'Header',         1, 'omnibus_pacbio_absquant_v11_header',         'header_kv'),
    (1, 'Data',           2, 'omnibus_pacbio_absquant_v11_data',           'tabular'),
    (1, 'Bioinformatics', 3, 'omnibus_pacbio_absquant_v11_bioinformatics', 'tabular'),
    (1, 'Contact',        4, 'omnibus_contact',                            'tabular'),
    (1, 'SampleContext',  5, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (1, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: standard_metag v101
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metag', 101);

INSERT INTO legacy_samplesheet_view VALUES
    (2, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (2, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (2, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (2, 'Data',           4, 'omnibus_standard_metag_v101_data',           'tabular'),
    (2, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (2, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (2, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (2, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_metag v11
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('pacbio_metag', 11);

INSERT INTO legacy_samplesheet_view VALUES
    (3, 'Header',         1, 'omnibus_pacbio_absquant_v11_header',         'header_kv'),
    (3, 'Data',           2, 'omnibus_pacbio_metag_v11_data',              'tabular'),
    (3, 'Bioinformatics', 3, 'omnibus_pacbio_absquant_v11_bioinformatics', 'tabular'),
    (3, 'Contact',        4, 'omnibus_contact',                            'tabular'),
    (3, 'SampleContext',  5, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (3, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_metag v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('pacbio_metag', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (4, 'Header',         1, 'omnibus_pacbio_absquant_v11_header',         'header_kv'),
    (4, 'Data',           2, 'omnibus_pacbio_metag_v10_data',              'tabular'),
    (4, 'Bioinformatics', 3, 'omnibus_pacbio_absquant_v11_bioinformatics', 'tabular'),
    (4, 'Contact',        4, 'omnibus_contact',                            'tabular'),
    (4, 'SampleContext',  5, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (4, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_absquant v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('pacbio_absquant', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (5, 'Header',         1, 'omnibus_pacbio_absquant_v11_header',         'header_kv'),
    (5, 'Data',           2, 'omnibus_pacbio_absquant_v10_data',           'tabular'),
    (5, 'Bioinformatics', 3, 'omnibus_pacbio_absquant_v11_bioinformatics', 'tabular'),
    (5, 'Contact',        4, 'omnibus_contact',                            'tabular'),
    (5, 'SampleContext',  5, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (5, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: standard_metag v90
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metag', 90);

INSERT INTO legacy_samplesheet_view VALUES
    (6, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (6, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (6, 'Settings',       3, 'omnibus_standard_metag_v90_settings',       'header_kv'),
    (6, 'Data',           4, 'omnibus_standard_metag_v90_data',           'tabular'),
    (6, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (6, 'Contact',        6, 'omnibus_contact',                           'tabular');


-- Format: standard_metag v0
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metag', 0);

INSERT INTO legacy_samplesheet_view VALUES
    (7, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (7, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (7, 'Settings',       3, 'omnibus_standard_metag_v0_settings',        'header_kv'),
    (7, 'Data',           4, 'omnibus_standard_metag_v0_data',            'tabular'),
    (7, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (7, 'Contact',        6, 'omnibus_contact',                           'tabular');


-- Format: standard_metag v100
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metag', 100);

INSERT INTO legacy_samplesheet_view VALUES
    (8, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (8, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (8, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (8, 'Data',           4, 'omnibus_standard_metag_v101_data',           'tabular'),
    (8, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (8, 'Contact',        6, 'omnibus_contact',                            'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (8, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: abs_quant_metag v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('abs_quant_metag', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (9, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (9, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (9, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (9, 'Data',           4, 'omnibus_abs_quant_metag_v10_data',           'tabular'),
    (9, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (9, 'Contact',        6, 'omnibus_contact',                            'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (9, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: abs_quant_metag v11
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('abs_quant_metag', 11);

INSERT INTO legacy_samplesheet_view VALUES
    (10, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (10, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (10, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (10, 'Data',           4, 'omnibus_abs_quant_metag_v10_data',           'tabular'),
    (10, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (10, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (10, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (10, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: standard_metat v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metat', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (11, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (11, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (11, 'Settings',       3, 'omnibus_standard_metag_v101_settings',      'header_kv'),
    (11, 'Data',           4, 'omnibus_standard_metat_v10_data',           'tabular'),
    (11, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (11, 'Contact',        6, 'omnibus_contact',                           'tabular');

-- Format: tellseq_metag v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('tellseq_metag', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (12, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (12, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (12, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (12, 'Data',           4, 'omnibus_tellseq_metag_v10_data',             'tabular'),
    (12, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (12, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (12, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (12, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: tellseq_absquant v10
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('tellseq_absquant', 10);

INSERT INTO legacy_samplesheet_view VALUES
    (13, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (13, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (13, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (13, 'Data',           4, 'omnibus_tellseq_absquant_v10_data',          'tabular'),
    (13, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (13, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (13, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (13, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_absquant v12
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('pacbio_absquant', 12);

INSERT INTO legacy_samplesheet_view VALUES
    (14, 'Header',         1, 'omnibus_pacbio_absquant_v11_header',         'header_kv'),
    (14, 'Data',           2, 'omnibus_pacbio_absquant_v12_data',           'tabular'),
    (14, 'Bioinformatics', 3, 'omnibus_pacbio_absquant_v11_bioinformatics', 'tabular'),
    (14, 'Contact',        4, 'omnibus_contact',                            'tabular'),
    (14, 'SampleContext',  5, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (14, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description'),
    (14, 'Data', 'sequenced_gdna_mass',
     'sequenced_sample_gdna_mass_ng',
     'check_has_sequenced_gdna_mass', 'syndna_pool_number'),
    (14, 'Data', 'extracted_sample_mass',
     'calc_mass_sample_aliquot_input_g',
     'check_has_extracted_sample_mass', 'syndna_pool_number'),
    (14, 'Data', 'extracted_sample_volume',
     'sample_volume_ul',
     'check_has_extracted_sample_volume', 'syndna_pool_number'),
    (14, 'Data', 'extracted_sample_surface_area',
     'sample_surface_area_cm2',
     'check_has_extracted_sample_surface_area', 'syndna_pool_number');

-- ============================================================
-- Legacy Extra Columns
-- ============================================================

CREATE TABLE legacy_extra_column (
    prepped_sample_idx   INTEGER NOT NULL
        REFERENCES prepped_sample(prepped_sample_idx),
    column_name             TEXT NOT NULL,
    column_value            TEXT,
    PRIMARY KEY (prepped_sample_idx, column_name)
);

-- ============================================================
-- Core Domain Tables
-- ============================================================

CREATE TABLE project (
    project_idx                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name                    TEXT NOT NULL UNIQUE,
    qiita_id                        TEXT NOT NULL,
    contact_email                   TEXT,
    human_filtering                 BOOLEAN NOT NULL DEFAULT 1,
    library_construction_protocol   TEXT NOT NULL,
    experiment_design_description   TEXT NOT NULL
);

CREATE TABLE input_plate (
    input_plate_idxx      INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_name          TEXT NOT NULL,
    primary_project_idx  INTEGER NOT NULL REFERENCES project(project_idx),
    elution_vol         REAL
);

CREATE TABLE input_sample (
    input_sample_idx     INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_name         TEXT NOT NULL,
    input_plate_idxx      INTEGER NOT NULL REFERENCES input_plate(input_plate_idxx),
    well                TEXT,
    project_idx          INTEGER REFERENCES project(project_idx),
        -- NULL for controls; controls inherit project via input_plate
    sample_type_idx      INTEGER NOT NULL REFERENCES sample_type(sample_type_idx),
    biosample_accession TEXT
        -- NCBI BioSample accession; nullable, populated post-fill
        -- via updates.set_biosample_accession
);

CREATE TABLE sequencing_run (
    run_idx              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_name     TEXT NOT NULL,
    run_date            TEXT NOT NULL,
    investigator_name   TEXT NOT NULL DEFAULT '',
    sequencer           TEXT NOT NULL,
    assay_type_idx       INTEGER NOT NULL REFERENCES assay_type(assay_type_idx),
    platform_idx         INTEGER NOT NULL REFERENCES sequencing_platform(platform_idx),
    compression_plate_name TEXT,
    description         TEXT DEFAULT '',
    legacy_format_idx    INTEGER
        REFERENCES legacy_samplesheet_format(legacy_format_idx)
        -- NULL for native DB-originated runs; non-NULL for ingested legacy files
);


CREATE TABLE compression_sample (
    compression_sample_idx    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_idx          INTEGER NOT NULL REFERENCES sequencing_run(run_idx),
    input_sample_idx INTEGER NOT NULL REFERENCES input_sample(input_sample_idx),
    compression_well  TEXT NOT NULL
        -- Position on the compression plate (well_id_384 / Sample_Well)
);

CREATE TABLE prepped_sample (
    prepped_sample_idx   INTEGER PRIMARY KEY AUTOINCREMENT,
    compression_sample_idx            INTEGER NOT NULL
        REFERENCES compression_sample(compression_sample_idx),
    prepped_well        TEXT NOT NULL,
        -- Final well position; equals compression_well for non-replicates,
        -- destination_well_384 for replicates
    sample_name             TEXT,
        -- NULL when same as input_sample.sample_name;
        -- populated for replicates (e.g. "orig_name.dest_well")
    well_description        TEXT
);

-- ============================================================
-- Platform-Specific Run Configuration
-- ============================================================

CREATE TABLE illumina_run (
    run_idx              INTEGER PRIMARY KEY REFERENCES sequencing_run(run_idx),
    read1_length        INTEGER NOT NULL,
    read2_length        INTEGER NOT NULL,
    reverse_complement  BOOLEAN NOT NULL DEFAULT 0,
    mask_short_reads    TEXT,
    override_cycles     TEXT,
    forward_adapter     TEXT,
    reverse_adapter     TEXT,
    barcodes_are_rc     BOOLEAN
);

-- ============================================================
-- Platform-Specific Sample Tables
-- ============================================================

-- illumina_sample: one row per (prepped_sample, lane).  The surrogate
-- illumina_sample_idx is the stable per-row identifier for the legacy
-- Data section.  The same prepped_sample_idx may appear on multiple
-- lanes; per-library invariants (i5/i7) are enforced via trigger.
CREATE TABLE illumina_sample (
    illumina_sample_idx      INTEGER PRIMARY KEY AUTOINCREMENT,
    prepped_sample_idx   INTEGER NOT NULL
        REFERENCES prepped_sample(prepped_sample_idx),
    i7_index_id             TEXT NOT NULL,
    i7_sequence             TEXT NOT NULL,
    i5_index_id             TEXT NOT NULL,
    i5_sequence             TEXT NOT NULL,
    lane                    INTEGER
);

-- Treat NULL lane as a single sentinel value so two NULL-lane rows for
-- one prepped_sample collide; non-NULL lane values must be distinct
-- per prepped_sample.
CREATE UNIQUE INDEX uq_illumina_cs_lane
    ON illumina_sample(prepped_sample_idx, COALESCE(lane, -1));

CREATE TABLE tellseq_sample (
    tellseq_sample_idx       INTEGER PRIMARY KEY AUTOINCREMENT,
    prepped_sample_idx   INTEGER NOT NULL
        REFERENCES prepped_sample(prepped_sample_idx),
    barcode_id              TEXT NOT NULL,
    lane                    INTEGER
);

CREATE UNIQUE INDEX uq_tellseq_cs_lane
    ON tellseq_sample(prepped_sample_idx, COALESCE(lane, -1));

CREATE TABLE pacbio_sample (
    pacbio_sample_idx        INTEGER PRIMARY KEY AUTOINCREMENT,
    prepped_sample_idx   INTEGER NOT NULL UNIQUE
        REFERENCES prepped_sample(prepped_sample_idx),
    barcode_id              TEXT NOT NULL,
    twist_adaptor_id        TEXT,
    syndna_is_twisted       BOOLEAN
);

-- ============================================================
-- Multi-Lane Integrity Triggers
-- ============================================================

-- Per-library invariants: across rows sharing one prepped_sample,
-- the columns that identify the prepped library (i5/i7 for Illumina,
-- barcode for TellSeq) must be identical.  Lane is the only column
-- that may differ.

CREATE TRIGGER illumina_sample_index_invariance
BEFORE INSERT ON illumina_sample
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM illumina_sample existing
            WHERE existing.prepped_sample_idx = NEW.prepped_sample_idx
              AND (existing.i7_index_id  != NEW.i7_index_id
                OR existing.i7_sequence  != NEW.i7_sequence
                OR existing.i5_index_id  != NEW.i5_index_id
                OR existing.i5_sequence  != NEW.i5_sequence)
        )
        THEN RAISE(ABORT,
            'illumina_sample i5/i7 must be identical across rows sharing prepped_sample_idx')
    END;
END;

CREATE TRIGGER tellseq_sample_barcode_invariance
BEFORE INSERT ON tellseq_sample
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM tellseq_sample existing
            WHERE existing.prepped_sample_idx = NEW.prepped_sample_idx
              AND existing.barcode_id != NEW.barcode_id
        )
        THEN RAISE(ABORT,
            'tellseq_sample barcode_id must be identical across rows sharing prepped_sample_idx')
    END;
END;

-- Lane uniformity: within the database, every row's lane is either
-- uniformly NULL (CSV had no Lane column) or uniformly non-NULL (CSV
-- had a Lane column with a value on every row).  Mixed states cannot
-- be reconstructed back to a valid CSV.  Scoped to the whole table
-- because one-run-per-DB means all rows belong to one CSV.

CREATE TRIGGER illumina_sample_lane_uniformity
BEFORE INSERT ON illumina_sample
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM illumina_sample existing
            WHERE (existing.lane IS NULL) != (NEW.lane IS NULL)
        )
        THEN RAISE(ABORT,
            'illumina_sample lane must be uniformly NULL or uniformly non-NULL within a database')
    END;
END;

CREATE TRIGGER tellseq_sample_lane_uniformity
BEFORE INSERT ON tellseq_sample
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM tellseq_sample existing
            WHERE (existing.lane IS NULL) != (NEW.lane IS NULL)
        )
        THEN RAISE(ABORT,
            'tellseq_sample lane must be uniformly NULL or uniformly non-NULL within a database')
    END;
END;

-- One run per database: a sequencing_brief SQLite file represents one
-- run only.  Other invariants (lane uniformity, the unambiguity of
-- platform-table surrogate PK as the per-row identifier) depend on
-- this property holding.

CREATE TRIGGER one_run_per_db
BEFORE INSERT ON sequencing_run
WHEN (SELECT COUNT(*) FROM sequencing_run) > 0
BEGIN
    SELECT RAISE(ABORT,
        'a sequencing_brief database may contain at most one sequencing_run');
END;

-- ============================================================
-- Workflow-Specific Sample Tables
-- ============================================================

CREATE TABLE metagenomic_absquant_sample (
    prepped_sample_idx           INTEGER PRIMARY KEY
        REFERENCES prepped_sample(prepped_sample_idx),
    syndna_pool_mass_ng             REAL,
    extracted_gdna_concentration    REAL,
    syndna_pool_number              TEXT,
    sequenced_sample_gdna_mass_ng   REAL,
    extracted_sample_mass_g         REAL,
    extracted_sample_volume_ul      REAL,
    extracted_sample_surface_area_cm2 REAL
);

CREATE TABLE metatranscriptomic_sample (
    prepped_sample_idx           INTEGER PRIMARY KEY
        REFERENCES prepped_sample(prepped_sample_idx),
    total_rna_concentration_ng_ul   REAL
);

CREATE TABLE katharoseq_sample (
    input_sample_idx         INTEGER PRIMARY KEY
        REFERENCES input_sample(input_sample_idx),
    rack_id                 TEXT,
    tube_code               TEXT,
    number_of_cells         INTEGER
);

-- ============================================================
-- Per-Capability Views (derived from sample data)
-- ============================================================

-- Each view returns (run_idx) for runs that have at least one sample with
-- non-null data for the corresponding metric column.  These replace the
-- stored run_capability table and enforcement triggers: capabilities are
-- derived from what the data actually contains, not declared up front.

CREATE VIEW run_capability_absquant_mass AS
SELECT DISTINCT cs.run_idx
FROM metagenomic_absquant_sample ma
JOIN prepped_sample prs ON ma.prepped_sample_idx = prs.prepped_sample_idx
JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
WHERE ma.extracted_sample_mass_g IS NOT NULL;

CREATE VIEW run_capability_absquant_volume AS
SELECT DISTINCT cs.run_idx
FROM metagenomic_absquant_sample ma
JOIN prepped_sample prs ON ma.prepped_sample_idx = prs.prepped_sample_idx
JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
WHERE ma.extracted_sample_volume_ul IS NOT NULL;

CREATE VIEW run_capability_absquant_surface_area AS
SELECT DISTINCT cs.run_idx
FROM metagenomic_absquant_sample ma
JOIN prepped_sample prs ON ma.prepped_sample_idx = prs.prepped_sample_idx
JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
WHERE ma.extracted_sample_surface_area_cm2 IS NOT NULL;

-- Union view: "what can this run do?"
CREATE VIEW run_capability AS
SELECT run_idx, 'absquant_mass' AS capability_name
    FROM run_capability_absquant_mass
UNION ALL
SELECT run_idx, 'absquant_volume'
    FROM run_capability_absquant_volume
UNION ALL
SELECT run_idx, 'absquant_surface_area'
    FROM run_capability_absquant_surface_area;

-- ============================================================
-- Derived Capability View
-- ============================================================

-- Computes consumer-level capabilities from run_capability.
-- Returns (run_idx, capability_family, version) tuples.
-- Higher versions are supersets of lower versions.

CREATE VIEW run_derived_capability AS
-- absquant v1: run has at least one absquant metric capability
SELECT DISTINCT run_idx, 'absquant' AS capability_family, 1 AS version
FROM run_capability
WHERE capability_name IN ('absquant_mass', 'absquant_volume', 'absquant_surface_area')
;

-- ============================================================
-- Utility Views
-- ============================================================

-- All projects associated with a plate (includes primary + sample-level)
CREATE VIEW input_plate_projects AS
    SELECT DISTINCT input_plate_idxx, project_idx
        FROM input_sample WHERE project_idx IS NOT NULL
    UNION
    SELECT input_plate_idxx, primary_project_idx AS project_idx
        FROM input_plate;

-- Maps each control sample to every project on its plate
CREATE VIEW control_project_associations AS
    SELECT s.input_sample_idx, s.sample_name, st.name AS control_type,
           pp.project_idx, p.project_name, p.qiita_id
    FROM input_sample s
    JOIN sample_type st ON s.sample_type_idx = st.sample_type_idx
    JOIN input_plate_projects pp ON s.input_plate_idxx = pp.input_plate_idxx
    JOIN project p ON pp.project_idx = p.project_idx
    WHERE s.project_idx IS NULL;

-- Detects replicated samples (a compression_sample with multiple prepped_wells)
CREATE VIEW replicated_samples AS
    SELECT cs.run_idx, cs.compression_sample_idx, COUNT(*) AS copy_count
    FROM compression_sample cs
    JOIN prepped_sample prs ON cs.compression_sample_idx = prs.compression_sample_idx
    GROUP BY cs.run_idx, cs.compression_sample_idx HAVING COUNT(*) > 1;

-- ============================================================
-- Omnibus Reconstruction Views — Shared
-- ============================================================

CREATE VIEW omnibus_contact AS
    SELECT cs.run_idx AS run_idx,
           p.project_name AS "Sample_Project",
           p.contact_email AS "Email"
    FROM project p
    JOIN input_sample ins ON ins.project_idx = p.project_idx
    JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
    GROUP BY cs.run_idx, p.project_idx
    ORDER BY p.project_idx;

CREATE VIEW omnibus_sample_context AS
    SELECT cs.run_idx AS run_idx,
        COALESCE(prs.sample_name, ins.sample_name) AS "sample_name",
        CASE st.name
            WHEN 'extraction_blank' THEN 'control blank'
            WHEN 'katharoseq_cells_positive_control' THEN 'control katharoseq'
            ELSE 'control ' || st.name
        END AS "sample_type",
        pp.qiita_id AS "primary_qiita_study",
        GROUP_CONCAT(
            CASE WHEN op.project_idx != ip.primary_project_idx
                 THEN op.qiita_id END
        ) AS "secondary_qiita_studies"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN sample_type st ON ins.sample_type_idx = st.sample_type_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    JOIN project pp ON ip.primary_project_idx = pp.project_idx
    LEFT JOIN input_plate_projects ipp ON ins.input_plate_idxx = ipp.input_plate_idxx
    LEFT JOIN project op ON ipp.project_idx = op.project_idx
    WHERE ins.project_idx IS NULL
    GROUP BY cs.run_idx, prs.prepped_sample_idx,
             COALESCE(prs.sample_name, ins.sample_name),
             st.name, pp.qiita_id;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio AbsQuant v11
-- ============================================================

CREATE VIEW omnibus_pacbio_absquant_v11_header AS
    SELECT sr.run_idx,
        lf.legacy_sheet_type AS "SheetType",
        CAST(lf.legacy_version AS TEXT) AS "SheetVersion",
        sr.investigator_name AS "Investigator Name",
        sr.experiment_name AS "Experiment Name",
        sr.run_date AS "Date",
        at.name AS "Assay",
        sr.description AS "Description"
    FROM sequencing_run sr
    JOIN assay_type at ON sr.assay_type_idx = at.assay_type_idx
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_idx = lf.legacy_format_idx;

-- Base PacBio absquant data view (no twist_adaptor_id or syndna_is_twisted).
-- v11 builds on this by adding those columns.
CREATE VIEW omnibus_pacbio_absquant_v10_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        COALESCE(prs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_idx = ip.primary_project_idx)
        ) AS "Sample_Project",
        prs.well_description AS "Well_description",
        ma.syndna_pool_mass_ng AS "mass_syndna_input_ng",
        ma.extracted_gdna_concentration AS "extracted_gdna_concentration_ng_ul",
        ip.elution_vol AS "vol_extracted_elution_ul",
        ma.syndna_pool_number AS "syndna_pool_number",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN project p ON ins.project_idx = p.project_idx
    JOIN pacbio_sample ps ON prs.prepped_sample_idx = ps.prepped_sample_idx
    LEFT JOIN metagenomic_absquant_sample ma
        ON prs.prepped_sample_idx = ma.prepped_sample_idx;

-- Adds twist_adaptor_id and syndna_is_twisted to the v10 base view.
CREATE VIEW omnibus_pacbio_absquant_v11_data AS
    SELECT v10.run_idx,
        v10."Sample_ID",
        v10."Sample_Name",
        v10."Sample_Plate",
        v10."Sample_Well",
        v10."barcode_id",
        ps.twist_adaptor_id AS "twist_adaptor_id",
        v10."Sample_Project",
        v10."Well_description",
        v10."mass_syndna_input_ng",
        v10."extracted_gdna_concentration_ng_ul",
        v10."vol_extracted_elution_ul",
        v10."syndna_pool_number",
        ps.syndna_is_twisted AS "syndna_is_twisted",
        v10."orig_name",
        v10."destination_well_384"
    FROM omnibus_pacbio_absquant_v10_data v10
    JOIN pacbio_sample ps ON v10."Sample_ID" = ps.prepped_sample_idx;

-- Adds absquant metric columns to the v11 base view.
-- Column aliases use the legacy CSV names (which differ from the DB column
-- names); the reverse mapping for population lives in constants.py
-- LEGACY_COLUMN_ALIASES.
CREATE VIEW omnibus_pacbio_absquant_v12_data AS
    SELECT v11.run_idx,
        v11."Sample_ID",
        v11."Sample_Name",
        v11."Sample_Plate",
        v11."Sample_Well",
        v11."barcode_id",
        v11."twist_adaptor_id",
        v11."Sample_Project",
        v11."Well_description",
        v11."mass_syndna_input_ng",
        v11."extracted_gdna_concentration_ng_ul",
        v11."vol_extracted_elution_ul",
        v11."syndna_pool_number",
        ma.sequenced_sample_gdna_mass_ng AS "sequenced_sample_gdna_mass_ng",
        ma.extracted_sample_mass_g AS "calc_mass_sample_aliquot_input_g",
        ma.extracted_sample_volume_ul AS "sample_volume_ul",
        ma.extracted_sample_surface_area_cm2 AS "sample_surface_area_cm2",
        v11."syndna_is_twisted",
        v11."orig_name",
        v11."destination_well_384"
    FROM omnibus_pacbio_absquant_v11_data v11
    LEFT JOIN metagenomic_absquant_sample ma
        ON v11."Sample_ID" = ma.prepped_sample_idx;

CREATE VIEW omnibus_pacbio_absquant_v11_bioinformatics AS
    SELECT DISTINCT cs.run_idx,
        p.project_name AS "Sample_Project",
        p.qiita_id AS "QiitaID",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol",
        p.experiment_design_description AS "experiment_design_description",
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_idx = cs.run_idx) AS "contains_replicates"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN project p ON ins.project_idx = p.project_idx
    GROUP BY cs.run_idx, p.project_idx, p.project_name, p.qiita_id,
             p.human_filtering, p.library_construction_protocol,
             p.experiment_design_description;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio Metag v10
-- ============================================================

-- Base PacBio metag data view (no twist_adaptor_id).
-- v11 builds on this by adding twist_adaptor_id.
CREATE VIEW omnibus_pacbio_metag_v10_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        COALESCE(prs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_idx = ip.primary_project_idx)
        ) AS "Sample_Project",
        prs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN project p ON ins.project_idx = p.project_idx
    JOIN pacbio_sample ps ON prs.prepped_sample_idx = ps.prepped_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio Metag v11
-- ============================================================

-- Adds twist_adaptor_id to the v10 base view.
CREATE VIEW omnibus_pacbio_metag_v11_data AS
    SELECT v10.run_idx,
        v10."Sample_ID",
        v10."Sample_Name",
        v10."Sample_Plate",
        v10."Sample_Well",
        v10."barcode_id",
        ps.twist_adaptor_id AS "twist_adaptor_id",
        v10."Sample_Project",
        v10."Well_description",
        v10."orig_name",
        v10."destination_well_384"
    FROM omnibus_pacbio_metag_v10_data v10
    JOIN pacbio_sample ps ON v10."Sample_ID" = ps.prepped_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — Illumina Shared
-- ============================================================

-- Header shared by all Illumina formats.
CREATE VIEW omnibus_illumina_header AS
    SELECT sr.run_idx,
        4 AS "IEMFileVersion",
        lf.legacy_sheet_type AS "SheetType",
        CAST(lf.legacy_version AS TEXT) AS "SheetVersion",
        sr.investigator_name AS "Investigator Name",
        sr.experiment_name AS "Experiment Name",
        sr.run_date AS "Date",
        'GenerateFASTQ' AS "Workflow",
        'FASTQ Only' AS "Application",
        at.name AS "Assay",
        sr.description AS "Description",
        'Default' AS "Chemistry"
    FROM sequencing_run sr
    JOIN assay_type at ON sr.assay_type_idx = at.assay_type_idx
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_idx = lf.legacy_format_idx;

-- Reads shared by all Illumina formats.
CREATE VIEW omnibus_illumina_reads AS
    SELECT sr.run_idx,
        ir.read1_length AS "read1_length",
        ir.read2_length AS "read2_length"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v90
-- ============================================================

-- Settings: ReverseComplement only.
CREATE VIEW omnibus_standard_metag_v90_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- Base Illumina Data view. Uses Sample_Well (v90 column name).
-- v0 and v101 layer on top of this view.
CREATE VIEW omnibus_standard_metag_v90_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        COALESCE(prs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ils.i7_index_id AS "I7_Index_ID",
        ils.i7_sequence AS "index",
        ils.i5_index_id AS "I5_Index_ID",
        ils.i5_sequence AS "index2",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_idx = ip.primary_project_idx)
        ) AS "Sample_Project",
        prs.well_description AS "Well_description",
        ils.lane AS "Lane"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN project p ON ins.project_idx = p.project_idx
    JOIN illumina_sample ils
        ON prs.prepped_sample_idx = ils.prepped_sample_idx;

-- Base Illumina Bioinformatics view (no contains_replicates).
-- v101 layers on top to add contains_replicates.
CREATE VIEW omnibus_standard_metag_v90_bioinformatics AS
    SELECT DISTINCT cs.run_idx,
        p.project_name AS "Sample_Project",
        p.qiita_id AS "QiitaID",
        ir.barcodes_are_rc AS "BarcodesAreRC",
        ir.forward_adapter AS "ForwardAdapter",
        ir.reverse_adapter AS "ReverseAdapter",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol",
        p.experiment_design_description AS "experiment_design_description"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN project p ON ins.project_idx = p.project_idx
    JOIN sequencing_run sr ON cs.run_idx = sr.run_idx
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx
    GROUP BY cs.run_idx, p.project_idx, p.project_name, p.qiita_id,
             ir.barcodes_are_rc, ir.forward_adapter, ir.reverse_adapter,
             p.human_filtering, p.library_construction_protocol,
             p.experiment_design_description;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v0
-- ============================================================

-- Settings: ReverseComplement + MaskShortReads.
CREATE VIEW omnibus_standard_metag_v0_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- Sources well_id_384 from compression_well (original compression position).
-- Sample_Well (prepped_well) and well_id_384 (compression_well) are equal
-- for non-replicates but differ for replicates.
CREATE VIEW omnibus_standard_metag_v0_data AS
    SELECT v90.run_idx,
        v90."Sample_ID",
        v90."Sample_Name",
        v90."Sample_Plate",
        cs.compression_well AS "well_id_384",
        v90."I7_Index_ID",
        v90."index",
        v90."I5_Index_ID",
        v90."index2",
        v90."Sample_Project",
        v90."Well_description",
        v90."Lane"
    FROM omnibus_standard_metag_v90_data v90
    JOIN prepped_sample prs ON v90."Sample_ID" = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v101
-- ============================================================

-- Settings: ReverseComplement + MaskShortReads + OverrideCycles.
CREATE VIEW omnibus_standard_metag_v101_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads",
        ir.override_cycles AS "OverrideCycles"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- Adds orig_name and destination_well_384 to the v0 base.
CREATE VIEW omnibus_standard_metag_v101_data AS
    SELECT v0.run_idx,
        v0."Sample_ID",
        v0."Sample_Name",
        v0."Sample_Plate",
        v0."well_id_384",
        v0."I7_Index_ID",
        v0."index",
        v0."I5_Index_ID",
        v0."index2",
        v0."Sample_Project",
        v0."Well_description",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384",
        v0."Lane"
    FROM omnibus_standard_metag_v0_data v0
    JOIN prepped_sample prs ON v0."Sample_ID" = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx;

-- Adds contains_replicates to the v90 base Bioinformatics.
CREATE VIEW omnibus_standard_metag_v101_bioinformatics AS
    SELECT v90.*,
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_idx = v90.run_idx) AS "contains_replicates"
    FROM omnibus_standard_metag_v90_bioinformatics v90;

-- ============================================================
-- Omnibus Reconstruction Views — AbsQuant Metag v10
-- ============================================================

-- Adds AbsQuant columns to the v101 Illumina Data base.
CREATE VIEW omnibus_abs_quant_metag_v10_data AS
    SELECT v101.run_idx,
        v101."Sample_ID",
        v101."Sample_Name",
        v101."Sample_Plate",
        v101."well_id_384",
        v101."I7_Index_ID",
        v101."index",
        v101."I5_Index_ID",
        v101."index2",
        v101."Sample_Project",
        v101."Well_description",
        ma.syndna_pool_mass_ng AS "mass_syndna_input_ng",
        ma.extracted_gdna_concentration AS "extracted_gdna_concentration_ng_ul",
        ip.elution_vol AS "vol_extracted_elution_ul",
        ma.syndna_pool_number AS "syndna_pool_number",
        v101."orig_name",
        v101."destination_well_384",
        v101."Lane"
    FROM omnibus_standard_metag_v101_data v101
    JOIN prepped_sample prs ON v101."Sample_ID" = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN metagenomic_absquant_sample ma
        ON v101."Sample_ID" = ma.prepped_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metat v10
-- ============================================================

-- Adds metatranscriptomic columns to the v0 Illumina Data base.
CREATE VIEW omnibus_standard_metat_v10_data AS
    SELECT v0.run_idx,
        v0."Sample_ID",
        v0."Sample_Name",
        v0."Sample_Plate",
        v0."well_id_384",
        v0."I7_Index_ID",
        v0."index",
        v0."I5_Index_ID",
        v0."index2",
        v0."Sample_Project",
        mt.total_rna_concentration_ng_ul AS "total_rna_concentration_ng_ul",
        ip.elution_vol AS "vol_extracted_elution_ul",
        v0."Well_description",
        v0."Lane"
    FROM omnibus_standard_metag_v0_data v0
    JOIN prepped_sample prs ON v0."Sample_ID" = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN metatranscriptomic_sample mt
        ON v0."Sample_ID" = mt.prepped_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — TellSeq Metag v10
-- ============================================================

-- TellSeq Data view: uses well_id_384 from compression_well and barcode_id
-- from tellseq_sample instead of Illumina i5/i7 index columns.
CREATE VIEW omnibus_tellseq_metag_v10_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        COALESCE(prs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        cs.compression_well AS "well_id_384",
        ts.barcode_id AS "barcode_id",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_idx = ip.primary_project_idx)
        ) AS "Sample_Project",
        prs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384",
        ts.lane AS "Lane"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN project p ON ins.project_idx = p.project_idx
    JOIN tellseq_sample ts ON prs.prepped_sample_idx = ts.prepped_sample_idx;

-- ============================================================
-- Omnibus Reconstruction Views — TellSeq AbsQuant v10
-- ============================================================

-- Adds AbsQuant columns to the TellSeq metag base.
CREATE VIEW omnibus_tellseq_absquant_v10_data AS
    SELECT v10.run_idx,
        v10."Sample_ID",
        v10."Sample_Name",
        v10."Sample_Plate",
        v10."well_id_384",
        v10."barcode_id",
        v10."Sample_Project",
        v10."Well_description",
        ma.syndna_pool_mass_ng AS "mass_syndna_input_ng",
        ma.extracted_gdna_concentration AS "extracted_gdna_concentration_ng_ul",
        ip.elution_vol AS "vol_extracted_elution_ul",
        ma.syndna_pool_number AS "syndna_pool_number",
        v10."orig_name",
        v10."destination_well_384",
        v10."Lane"
    FROM omnibus_tellseq_metag_v10_data v10
    JOIN prepped_sample prs ON v10."Sample_ID" = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idxx = ip.input_plate_idxx
    LEFT JOIN metagenomic_absquant_sample ma
        ON v10."Sample_ID" = ma.prepped_sample_idx;

-- ============================================================
-- Audit log
-- ============================================================

-- Lightweight per-column audit trail.  Update operations in
-- updates.py write one row here per modified domain row, capturing
-- the prior and new values plus an optional caller-supplied reason.
CREATE TABLE change_log (
    change_idx       INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at      TEXT NOT NULL DEFAULT (datetime('now')),
    table_name      TEXT NOT NULL,
    row_idx          INTEGER NOT NULL,
    column_name     TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT
);
