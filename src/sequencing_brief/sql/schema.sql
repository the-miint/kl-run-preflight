-- ============================================================
-- Sequencing Sample Sheet Schema v3
-- Validated against: pacbio_absquant v11, standard_metag v101
-- ============================================================

PRAGMA foreign_keys = ON;

-- ============================================================
-- Reference Tables
-- ============================================================

CREATE TABLE assay_type (
    assay_type_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE
);

CREATE TABLE sequencing_platform (
    platform_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE
);

CREATE TABLE sample_type (
    sample_type_id  INTEGER PRIMARY KEY AUTOINCREMENT,
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
    legacy_format_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_sheet_type   TEXT NOT NULL,
    legacy_version      INTEGER NOT NULL,
    UNIQUE(legacy_sheet_type, legacy_version)
);

CREATE TABLE legacy_samplesheet_view (
    legacy_format_id    INTEGER NOT NULL
        REFERENCES legacy_samplesheet_format(legacy_format_id),
    section_name        TEXT NOT NULL,
    section_order       INTEGER NOT NULL,
    view_name           TEXT NOT NULL,
    section_format      TEXT NOT NULL DEFAULT 'tabular'
        REFERENCES legacy_section_format(format_name),
    PRIMARY KEY (legacy_format_id, section_name)
);

CREATE TABLE legacy_samplesheet_optional_columns (
    legacy_format_id    INTEGER NOT NULL
        REFERENCES legacy_samplesheet_format(legacy_format_id),
    section_name        TEXT NOT NULL,
    group_name          TEXT NOT NULL,
    column_names        TEXT NOT NULL,       -- comma-separated column names
    check_function      TEXT NOT NULL,       -- function name to determine presence
    insert_after        TEXT,                -- column after which to insert (NULL = append)
    PRIMARY KEY (legacy_format_id, section_name, group_name)
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
     'contains_replicates', 'Well_description'),
    (1, 'Data', 'katharoseq',
     'Kathseq_RackID,TubeCode,katharo_description,number_of_cells,platemap_generation_date,project_abbreviation,well_id_96',
     'contains_katharoseq', 'Well_description');

-- Format: standard_metag v101
INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('standard_metag', 101);

INSERT INTO legacy_samplesheet_view VALUES
    (2, 'Header',         1, 'omnibus_standard_metag_v101_header',         'header_kv'),
    (2, 'Reads',          2, 'omnibus_standard_metag_v101_reads',          'values_only'),
    (2, 'Settings',       3, 'omnibus_standard_metag_v101_settings',       'header_kv'),
    (2, 'Data',           4, 'omnibus_standard_metag_v101_data',           'tabular'),
    (2, 'Bioinformatics', 5, 'omnibus_standard_metag_v101_bioinformatics', 'tabular'),
    (2, 'Contact',        6, 'omnibus_contact',                            'tabular'),
    (2, 'SampleContext',  7, 'omnibus_sample_context',                     'tabular');

INSERT INTO legacy_samplesheet_optional_columns VALUES
    (2, 'Data', 'replicates',
     'orig_name,destination_well_384',
     'contains_replicates', 'Well_description');

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
     'contains_replicates', 'Well_description');

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
     'contains_replicates', 'Well_description');

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
     'contains_replicates', 'Well_description');

-- ============================================================
-- Core Domain Tables
-- ============================================================

CREATE TABLE project (
    project_id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name                    TEXT NOT NULL UNIQUE,
    qiita_id                        TEXT NOT NULL,
    contact_email                   TEXT,
    human_filtering                 BOOLEAN NOT NULL DEFAULT 1,
    library_construction_protocol   TEXT NOT NULL,
    experiment_design_description   TEXT NOT NULL
);

CREATE TABLE input_plate (
    input_plate_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_name          TEXT NOT NULL,
    primary_project_id  INTEGER NOT NULL REFERENCES project(project_id),
    elution_vol         REAL
);

CREATE TABLE input_sample (
    input_sample_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_name         TEXT NOT NULL,
    input_plate_id      INTEGER NOT NULL REFERENCES input_plate(input_plate_id),
    well                TEXT NOT NULL,
    project_id          INTEGER REFERENCES project(project_id),
        -- NULL for controls; controls inherit project via input_plate
    sample_type_id      INTEGER NOT NULL REFERENCES sample_type(sample_type_id)
);

