-- Move the matrix/tube barcode from katharoseq_sample.tube_code to a nullable
-- input_sample.matrix_tube_id. It is a per-sample fact (the amplicon prep
-- template carries a TubeCode for every sample, not just KatharoSeq controls),
-- so input_sample is its correct home. Brings a database forward to match
-- schema.sql.

ALTER TABLE input_sample ADD COLUMN matrix_tube_id TEXT;
ALTER TABLE katharoseq_sample DROP COLUMN tube_code;
