-- ============================================================
-- Run Preflight Schema
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
    delimiter           TEXT NOT NULL DEFAULT ',',
        -- field separator of the whole file; the omnibus formats are comma-
        -- delimited, the amplicon prep template is TAB-delimited.
    has_section_labels  BOOLEAN NOT NULL DEFAULT 1,
        -- 0 for a file that carries no [Section] label lines, which therefore
        -- holds exactly one written section.
    platform_idx        INTEGER REFERENCES sequencing_platform(platform_idx),
        -- the platform a file of this format describes
    default_instrument_type TEXT,
        -- instrument recorded for a run of this format when the file itself
        -- states none
    sample_kind         TEXT,
        -- which <kind>_sample table holds this format's per-sample rows, or
        -- NULL when the format has none: an amplicon run carries a single
        -- in-line Golay barcode rather than an i5/i7 pair, so it has no
        -- platform-specific sample rows. A guard test checks every row.
    sample_name_column      TEXT NOT NULL DEFAULT 'Sample_Name',
    plate_column            TEXT NOT NULL DEFAULT 'Sample_Plate',
    project_column          TEXT NOT NULL DEFAULT 'Sample_Project',
    well_description_column TEXT NOT NULL DEFAULT 'Well_description',
    well_column             TEXT NOT NULL DEFAULT 'well_id_384',
    replicates_supported    BOOLEAN NOT NULL DEFAULT 1,
        -- 0 for a format whose replicate well semantics cannot be round-tripped,
        -- which is the pre-v101 standard_metag family. Declared rather than
        -- derived from the version number, which is only comparable within one
        -- format family: amplicon v1/v2/v3 are unrelated to standard_metag
        -- v0/v90/v100 despite sorting the same way.
        -- which Data column of this format holds each fact the loader needs.
        -- The defaults are the omnibus vocabulary, which all fourteen omnibus
        -- formats share; the well column varies between them and is stated on
        -- every row. A guard test checks each name against the format's Data
        -- view.
        -- Every column below legacy_version is appended by the ADD COLUMN
        -- statements in patch (002), in the order they appear here.
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
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('pacbio_absquant', 11,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
            'Pacbio_Revio', 'pacbio', 'Sample_Well');

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
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('standard_metag', 101,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (2, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (2, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (2, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (2, 'Data',           4, 'omnibus_standard_metag_v101_data',           'tabular'),
    (2, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (2, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (2, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (2, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_metag v11
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('pacbio_metag', 11,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
            'Pacbio_Revio', 'pacbio', 'Sample_Well');

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
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('pacbio_metag', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
            'Pacbio_Revio', 'pacbio', 'Sample_Well');

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
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('pacbio_absquant', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
            'Pacbio_Revio', 'pacbio', 'Sample_Well');

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
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column, replicates_supported)
    VALUES ('standard_metag', 90,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'Sample_Well', 0);

INSERT INTO legacy_samplesheet_view VALUES
    (6, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (6, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (6, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (6, 'Data',           4, 'omnibus_standard_metag_v90_data',           'tabular'),
    (6, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (6, 'Contact',        6, 'omnibus_contact',                           'tabular');


-- Format: standard_metag v0
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column, replicates_supported)
    VALUES ('standard_metag', 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384', 0);

INSERT INTO legacy_samplesheet_view VALUES
    (7, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (7, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (7, 'Settings',       3, 'omnibus_illumina_settings',        'header_kv'),
    (7, 'Data',           4, 'omnibus_standard_metag_v0_data',            'tabular'),
    (7, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (7, 'Contact',        6, 'omnibus_contact',                           'tabular');


-- Format: standard_metag v100
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column, replicates_supported)
    VALUES ('standard_metag', 100,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384', 0);

INSERT INTO legacy_samplesheet_view VALUES
    (8, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (8, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (8, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (8, 'Data',           4, 'omnibus_standard_metag_v101_data',           'tabular'),
    (8, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (8, 'Contact',        6, 'omnibus_contact',                            'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (8, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: abs_quant_metag v10
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('abs_quant_metag', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (9, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (9, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (9, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (9, 'Data',           4, 'omnibus_abs_quant_metag_v10_data',           'tabular'),
    (9, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (9, 'Contact',        6, 'omnibus_contact',                            'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (9, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: abs_quant_metag v11
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('abs_quant_metag', 11,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (10, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (10, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (10, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (10, 'Data',           4, 'omnibus_abs_quant_metag_v10_data',           'tabular'),
    (10, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (10, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (10, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (10, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: standard_metat v10
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('standard_metat', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'illumina', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (11, 'Header',         1, 'omnibus_illumina_header',                   'header_kv'),
    (11, 'Reads',          2, 'omnibus_illumina_reads',                    'values_only'),
    (11, 'Settings',       3, 'omnibus_illumina_settings',      'header_kv'),
    (11, 'Data',           4, 'omnibus_standard_metat_v10_data',           'tabular'),
    (11, 'Bioinformatics', 5, 'omnibus_standard_metag_v90_bioinformatics', 'tabular'),
    (11, 'Contact',        6, 'omnibus_contact',                           'tabular');

-- Format: tellseq_metag v10
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('tellseq_metag', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'tellseq', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (12, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (12, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (12, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (12, 'Data',           4, 'omnibus_tellseq_metag_v10_data',             'tabular'),
    (12, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (12, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (12, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (12, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: tellseq_absquant v10
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('tellseq_absquant', 10,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', 'tellseq', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (13, 'Header',         1, 'omnibus_illumina_header',                    'header_kv'),
    (13, 'Reads',          2, 'omnibus_illumina_reads',                     'values_only'),
    (13, 'Settings',       3, 'omnibus_illumina_settings',       'header_kv'),
    (13, 'Data',           4, 'omnibus_tellseq_absquant_v10_data',          'tabular'),
    (13, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (13, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (13, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (13, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'check_contains_replicates', 'Well_description');

-- Format: pacbio_absquant v12
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, platform_idx,
         default_instrument_type, sample_kind, well_column)
    VALUES ('pacbio_absquant', 12,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
            'Pacbio_Revio', 'pacbio', 'Sample_Well');

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

-- The amplicon prep template: a flat TAB-delimited EMP 16S sheet that carries
-- no [Section] label lines, so the sheet writes exactly one section (Data). The
-- Header/Bioinformatics/Contact/SampleContext registrations describe facts the
-- sheet denormalizes across its Data columns; the loader regroups them, and they
-- are validated but never written back out. Three layouts are registered (see the
-- amplicon_v{1,2,3}_data views); header resolution takes the widest match.
INSERT INTO legacy_samplesheet_format
        (legacy_sheet_type, legacy_version, delimiter, has_section_labels,
         platform_idx, default_instrument_type, sample_kind,
         sample_name_column, plate_column, project_column,
         well_description_column, well_column)
    VALUES ('amplicon', 1, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL,
            'sample_name', 'sample_plate', 'project_name',
            'well_description', 'well_id_384'),
           ('amplicon', 2, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL,
            'sample_name', 'sample_plate', 'project_name',
            'well_description', 'well_id'),
           ('amplicon', 3, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL,
            'sample_name', 'sample_plate', 'project_name',
            'well_description', 'well_id_384');

INSERT INTO legacy_samplesheet_view VALUES
    (15, 'Header',         1, 'amplicon_header',         'header_kv'),
    (15, 'Data',           2, 'amplicon_v1_data',        'tabular'),
    (15, 'Bioinformatics', 3, 'amplicon_bioinformatics', 'tabular'),
    (15, 'Contact',        4, 'omnibus_contact',         'tabular'),
    (15, 'SampleContext',  5, 'amplicon_sample_context', 'tabular'),
    (16, 'Header',         1, 'amplicon_header',         'header_kv'),
    (16, 'Data',           2, 'amplicon_v2_data',        'tabular'),
    (16, 'Bioinformatics', 3, 'amplicon_bioinformatics', 'tabular'),
    (16, 'Contact',        4, 'omnibus_contact',         'tabular'),
    (16, 'SampleContext',  5, 'amplicon_sample_context', 'tabular'),
    (17, 'Header',         1, 'amplicon_header',         'header_kv'),
    (17, 'Data',           2, 'amplicon_v3_data',        'tabular'),
    (17, 'Bioinformatics', 3, 'amplicon_bioinformatics', 'tabular'),
    (17, 'Contact',        4, 'omnibus_contact',         'tabular'),
    (17, 'SampleContext',  5, 'amplicon_sample_context', 'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (15, 'Data', 'katharoseq', 'Kathseq_RackID,number_of_cells',
     'check_contains_katharoseq', NULL),
    (16, 'Data', 'katharoseq', 'Kathseq_RackID,number_of_cells',
     'check_contains_katharoseq', NULL),
    (17, 'Data', 'katharoseq', 'Kathseq_RackID,number_of_cells',
     'check_contains_katharoseq', NULL);

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

-- A project is identified by external_project_id, bioproject_accession, or both
CREATE TABLE project (
    project_idx                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name                    TEXT NOT NULL UNIQUE,
    external_project_id             TEXT,
    contact_email                   TEXT,
    human_filtering                 BOOLEAN NOT NULL DEFAULT 1,
    library_construction_protocol   TEXT NOT NULL,
    experiment_design_description   TEXT NOT NULL,
    bioproject_accession             TEXT,
        -- NCBI BioProject accession
    CHECK (external_project_id IS NOT NULL OR bioproject_accession IS NOT NULL)
);

CREATE TABLE input_plate (
    input_plate_idx      INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_name          TEXT NOT NULL,
    primary_project_idx  INTEGER NOT NULL REFERENCES project(project_idx),
    elution_vol         REAL,
    primer_plate        TEXT,
    plating             TEXT,
    extractionkit_lot   TEXT,
    extraction_robot    TEXT,
    platemap_generation_date TEXT,
    plate_contents_description TEXT
        -- what this plate holds, at plate grain. Distinct from
        -- project.experiment_design_description, which stays authoritative at
        -- project grain: plates and projects are many-to-many, so plate is a
        -- crossing axis rather than a finer one.
);

-- A sample is identified by sample_name, biosample_accession, or both
CREATE TABLE input_sample (
    input_sample_idx     INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_name         TEXT,
    input_plate_idx      INTEGER NOT NULL REFERENCES input_plate(input_plate_idx),
    well                TEXT,
    project_idx          INTEGER REFERENCES project(project_idx),
        -- NULL for controls; controls inherit project via input_plate
    sample_type_idx      INTEGER NOT NULL REFERENCES sample_type(sample_type_idx),
    biosample_accession TEXT,
        -- NCBI BioSample accession
    do_not_use          BOOLEAN NOT NULL DEFAULT 0,
        -- TRUE excludes the sample (and all its preps) from default fetches
    matrix_tube_id      TEXT,
        -- physical matrix/tube barcode, nullable; the amplicon prep template's
        -- TubeCode. Per-sample, so it lives here rather than on katharoseq_sample.
        -- Appended last to match the ADD COLUMN patch (002) that brings v0 forward.
    CHECK (sample_name IS NOT NULL OR biosample_accession IS NOT NULL)
);

CREATE TABLE processing_run (
    run_idx              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_name     TEXT NOT NULL,
    run_date            TEXT NOT NULL,
    investigator_name   TEXT NOT NULL DEFAULT '',
    instrument_type           TEXT NOT NULL,
    assay_type_idx       INTEGER NOT NULL REFERENCES assay_type(assay_type_idx),
    platform_idx         INTEGER NOT NULL REFERENCES sequencing_platform(platform_idx),
    compression_plate_name TEXT,
    description         TEXT DEFAULT '',
    legacy_format_idx    INTEGER REFERENCES legacy_samplesheet_format(legacy_format_idx),
        -- NULL for native DB-originated runs; non-NULL for ingested legacy files
    external_run_id            TEXT
);


CREATE TABLE compression_sample (
    compression_sample_idx    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_idx          INTEGER NOT NULL REFERENCES processing_run(run_idx),
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
    do_not_use              BOOLEAN,
        -- per-replicate override: 1 = exclude this replicate;
        -- NULL = not flagged (inherit the input_sample flag).
        -- Can only add exclusion: the input_sample flag is a hard floor.
    well_description        TEXT
);

-- ============================================================
-- Platform-Specific Run Configuration
-- ============================================================

CREATE TABLE illumina_run (
    run_idx              INTEGER PRIMARY KEY REFERENCES processing_run(run_idx),
    read1_length        INTEGER,
    read2_length        INTEGER,
        -- both NULL when the source document records no run configuration,
        -- as the flat amplicon prep template does not
    reverse_complement  BOOLEAN,
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
    syndna_is_twisted       BOOLEAN,
    smrt_cell_well_sample_id TEXT CHECK (smrt_cell_well_sample_id GLOB '[12]_[A-D]01'),
        -- On-disk SMRT Cell well folder name, e.g. 1_B01. Revio wells are
        -- column 01 only (A01-D01), so _02 and higher are invalid per page 28
        -- of SMRT-Link-v25.2-user-guide.pdf.
    movie_context_id        TEXT
        -- Full movie context, e.g. m84137_260702_104358_s3. Its format varies
        -- across instrument and software, so it is intentionally unconstrained.
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

-- One run per database: a run-preflight SQLite file represents one
-- run only.  Other invariants (lane uniformity, the unambiguity of
-- platform-table surrogate PK as the per-row identifier) depend on
-- this property holding.

CREATE TRIGGER one_run_per_db
BEFORE INSERT ON processing_run
WHEN (SELECT COUNT(*) FROM processing_run) > 0
BEGIN
    SELECT RAISE(ABORT,
        'a run-preflight database may contain at most one processing_run');
END;

-- ============================================================
-- Workflow-Specific Run Configuration
-- ============================================================

CREATE TABLE amplicon_run (
    run_idx              INTEGER PRIMARY KEY REFERENCES processing_run(run_idx),
    primer              TEXT NOT NULL,
    linker              TEXT NOT NULL,
    target_gene         TEXT NOT NULL,
    target_subfragment  TEXT NOT NULL,
    pcr_primers         TEXT NOT NULL,
    sequencing_meth     TEXT NOT NULL,
    -- whether the Golay barcodes are stored reverse-complemented; the prep
    -- template does not state this, so it is inferred from the primer at ingest
    -- (see _barcodes_are_rc_for_primer), mirroring illumina_run.barcodes_are_rc.
    barcodes_are_rc     BOOLEAN NOT NULL
);

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

CREATE TABLE amplicon_sample (
    prepped_sample_idx      INTEGER PRIMARY KEY
        REFERENCES prepped_sample(prepped_sample_idx),
    barcode                 TEXT NOT NULL
        -- the in-line Golay barcode SEQUENCE, added to the sample during
        -- amplicon PCR; controls carry one too. Sequencing platform-independent.
);

CREATE TABLE katharoseq_sample (
    input_sample_idx         INTEGER PRIMARY KEY
        REFERENCES input_sample(input_sample_idx),
    rack_id                 TEXT,
    number_of_cells         REAL
        -- fractional counts occur in serial dilutions (e.g. 38.4, 7.68), so
        -- this is REAL rather than INTEGER
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
    SELECT DISTINCT input_plate_idx, project_idx
        FROM input_sample WHERE project_idx IS NOT NULL
    UNION
    SELECT input_plate_idx, primary_project_idx AS project_idx
        FROM input_plate;

-- Maps each control sample to every project on its plate
CREATE VIEW control_project_associations AS
    SELECT s.input_sample_idx, s.sample_name, st.name AS control_type,
           pp.project_idx, p.project_name, p.external_project_id
    FROM input_sample s
    JOIN sample_type st ON s.sample_type_idx = st.sample_type_idx
    JOIN input_plate_projects pp ON s.input_plate_idx = pp.input_plate_idx
    JOIN project p ON pp.project_idx = p.project_idx
    WHERE s.project_idx IS NULL;

-- Detects replicated samples (a compression_sample with multiple prepped_wells)
CREATE VIEW replicated_samples AS
    SELECT cs.run_idx, cs.compression_sample_idx, COUNT(*) AS copy_count
    FROM compression_sample cs
    JOIN prepped_sample prs ON cs.compression_sample_idx = prs.compression_sample_idx
    GROUP BY cs.run_idx, cs.compression_sample_idx HAVING COUNT(*) > 1;

-- Resolves each prepped_sample to its effective project: the input_sample's
-- project_idx when set, else the plate's primary_project_idx (controls).
-- Provides one shared definition reused by run_illumina_sample and by the
-- omnibus reconstruction Data views.
CREATE VIEW prepped_sample_project AS
    SELECT prs.prepped_sample_idx,
           COALESCE(ins.project_idx, ip.primary_project_idx) AS project_idx,
           COALESCE(p.project_name,
               (SELECT p2.project_name FROM project p2
                WHERE p2.project_idx = ip.primary_project_idx)
           ) AS project_name
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    LEFT JOIN project p ON ins.project_idx = p.project_idx;

-- Resolves each prepped_sample to its effective sample_name and effective
-- do_not_use flag. sample_name is prepped_sample.sample_name when populated
-- (replicates) else input_sample.sample_name. do_not_use is the hard-floor
-- OR of the input flag and the per-prep override (input excluded => excluded
-- regardless of the prep value). Defined once so the resolution is shared
-- rather than duplicated across the views that need it.
CREATE VIEW prepped_sample_name AS
    SELECT prs.prepped_sample_idx,
           COALESCE(prs.sample_name, ins.sample_name) AS sample_name,
           (ins.do_not_use = 1 OR COALESCE(prs.do_not_use, 0) = 1) AS do_not_use
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx;

-- Joins illumina_sample to its scoping run and input_sample so callers can
-- filter by run_idx without re-deriving the prepped/compression chain.
CREATE VIEW run_illumina_sample AS
    SELECT
        ils.illumina_sample_idx,
        ils.prepped_sample_idx,
        ils.lane,
        ils.i7_index_id, ils.i7_sequence,
        ils.i5_index_id, ils.i5_sequence,
        cs.run_idx,
        cs.input_sample_idx,
        psn.sample_name,
        psn.do_not_use,
        psp.project_name
    FROM illumina_sample ils
    JOIN prepped_sample prs ON ils.prepped_sample_idx = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN prepped_sample_name psn ON ils.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON ils.prepped_sample_idx = psp.prepped_sample_idx;

-- Joins pacbio_sample to its scoping run and input_sample, mirroring
-- run_illumina_sample so callers can filter by run_idx without re-deriving
-- the prepped/compression chain.
CREATE VIEW run_pacbio_sample AS
    SELECT
        ps.pacbio_sample_idx,
        ps.prepped_sample_idx,
        ps.barcode_id,
        ps.twist_adaptor_id,
        ps.syndna_is_twisted,
        ps.smrt_cell_well_sample_id,
        ps.movie_context_id,
        cs.run_idx,
        cs.input_sample_idx,
        psn.sample_name,
        psn.do_not_use,
        psp.project_name
    FROM pacbio_sample ps
    JOIN prepped_sample prs ON ps.prepped_sample_idx = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN prepped_sample_name psn ON ps.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON ps.prepped_sample_idx = psp.prepped_sample_idx;

-- Joins amplicon_sample to its scoping run and input_sample, mirroring
-- run_pacbio_sample so callers can filter by run_idx without re-deriving the
-- prepped/compression chain. amplicon_sample has no surrogate key, so
-- prepped_sample_idx is the per-sample handle.
CREATE VIEW run_amplicon_sample AS
    SELECT
        a.prepped_sample_idx,
        a.barcode,
        ar.barcodes_are_rc,
        cs.run_idx,
        cs.input_sample_idx,
        psn.sample_name,
        psn.do_not_use,
        psp.project_name
    FROM amplicon_sample a
    JOIN prepped_sample prs ON a.prepped_sample_idx = prs.prepped_sample_idx
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN amplicon_run ar ON cs.run_idx = ar.run_idx
    JOIN prepped_sample_name psn ON a.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON a.prepped_sample_idx = psp.prepped_sample_idx;

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
        psn.sample_name AS "sample_name",
        CASE st.name
            WHEN 'extraction_blank' THEN 'control blank'
            WHEN 'katharoseq_cells_positive_control' THEN 'control katharoseq'
            ELSE 'control ' || st.name
        END AS "sample_type",
        pp.external_project_id AS "primary_qiita_study",
        GROUP_CONCAT(
            CASE WHEN op.project_idx != ip.primary_project_idx
                 THEN op.external_project_id END
        ) AS "secondary_qiita_studies"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN sample_type st ON ins.sample_type_idx = st.sample_type_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN project pp ON ip.primary_project_idx = pp.project_idx
    LEFT JOIN input_plate_projects ipp ON ins.input_plate_idx = ipp.input_plate_idx
    LEFT JOIN project op ON ipp.project_idx = op.project_idx
    WHERE ins.project_idx IS NULL
    GROUP BY cs.run_idx, prs.prepped_sample_idx,
             psn.sample_name,
             st.name, pp.external_project_id;

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
    FROM processing_run sr
    JOIN assay_type at ON sr.assay_type_idx = at.assay_type_idx
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_idx = lf.legacy_format_idx;

-- Base PacBio absquant data view (no twist_adaptor_id or syndna_is_twisted).
-- v11 builds on this by adding those columns.
CREATE VIEW omnibus_pacbio_absquant_v10_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        psn.sample_name AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        psp.project_name AS "Sample_Project",
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
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON prs.prepped_sample_idx = psp.prepped_sample_idx
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
        p.external_project_id AS "QiitaID",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol",
        p.experiment_design_description AS "experiment_design_description",
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_idx = cs.run_idx) AS "contains_replicates"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN project p ON ins.project_idx = p.project_idx
    GROUP BY cs.run_idx, p.project_idx, p.project_name, p.external_project_id,
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
        psn.sample_name AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        psp.project_name AS "Sample_Project",
        prs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON prs.prepped_sample_idx = psp.prepped_sample_idx
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
    FROM processing_run sr
    JOIN assay_type at ON sr.assay_type_idx = at.assay_type_idx
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_idx = lf.legacy_format_idx;

-- Reads shared by all Illumina formats.
CREATE VIEW omnibus_illumina_reads AS
    SELECT sr.run_idx,
        ir.read1_length AS "read1_length",
        ir.read2_length AS "read2_length"
    FROM processing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- Settings shared by all Illumina formats.
-- Keys whose DB value is NULL are dropped on emit by the reconstructor,
-- so a format that historically populated only a subset still round-trips
-- cleanly while gaining the ability to read and emit any of the three.
CREATE VIEW omnibus_illumina_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads",
        ir.override_cycles AS "OverrideCycles"
    FROM processing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v90
-- ============================================================

-- Base Illumina Data view. Uses Sample_Well (v90 column name).
-- v0 and v101 layer on top of this view.
CREATE VIEW omnibus_standard_metag_v90_data AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx AS "Sample_ID",
        psn.sample_name AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        prs.prepped_well AS "Sample_Well",
        ils.i7_index_id AS "I7_Index_ID",
        ils.i7_sequence AS "index",
        ils.i5_index_id AS "I5_Index_ID",
        ils.i5_sequence AS "index2",
        psp.project_name AS "Sample_Project",
        prs.well_description AS "Well_description",
        ils.lane AS "Lane"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON prs.prepped_sample_idx = psp.prepped_sample_idx
    JOIN illumina_sample ils
        ON prs.prepped_sample_idx = ils.prepped_sample_idx;

-- Base Illumina Bioinformatics view (no contains_replicates).
-- v101 layers on top to add contains_replicates.
CREATE VIEW omnibus_standard_metag_v90_bioinformatics AS
    SELECT DISTINCT cs.run_idx,
        p.project_name AS "Sample_Project",
        p.external_project_id AS "QiitaID",
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
    JOIN processing_run sr ON cs.run_idx = sr.run_idx
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx
    GROUP BY cs.run_idx, p.project_idx, p.project_name, p.external_project_id,
             ir.barcodes_are_rc, ir.forward_adapter, ir.reverse_adapter,
             p.human_filtering, p.library_construction_protocol,
             p.experiment_design_description;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v0
-- ============================================================

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
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
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
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
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
        psn.sample_name AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        cs.compression_well AS "well_id_384",
        ts.barcode_id AS "barcode_id",
        psp.project_name AS "Sample_Project",
        prs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        prs.prepped_well AS "destination_well_384",
        ts.lane AS "Lane"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON prs.prepped_sample_idx = psp.prepped_sample_idx
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
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    LEFT JOIN metagenomic_absquant_sample ma
        ON v10."Sample_ID" = ma.prepped_sample_idx;

-- ============================================================
-- Amplicon Prep-Template Reconstruction Views
-- ============================================================

-- Shared base carrying every typed prep-template column with the joins resolved
-- once; the amplicon_v1_data view projects the subset the sheet spells.
CREATE VIEW amplicon_data_base AS
    SELECT cs.run_idx,
        prs.prepped_sample_idx,
        psn.sample_name AS "sample_name",
        a.barcode AS "barcode",
        ar.primer AS "primer",
        ar.linker AS "linker",
        ar.pcr_primers AS "pcr_primers",
        ar.sequencing_meth AS "sequencing_meth",
        ar.target_gene AS "target_gene",
        ar.target_subfragment AS "target_subfragment",
        ip.primer_plate AS "primer_plate",
        ip.plating AS "plating",
        ip.extractionkit_lot AS "extractionkit_lot",
        ip.extraction_robot AS "extraction_robot",
        ip.plate_name AS "sample_plate",
        psp.project_name AS "project_name",
        ins.sample_name AS "orig_name",
        prs.well_description AS "well_description",
        p.library_construction_protocol AS "library_construction_protocol",
        cs.compression_well AS "well_id_384",
        cs.compression_well AS "well_id",
        ins.well AS "well_id_96",
        ip.plate_contents_description AS "experiment_design_description",
        sr.instrument_type AS "instrument_model",
        sr.external_run_id AS "runid",
        ins.matrix_tube_id AS "TubeCode",
        CASE st.name
            WHEN 'extraction_blank' THEN 'negative_control'
            WHEN 'katharoseq_cells_positive_control' THEN 'positive_control'
            ELSE ''
        END AS "control_description",
        ip.platemap_generation_date AS "platemap_generation_date",
        ip.elution_vol AS "vol_extracted_elution_ul",
        ks.rack_id AS "Kathseq_RackID",
        ks.number_of_cells AS "number_of_cells"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN input_plate ip ON ins.input_plate_idx = ip.input_plate_idx
    JOIN sample_type st ON ins.sample_type_idx = st.sample_type_idx
    JOIN processing_run sr ON cs.run_idx = sr.run_idx
    JOIN amplicon_run ar ON sr.run_idx = ar.run_idx
    JOIN amplicon_sample a ON prs.prepped_sample_idx = a.prepped_sample_idx
    JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx
    JOIN prepped_sample_project psp ON prs.prepped_sample_idx = psp.prepped_sample_idx
    JOIN project p ON psp.project_idx = p.project_idx
    LEFT JOIN katharoseq_sample ks ON ins.input_sample_idx = ks.input_sample_idx;

-- The amplicon prep template is free-form: real studies vary the column set.
-- Three registered layouts span what we've seen; header resolution takes the
-- widest match. Kathseq_RackID / number_of_cells are optional on all three (see
-- legacy_samplesheet_optional_columns); unrecognized columns round-trip verbatim.

-- v1: wide — tube + plate tier, 96/384 wells, and control_description.
CREATE VIEW amplicon_v1_data AS
    SELECT run_idx, prepped_sample_idx, "sample_name", "barcode", "primer", "linker", "pcr_primers",
        "sequencing_meth", "target_gene", "target_subfragment", "primer_plate",
        "plating", "extractionkit_lot", "extraction_robot", "sample_plate",
        "project_name", "orig_name", "well_description",
        "library_construction_protocol", "well_id_384", "well_id_96",
        "experiment_design_description", "instrument_model", "runid",
        "TubeCode", "control_description", "platemap_generation_date",
        "vol_extracted_elution_ul", "Kathseq_RackID", "number_of_cells"
    FROM amplicon_data_base;

-- v2: narrow — spells the 384-well `well_id` and omits the tube/plate-detail
-- tier (no TubeCode / platemap / elution volume / control_description).
CREATE VIEW amplicon_v2_data AS
    SELECT run_idx, prepped_sample_idx, "sample_name", "barcode", "primer", "linker", "pcr_primers",
        "sequencing_meth", "target_gene", "target_subfragment", "primer_plate",
        "plating", "extractionkit_lot", "extraction_robot", "sample_plate",
        "project_name", "orig_name", "well_description",
        "library_construction_protocol", "well_id", "well_id_96",
        "experiment_design_description", "instrument_model", "runid",
        "Kathseq_RackID", "number_of_cells"
    FROM amplicon_data_base;

-- v3: wide without control_description (some studies omit that column).
CREATE VIEW amplicon_v3_data AS
    SELECT run_idx, prepped_sample_idx, "sample_name", "barcode", "primer", "linker", "pcr_primers",
        "sequencing_meth", "target_gene", "target_subfragment", "primer_plate",
        "plating", "extractionkit_lot", "extraction_robot", "sample_plate",
        "project_name", "orig_name", "well_description",
        "library_construction_protocol", "well_id_384", "well_id_96",
        "experiment_design_description", "instrument_model", "runid",
        "TubeCode", "platemap_generation_date",
        "vol_extracted_elution_ul", "Kathseq_RackID", "number_of_cells"
    FROM amplicon_data_base;

-- The prep template states no run configuration, so the header carries only
-- what identifies the format and the run itself.
CREATE VIEW amplicon_header AS
    SELECT sr.run_idx,
        lf.legacy_sheet_type AS "SheetType",
        CAST(lf.legacy_version AS TEXT) AS "SheetVersion",
        at.name AS "Assay",
        sr.experiment_name AS "Experiment Name",
        sr.run_date AS "Date"
    FROM processing_run sr
    JOIN assay_type at ON sr.assay_type_idx = at.assay_type_idx
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_idx = lf.legacy_format_idx;

-- Project-grain facts. experiment_design_description is absent by design: in
-- these sheets it varies per plate within one project, so it is carried on
-- input_plate.plate_contents_description instead.
CREATE VIEW amplicon_bioinformatics AS
    SELECT DISTINCT cs.run_idx,
        p.project_name AS "Sample_Project",
        p.external_project_id AS "QiitaID",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol"
    FROM prepped_sample prs
    JOIN compression_sample cs ON prs.compression_sample_idx = cs.compression_sample_idx
    JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
    JOIN project p ON ins.project_idx = p.project_idx
    GROUP BY cs.run_idx, p.project_idx, p.project_name, p.external_project_id,
             p.human_filtering, p.library_construction_protocol;

-- The prep template records controls but no study associations; this projects
-- the shared view down to what such a sheet can state. It is validated on load
-- and never written back out.
CREATE VIEW amplicon_sample_context AS
    SELECT run_idx, "sample_name", "sample_type" FROM omnibus_sample_context;

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