CREATE TABLE sequencing_run (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_name     TEXT NOT NULL,
    run_date            TEXT NOT NULL,
    investigator_name   TEXT NOT NULL DEFAULT '',
    sequencer           TEXT NOT NULL,
    assay_type_id       INTEGER NOT NULL REFERENCES assay_type(assay_type_id),
    platform_id         INTEGER NOT NULL REFERENCES sequencing_platform(platform_id),
    compression_plate_name TEXT,
    description         TEXT DEFAULT '',
    legacy_format_id    INTEGER
        REFERENCES legacy_samplesheet_format(legacy_format_id)
        -- NULL for native DB-originated runs; non-NULL for ingested legacy files
);

CREATE TABLE compression_sample (
    compression_sample_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES sequencing_run(run_id),
    input_sample_id         INTEGER NOT NULL REFERENCES input_sample(input_sample_id),
    compression_well        TEXT NOT NULL,
    sample_name             TEXT,
        -- NULL when same as input_sample.sample_name;
        -- populated for replicates (e.g. "orig_name.dest_well")
    well_description        TEXT
);

-- ============================================================
-- Platform-Specific Run Configuration
-- ============================================================

CREATE TABLE illumina_run (
    run_id              INTEGER PRIMARY KEY REFERENCES sequencing_run(run_id),
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

CREATE TABLE illumina_sample (
    compression_sample_id   INTEGER PRIMARY KEY
        REFERENCES compression_sample(compression_sample_id),
    i7_index_id             TEXT NOT NULL,
    i7_sequence             TEXT NOT NULL,
    i5_index_id             TEXT NOT NULL,
    i5_sequence             TEXT NOT NULL
);

CREATE TABLE tellseq_sample (
    compression_sample_id   INTEGER PRIMARY KEY
        REFERENCES compression_sample(compression_sample_id),
    barcode_id              TEXT NOT NULL
);

CREATE TABLE pacbio_sample (
    compression_sample_id   INTEGER PRIMARY KEY
        REFERENCES compression_sample(compression_sample_id),
    barcode_id              TEXT NOT NULL,
    twist_adaptor_id        TEXT,
    syndna_is_twisted       BOOLEAN
);

-- ============================================================
-- Workflow-Specific Sample Tables
-- ============================================================

CREATE TABLE metagenomic_absquant_sample (
    compression_sample_id       INTEGER PRIMARY KEY
        REFERENCES compression_sample(compression_sample_id),
    syndna_pool_mass_ng         REAL,
    extracted_gdna_concentration REAL,
    syndna_pool_number          TEXT
);

CREATE TABLE metatranscriptomic_sample (
    compression_sample_id           INTEGER PRIMARY KEY
        REFERENCES compression_sample(compression_sample_id),
    total_rna_concentration_ng_ul   REAL
);

CREATE TABLE katharoseq_sample (
    input_sample_id         INTEGER PRIMARY KEY
        REFERENCES input_sample(input_sample_id),
    rack_id                 TEXT,
    tube_code               TEXT,
    number_of_cells         INTEGER
);

-- ============================================================
-- Utility Views
-- ============================================================

-- All projects associated with a plate (includes primary + sample-level)
CREATE VIEW input_plate_projects AS
    SELECT DISTINCT input_plate_id, project_id
        FROM input_sample WHERE project_id IS NOT NULL
    UNION
    SELECT input_plate_id, primary_project_id AS project_id
        FROM input_plate;

-- Maps each control sample to every project on its plate
CREATE VIEW control_project_associations AS
    SELECT s.input_sample_id, s.sample_name, st.name AS control_type,
           pp.project_id, p.project_name, p.qiita_id
    FROM input_sample s
    JOIN sample_type st ON s.sample_type_id = st.sample_type_id
    JOIN input_plate_projects pp ON s.input_plate_id = pp.input_plate_id
    JOIN project p ON pp.project_id = p.project_id
    WHERE s.project_id IS NULL;

-- Detects replicated samples (same input_sample on multiple compression wells)
CREATE VIEW replicated_samples AS
    SELECT run_id, input_sample_id, COUNT(*) AS copy_count
    FROM compression_sample
    GROUP BY run_id, input_sample_id HAVING COUNT(*) > 1;

-- ============================================================
-- Omnibus Reconstruction Views — Shared
-- ============================================================

CREATE VIEW omnibus_contact AS
    SELECT p.project_name AS "Sample_Project",
           p.contact_email AS "Email"
    FROM project p;

CREATE VIEW omnibus_sample_context AS
    SELECT COALESCE(cs.sample_name, ins.sample_name) AS "sample_name",
        CASE st.name
            WHEN 'extraction_blank' THEN 'control blank'
            WHEN 'katharoseq_cells_positive_control' THEN 'control katharoseq'
            ELSE 'control ' || st.name
        END AS "sample_type",
        pp.qiita_id AS "primary_qiita_study",
        GROUP_CONCAT(
            CASE WHEN op.project_id != ip.primary_project_id
                 THEN op.qiita_id END
        ) AS "secondary_qiita_studies"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN sample_type st ON ins.sample_type_id = st.sample_type_id
    JOIN input_plate ip ON ins.input_plate_id = ip.input_plate_id
    JOIN project pp ON ip.primary_project_id = pp.project_id
    LEFT JOIN input_plate_projects ipp ON ins.input_plate_id = ipp.input_plate_id
    LEFT JOIN project op ON ipp.project_id = op.project_id
    WHERE ins.project_id IS NULL
    GROUP BY cs.compression_sample_id,
             COALESCE(cs.sample_name, ins.sample_name),
             st.name, pp.qiita_id;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio AbsQuant v11
-- ============================================================

CREATE VIEW omnibus_pacbio_absquant_v11_header AS
    SELECT sr.run_id,
        lf.legacy_sheet_type AS "SheetType",
        CAST(lf.legacy_version AS TEXT) AS "SheetVersion",
        sr.investigator_name AS "Investigator Name",
        sr.experiment_name AS "Experiment Name",
        sr.run_date AS "Date",
        at.name AS "Assay",
        sr.description AS "Description"
    FROM sequencing_run sr
    JOIN assay_type at ON sr.assay_type_id = at.assay_type_id
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_id = lf.legacy_format_id;

-- Base PacBio absquant data view (no twist_adaptor_id or syndna_is_twisted).
-- v11 builds on this by adding those columns.
CREATE VIEW omnibus_pacbio_absquant_v10_data AS
    SELECT cs.run_id,
        cs.compression_sample_id AS "Sample_ID",
        COALESCE(cs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        ins.well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_id = ip.primary_project_id)
        ) AS "Sample_Project",
        cs.well_description AS "Well_description",
        ma.syndna_pool_mass_ng AS "mass_syndna_input_ng",
        ma.extracted_gdna_concentration AS "extracted_gdna_concentration_ng_ul",
        ip.elution_vol AS "vol_extracted_elution_ul",
        ma.syndna_pool_number AS "syndna_pool_number",
        ins.sample_name AS "orig_name",
        cs.compression_well AS "destination_well_384",
        1 AS "Lane"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN input_plate ip ON ins.input_plate_id = ip.input_plate_id
    LEFT JOIN project p ON ins.project_id = p.project_id
    JOIN pacbio_sample ps ON cs.compression_sample_id = ps.compression_sample_id
    LEFT JOIN metagenomic_absquant_sample ma
        ON cs.compression_sample_id = ma.compression_sample_id;

