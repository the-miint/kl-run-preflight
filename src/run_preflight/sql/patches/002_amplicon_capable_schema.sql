-- Bring a baseline database forward to the amplicon-capable schema.
--
-- Two tables are rebuilt because SQLite cannot alter a column in place:
-- katharoseq_sample loses tube_code (the matrix/tube barcode is per-sample, so
-- it moves to input_sample) and widens number_of_cells to REAL (serial
-- dilutions reach fractional counts); illumina_run drops NOT NULL from its read
-- lengths so a run whose source document records no configuration can still
-- carry a row. Views referencing a rebuilt table are dropped first and
-- recreated after, since SQLite refuses to drop a table a view references.
--
-- PRAGMA foreign_keys is deliberately not set: the migration runner wraps each
-- .sql patch in a transaction, and SQLite silently ignores that pragma inside
-- one.

-- ---------------------------------------------------------------- core tables

ALTER TABLE input_sample ADD COLUMN matrix_tube_id TEXT;

ALTER TABLE input_plate ADD COLUMN primer_plate TEXT;
ALTER TABLE input_plate ADD COLUMN plating TEXT;
ALTER TABLE input_plate ADD COLUMN extractionkit_lot TEXT;
ALTER TABLE input_plate ADD COLUMN extraction_robot TEXT;
ALTER TABLE input_plate ADD COLUMN platemap_generation_date TEXT;
ALTER TABLE input_plate ADD COLUMN plate_contents_description TEXT;

CREATE TABLE amplicon_sample (
    prepped_sample_idx      INTEGER PRIMARY KEY
        REFERENCES prepped_sample(prepped_sample_idx),
    barcode                 TEXT NOT NULL
);

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

-- ------------------------------------------------------- katharoseq_sample

CREATE TABLE katharoseq_sample_new (
    input_sample_idx         INTEGER PRIMARY KEY
        REFERENCES input_sample(input_sample_idx),
    rack_id                 TEXT,
    number_of_cells         REAL
);

INSERT INTO katharoseq_sample_new (input_sample_idx, rack_id, number_of_cells)
    SELECT input_sample_idx, rack_id, number_of_cells FROM katharoseq_sample;
DROP TABLE katharoseq_sample;
ALTER TABLE katharoseq_sample_new RENAME TO katharoseq_sample;

-- ------------------------------------------------------------- illumina_run

DROP VIEW omnibus_standard_metag_v101_bioinformatics;
DROP VIEW omnibus_standard_metag_v90_bioinformatics;
DROP VIEW omnibus_illumina_settings;
DROP VIEW omnibus_illumina_reads;

CREATE TABLE illumina_run_new (
    run_idx              INTEGER PRIMARY KEY REFERENCES processing_run(run_idx),
    read1_length        INTEGER,
    read2_length        INTEGER,
    reverse_complement  BOOLEAN,
    mask_short_reads    TEXT,
    override_cycles     TEXT,
    forward_adapter     TEXT,
    reverse_adapter     TEXT,
    barcodes_are_rc     BOOLEAN
);

INSERT INTO illumina_run_new SELECT * FROM illumina_run;
DROP TABLE illumina_run;
ALTER TABLE illumina_run_new RENAME TO illumina_run;

-- ------------------------------------------------------------ format registry

ALTER TABLE legacy_samplesheet_format ADD COLUMN delimiter TEXT NOT NULL DEFAULT ',';
ALTER TABLE legacy_samplesheet_format ADD COLUMN has_section_labels BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN platform_idx INTEGER REFERENCES sequencing_platform(platform_idx);
ALTER TABLE legacy_samplesheet_format ADD COLUMN default_instrument_type TEXT;
ALTER TABLE legacy_samplesheet_format ADD COLUMN sample_kind TEXT;
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN sample_name_column TEXT NOT NULL DEFAULT 'Sample_Name';
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN plate_column TEXT NOT NULL DEFAULT 'Sample_Plate';
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN project_column TEXT NOT NULL DEFAULT 'Sample_Project';
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN well_description_column TEXT NOT NULL DEFAULT 'Well_description';
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN well_column TEXT NOT NULL DEFAULT 'well_id_384';
ALTER TABLE legacy_samplesheet_format
    ADD COLUMN replicates_supported BOOLEAN NOT NULL DEFAULT 1;

