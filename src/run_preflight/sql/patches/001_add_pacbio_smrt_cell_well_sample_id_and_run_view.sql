-- Add the PacBio SMRT Cell and movie-context columns plus the
-- run_pacbio_sample view. Brings a baseline (v0) database forward to
-- match schema.sql.

ALTER TABLE pacbio_sample ADD COLUMN smrt_cell_well_sample_id TEXT CHECK (smrt_cell_well_sample_id GLOB '[12]_[A-D]01');
ALTER TABLE pacbio_sample ADD COLUMN movie_context_id TEXT;

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