-- Adds twist_adaptor_id and syndna_is_twisted to the v10 base view.
CREATE VIEW omnibus_pacbio_absquant_v11_data AS
    SELECT v10.run_id,
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
        v10."destination_well_384",
        v10."Lane"
    FROM omnibus_pacbio_absquant_v10_data v10
    JOIN pacbio_sample ps ON v10."Sample_ID" = ps.compression_sample_id;

CREATE VIEW omnibus_pacbio_absquant_v11_bioinformatics AS
    SELECT DISTINCT cs.run_id,
        p.project_name AS "Sample_Project",
        p.qiita_id AS "QiitaID",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol",
        p.experiment_design_description AS "experiment_design_description",
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_id = cs.run_id) AS "contains_replicates"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN project p ON ins.project_id = p.project_id
    GROUP BY cs.run_id, p.project_id, p.project_name, p.qiita_id,
             p.human_filtering, p.library_construction_protocol,
             p.experiment_design_description;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio Metag v10
-- ============================================================

-- Base PacBio metag data view (no twist_adaptor_id).
-- v11 builds on this by adding twist_adaptor_id.
CREATE VIEW omnibus_pacbio_metag_v10_data AS
    SELECT cs.run_id,
        cs.compression_sample_id AS "Sample_ID",
        COALESCE(cs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        ins.well AS "Sample_Well",
        ps.barcode_id AS "barcode_id",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_id = ip.primary_project_id)
        ) AS "Sample_Project",
        cs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        cs.compression_well AS "destination_well_384",
        1 AS "Lane"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN input_plate ip ON ins.input_plate_id = ip.input_plate_id
    LEFT JOIN project p ON ins.project_id = p.project_id
    JOIN pacbio_sample ps ON cs.compression_sample_id = ps.compression_sample_id;