UPDATE legacy_samplesheet_format
   SET replicates_supported = 0
 WHERE (legacy_sheet_type, legacy_version) IN
       (('standard_metag', 0), ('standard_metag', 90), ('standard_metag', 100));

UPDATE legacy_samplesheet_format
   SET platform_idx = (SELECT platform_idx FROM sequencing_platform WHERE name = 'PacBio'),
       default_instrument_type = 'Pacbio_Revio',
       sample_kind = 'pacbio',
       well_column = 'Sample_Well'
 WHERE (legacy_sheet_type, legacy_version) IN
       (('pacbio_absquant', 10), ('pacbio_absquant', 11), ('pacbio_absquant', 12),
        ('pacbio_metag', 10), ('pacbio_metag', 11));

UPDATE legacy_samplesheet_format
   SET platform_idx = (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
       default_instrument_type = 'Unknown',
       sample_kind = 'illumina',
       well_column = 'Sample_Well'
 WHERE (legacy_sheet_type, legacy_version) = ('standard_metag', 90);

UPDATE legacy_samplesheet_format
   SET platform_idx = (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
       default_instrument_type = 'Unknown',
       sample_kind = 'illumina',
       well_column = 'well_id_384'
 WHERE (legacy_sheet_type, legacy_version) IN
       (('standard_metag', 0), ('standard_metag', 100), ('standard_metag', 101),
        ('abs_quant_metag', 10), ('abs_quant_metag', 11), ('standard_metat', 10));

UPDATE legacy_samplesheet_format
   SET platform_idx = (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
       default_instrument_type = 'Unknown',
       sample_kind = 'tellseq',
       well_column = 'well_id_384'
 WHERE (legacy_sheet_type, legacy_version) IN
       (('tellseq_metag', 10), ('tellseq_absquant', 10));

INSERT INTO legacy_samplesheet_format
        (legacy_format_idx, legacy_sheet_type, legacy_version, delimiter,
         has_section_labels, platform_idx, default_instrument_type, sample_kind,
         sample_name_column, plate_column, project_column,
         well_description_column, well_column)
    VALUES (15, 'amplicon', 1, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL, 'sample_name', 'sample_plate', 'project_name',
            'well_description', 'well_id_384'),
           (16, 'amplicon', 2, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL, 'sample_name', 'sample_plate', 'project_name',
            'well_description', 'well_id'),
           (17, 'amplicon', 3, char(9), 0,
            (SELECT platform_idx FROM sequencing_platform WHERE name = 'Illumina'),
            'Unknown', NULL, 'sample_name', 'sample_plate', 'project_name',
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

-- ------------------------------------------------------------------- views

CREATE VIEW omnibus_illumina_reads AS
    SELECT sr.run_idx,
        ir.read1_length AS "read1_length",
        ir.read2_length AS "read2_length"
    FROM processing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

CREATE VIEW omnibus_illumina_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads",
        ir.override_cycles AS "OverrideCycles"
    FROM processing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

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

CREATE VIEW omnibus_standard_metag_v101_bioinformatics AS
    SELECT v90.*,
        EXISTS (SELECT 1 FROM replicated_samples rs
                WHERE rs.run_idx = v90.run_idx) AS "contains_replicates"
    FROM omnibus_standard_metag_v90_bioinformatics v90;

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

-- Three registered layouts span the real prep-template variety; header
-- resolution takes the widest match. Kathseq_RackID / number_of_cells are
-- optional on all three; unrecognized columns round-trip verbatim.

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

-- v2: narrow — spells the 384-well `well_id`, omits the tube/plate-detail tier.
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
