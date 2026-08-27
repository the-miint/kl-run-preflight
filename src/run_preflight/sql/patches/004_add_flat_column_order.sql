-- Store a flat prep template's own column header (ordered, tab-joined) so it
-- reconstructs in the sheet's original column order — flat sheets vary in which
-- columns they carry and in what order. Brings a database forward to match
-- schema.sql.

ALTER TABLE processing_run ADD COLUMN flat_column_order TEXT;