-- ============================================================
-- Omnibus Reconstruction Views — PacBio Metag v11
-- ============================================================

-- Adds twist_adaptor_id to the v10 base view.
CREATE VIEW omnibus_pacbio_metag_v11_data AS
    SELECT v10.run_id,
        v10."Sample_ID",
        v10."Sample_Name",
        v10."Sample_Plate",
        v10."Sample_Well",
        v10."barcode_id",
        ps.twist_adaptor_id AS "twist_adaptor_id",
        v10."Sample_Project",
        v10."Well_description",
        v10."orig_name",
        v10."destination_well_384",
        v10."Lane"
    FROM omnibus_pacbio_metag_v10_data v10
    JOIN pacbio_sample ps ON v10."Sample_ID" = ps.compression_sample_id;

-- ============================================================
-- Omnibus Reconstruction Views — Standard Metag v101
-- ============================================================

CREATE VIEW omnibus_standard_metag_v101_header AS
    SELECT sr.run_id,
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
    JOIN assay_type at ON sr.assay_type_id = at.assay_type_id
    JOIN legacy_samplesheet_format lf ON sr.legacy_format_id = lf.legacy_format_id;

CREATE VIEW omnibus_standard_metag_v101_reads AS
    SELECT sr.run_id,
        ir.read1_length AS "read1_length",
        ir.read2_length AS "read2_length"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_id = ir.run_id;

CREATE VIEW omnibus_standard_metag_v101_settings AS
    SELECT sr.run_id,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads",
        ir.override_cycles AS "OverrideCycles"
    FROM sequencing_run sr
    JOIN illumina_run ir ON sr.run_id = ir.run_id;

CREATE VIEW omnibus_standard_metag_v101_data AS
    SELECT cs.run_id,
        cs.compression_sample_id AS "Sample_ID",
        COALESCE(cs.sample_name, ins.sample_name) AS "Sample_Name",
        ip.plate_name AS "Sample_Plate",
        ins.well AS "well_id_384",
        ils.i7_index_id AS "I7_Index_ID",
        ils.i7_sequence AS "index",
        ils.i5_index_id AS "I5_Index_ID",
        ils.i5_sequence AS "index2",
        COALESCE(p.project_name,
            (SELECT p2.project_name FROM project p2
             WHERE p2.project_id = ip.primary_project_id)
        ) AS "Sample_Project",
        cs.well_description AS "Well_description",
        ins.sample_name AS "orig_name",
        cs.compression_well AS "destination_well_384",
        1 AS "Lane"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN input_plate ip ON ins.input_plate_id = ip.input_plate_id
    LEFT JOIN project p ON ins.project_id = p.project_id
    JOIN illumina_sample ils
        ON cs.compression_sample_id = ils.compression_sample_id;

CREATE VIEW omnibus_standard_metag_v101_bioinformatics AS
    SELECT DISTINCT cs.run_id,
        p.project_name AS "Sample_Project",
        p.qiita_id AS "QiitaID",
        ir.barcodes_are_rc AS "BarcodesAreRC",
        ir.forward_adapter AS "ForwardAdapter",
        ir.reverse_adapter AS "ReverseAdapter",
        p.human_filtering AS "HumanFiltering",
        p.library_construction_protocol AS "library_construction_protocol",
        p.experiment_design_description AS "experiment_design_description",
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_id = cs.run_id) AS "contains_replicates"
    FROM compression_sample cs
    JOIN input_sample ins ON cs.input_sample_id = ins.input_sample_id
    JOIN project p ON ins.project_id = p.project_id
    JOIN sequencing_run sr ON cs.run_id = sr.run_id
    JOIN illumina_run ir ON sr.run_id = ir.run_id
    GROUP BY cs.run_id, p.project_id, p.project_name, p.qiita_id,
             ir.barcodes_are_rc, ir.forward_adapter, ir.reverse_adapter,
             p.human_filtering, p.library_construction_protocol,
             p.experiment_design_description;
