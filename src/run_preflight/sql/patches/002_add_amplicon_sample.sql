-- Add the amplicon_sample table (one in-line Golay barcode per prepped_sample)
-- and register the flat amplicon v1 prep-template format. Brings a database
-- forward to match schema.sql.

CREATE TABLE amplicon_sample (
    amplicon_sample_idx      INTEGER PRIMARY KEY AUTOINCREMENT,
    prepped_sample_idx   INTEGER NOT NULL UNIQUE
        REFERENCES prepped_sample(prepped_sample_idx),
    barcode                 TEXT NOT NULL
);

INSERT INTO legacy_samplesheet_format (legacy_sheet_type, legacy_version)
    VALUES ('amplicon', 1);
