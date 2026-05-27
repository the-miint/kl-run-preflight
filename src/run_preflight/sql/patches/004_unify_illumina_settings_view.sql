-- Patch 004: Unify the per-version Illumina Settings views into one.
--
-- Drops omnibus_standard_metag_v{0,90,101}_settings and replaces them
-- with a single omnibus_illumina_settings view exposing all three keys
-- (ReverseComplement, MaskShortReads, OverrideCycles). All Illumina
-- legacy_samplesheet_view rows are repointed at the unified view.
-- Reconstruction NULL-skips on emit, so runs that historically populated
-- only a subset of the keys still round-trip cleanly.
--
-- The migration runner sets PRAGMA user_version after this script
-- runs — do not set user_version here.

DROP VIEW IF EXISTS omnibus_standard_metag_v90_settings;
DROP VIEW IF EXISTS omnibus_standard_metag_v0_settings;
DROP VIEW IF EXISTS omnibus_standard_metag_v101_settings;

CREATE VIEW omnibus_illumina_settings AS
    SELECT sr.run_idx,
        ir.reverse_complement AS "ReverseComplement",
        ir.mask_short_reads AS "MaskShortReads",
        ir.override_cycles AS "OverrideCycles"
    FROM processing_run sr
    JOIN illumina_run ir ON sr.run_idx = ir.run_idx;

UPDATE legacy_samplesheet_view
    SET view_name = 'omnibus_illumina_settings'
    WHERE view_name IN (
        'omnibus_standard_metag_v90_settings',
        'omnibus_standard_metag_v0_settings',
        'omnibus_standard_metag_v101_settings'
    );
