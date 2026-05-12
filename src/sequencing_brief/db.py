"""Create the SQLite database and populate it from parsed omnibus data.

The schema DDL lives in ``schema.sql`` alongside this module so it can be
read and edited independently of the Python code.
"""

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

from .legacy import LegacyExtraColumnWarning
from .migrate import get_latest_version
from .constants import (
    COL_BARCODE_ID,
    COL_CONTAINS_REPLICATES,
    COL_EXTRACTED_SAMPLE_MASS,
    COL_EXTRACTED_SAMPLE_SURFACE_AREA,
    COL_EXTRACTED_SAMPLE_VOLUME,
    COL_SEQUENCED_SAMPLE_GDNA_MASS,
    LEGACY_COLUMN_ALIASES,
    COL_LANE,
    COL_RUN_IDX,
    COL_BARCODES_ARE_RC,
    COL_DESTINATION_WELL_384,
    COL_EMAIL,
    COL_EXPERIMENT_DESIGN_DESCRIPTION,
    COL_EXTRACTED_GDNA_CONC,
    COL_FORWARD_ADAPTER,
    COL_HUMAN_FILTERING,
    COL_I5_INDEX_ID,
    COL_I7_INDEX_ID,
    COL_INDEX,
    COL_INDEX2,
    COL_LIBRARY_CONSTRUCTION_PROTOCOL,
    COL_MASS_SYNDNA_INPUT,
    COL_ORIG_NAME,
    COL_QIITA_ID,
    COL_REVERSE_ADAPTER,
    COL_SAMPLE_NAME,
    COL_SAMPLE_PLATE,
    COL_SAMPLE_PROJECT,
    COL_SAMPLE_WELL,
    COL_SC_SAMPLE_NAME,
    COL_SC_SAMPLE_TYPE,
    COL_SYNDNA_IS_TWISTED,
    COL_SYNDNA_POOL_NUMBER,
    COL_TOTAL_RNA_CONC,
    COL_TWIST_ADAPTOR_ID,
    COL_VOL_EXTRACTED_ELUTION,
    COL_WELL_DESCRIPTION,
    COL_WELL_ID_384,
    CONTEXT_TYPE_MAP,
    FIELD_ASSAY,
    FIELD_DATE,
    FIELD_DESCRIPTION,
    FIELD_EXPERIMENT_NAME,
    FIELD_INVESTIGATOR_NAME,
    FIELD_MASK_SHORT_READS,
    FIELD_OVERRIDE_CYCLES,
    FIELD_REVERSE_COMPLEMENT,
    FIELD_SHEET_TYPE,
    FIELD_SHEET_VERSION,
    PLATFORM_ILLUMINA,
    PLATFORM_PACBIO,
    SAMPLE_TYPE_STANDARD,
    SECTION_BIOINFORMATICS,
    SECTION_CONTACT,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_READS,
    SECTION_SAMPLE_CONTEXT,
    SECTION_SETTINGS,
    SEQUENCER_PACBIO_REVIO,
    SEQUENCER_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Column-name normalization
# ---------------------------------------------------------------------------


def _normalize_column_aliases(data_rows: list[dict]) -> list[dict]:
    """Rename legacy CSV column names to their canonical DB equivalents.

    Rewrites row dicts in place and returns the same list for convenience.
    Only keys present in LEGACY_COLUMN_ALIASES are affected.

    Args:
        data_rows: The parsed Data-section row dicts.

    Returns:
        list[dict]: The same *data_rows* list, with aliased keys renamed.
    """
    # Build the subset of aliases that actually appear in the data
    if not data_rows:
        return data_rows
    active_aliases = {
        csv_name: db_name
        for csv_name, db_name in LEGACY_COLUMN_ALIASES.items()
        if csv_name in data_rows[0]
    }
    if not active_aliases:
        return data_rows

    # Rename matching keys in every row
    for row in data_rows:
        for csv_name, db_name in active_aliases.items():
            if csv_name in row:
                row[db_name] = row.pop(csv_name)
    return data_rows


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "schema.sql"


def _load_schema_sql() -> str:
    """Read the DDL + seed-data script from the companion SQL file.

    Returns:
        str: The full contents of schema.sql as a single string.
    """
    return _SCHEMA_PATH.read_text()


def create_db(db_path: str) -> sqlite3.Connection:
    """Create a fresh SQLite database at *db_path* with the full schema.

    Args:
        db_path: Filesystem path where the SQLite database file will be
            created. Any existing file at this path will be overwritten by
            SQLite's default behaviour.

    Returns:
        sqlite3.Connection: An open connection to the new database with
        foreign-key enforcement enabled.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_load_schema_sql())
    # Stamp the database with the current schema version
    conn.execute(f"PRAGMA user_version = {get_latest_version()}")
    return conn


# ---------------------------------------------------------------------------
# View introspection
# ---------------------------------------------------------------------------


def introspect_view(cur, view_name: str) -> tuple[list[str], bool]:
    """Return column names (excluding run_idx) and whether run_idx exists.

    Issues a single PRAGMA table_info call per view so callers never
    need to re-query the view schema.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        tuple[list[str], bool]: (column names sans run_idx, has_run_idx).
    """
    cur.execute(f"PRAGMA table_info({view_name})")
    all_info = cur.fetchall()
    has_run_idx = any(row[1] == COL_RUN_IDX for row in all_info)
    cols = [row[1] for row in all_info if row[1] != COL_RUN_IDX]
    return cols, has_run_idx


def get_view_columns(cur, view_name: str) -> list[str]:
    """Return the column names of a SQL view, excluding run_idx.

    Thin wrapper around introspect_view for callers that only need
    the column list.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        list[str]: Ordered list of column names from the view, with
        run_idx omitted.
    """
    cols, _ = introspect_view(cur, view_name)
    return cols


# ---------------------------------------------------------------------------
# Section format lookup
# ---------------------------------------------------------------------------


def get_section_formats(conn: sqlite3.Connection) -> dict[str, str]:
    """Return a mapping of section name to section format from the DB.

    Queries the legacy_samplesheet_view table for all distinct
    (section_name, section_format) pairs.  The result can be passed to
    the parser so that section-format knowledge lives in one place
    (the DB schema).

    Args:
        conn: An open SQLite connection with the schema already created.

    Returns:
        dict[str, str]: Mapping of section name (e.g. "Header", "Data")
        to format string (e.g. "header_kv", "tabular").
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT section_name, section_format FROM legacy_samplesheet_view"
    )
    return {name: fmt for name, fmt in cur.fetchall()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Lane is the only CSV column whose value is allowed to differ across
# rows that share a (plate, orig_name, dest_well) triple — by definition
# a lane split is "same loading event, different lane."  Every other
# column (including derived ones like Sample_ID) must agree across the
# group; mismatches are surfaced by _check_per_tube_consistency.
_PER_LOADING_COLUMNS = frozenset({COL_LANE})


def _check_per_tube_consistency(
    first_row: dict, current_row: dict, cache_key: tuple
) -> None:
    """Verify a lane-split row agrees with the first row in its group.

    Lane-split CSV rows share a `(plate, orig_name, dest_well)` triple
    and produce one `prepped_sample` with N platform-table rows.
    Any column whose value differs between the first row in the group
    and *current_row* (other than per-loading columns) signals either a
    CSV authoring error or data we cannot losslessly represent under
    the lane-split model.

    Args:
        first_row: The row dict that originally created the group.
        current_row: A subsequent row dict that hashes to the same key.
        cache_key: The `(plate, orig_name, dest_well)` triple, used to
            identify the offending group in the error message.

    Raises:
        ValueError: If any non-per-loading column disagrees between the
            two rows.
    """
    for col in set(first_row) | set(current_row):
        if col in _PER_LOADING_COLUMNS:
            continue
        first_val = first_row.get(col)
        current_val = current_row.get(col)
        if first_val != current_val:
            raise ValueError(
                f"Lane-split rows for {cache_key!r} disagree on column "
                f"{col!r}: first row has {first_val!r}, this row has "
                f"{current_val!r}"
            )


def _parse_bool_str(value: str | None, *, nullable: bool = False) -> int | None:
    """Convert a boolean-ish string to an integer 0 or 1.

    Recognises "false" and "0" (case-insensitive) as falsy; everything
    else is truthy.  When *nullable* is True, empty strings and None
    return None instead of an integer.

    Args:
        value: The string to parse ("True", "False", "0", "1", etc.).
        nullable: If True, return None for empty or None values.

    Returns:
        int | None: 0 for falsy, 1 for truthy, or None if nullable and
        the value is empty/None.
    """
    if nullable and value in ("", None):
        return None
    if isinstance(value, str) and value.lower() in ("false", "0"):
        return 0
    return 1


def _lookup_idx(cur, table: str, col: str, value) -> int:
    """Return the primary-key rowid for the row where *col* equals *value*.

    Args:
        cur: An open SQLite cursor.
        table: Name of the table to query.
        col: Column name to match against.
        value: The value to look up in *col*.

    Returns:
        int: The rowid of the matching row.

    Raises:
        ValueError: If no row with the given *col*/*value* exists.
    """
    cur.execute(f"SELECT rowid FROM {table} WHERE {col} = ?", (value,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"{table}.{col} = {value!r} not found")
    return row[0]


# ---------------------------------------------------------------------------
# Pre-population checks
# ---------------------------------------------------------------------------


def _reject_unsupported_replicates(
    sheet_version: int,
    data_rows: list[dict],
    bio_rows: list[dict],
) -> None:
    """Raise ValueError if a pre-v101 file contains replicates.

    Replicate well semantics changed at v101. Earlier standard_metag
    versions that contain replicate signals use well_id_384 in a way
    that cannot be round-tripped correctly. This check applies only to
    standard_metag files; other format families do not carry replicate
    columns in pre-v101 versions.
    """
    if sheet_version >= 101:
        return

    # Check for any replicate signal
    data_cols = set(data_rows[0].keys()) if data_rows else set()
    has_replicate_cols = bool({COL_ORIG_NAME, COL_DESTINATION_WELL_384} & data_cols)
    has_replicate_flag = any(
        row.get(COL_CONTAINS_REPLICATES) is not None
        and _parse_bool_str(row.get(COL_CONTAINS_REPLICATES))
        for row in bio_rows
    )

    if has_replicate_cols or has_replicate_flag:
        raise ValueError(
            f"Replicates in legacy version {sheet_version} are not "
            f"supported; replicate well semantics require v101 or later."
        )


# ---------------------------------------------------------------------------
# Main populate entry-point
# ---------------------------------------------------------------------------


def populate_db(conn: sqlite3.Connection, sections: dict) -> None:
    """Insert all parsed omnibus data into *conn*.

    Resolves reference-table IDs, inserts projects, input plates, the
    sequencing run, and all sample rows (with platform- and protocol-
    specific child tables, including TellSeq, absquant, and metatranscriptomic
    extensions) in a single transaction committed at the end.

    Args:
        conn: An open SQLite connection (with schema already created).
        sections: The dict returned by parse_omnibus(), keyed by section
            name ("Header", "Data", "Bioinformatics", "Contact", and
            optionally "SampleContext").
    """
    cur = conn.cursor()
    header = sections[SECTION_HEADER]
    data_rows = _normalize_column_aliases(sections[SECTION_DATA])
    bio_rows = sections[SECTION_BIOINFORMATICS]
    contact_rows = sections[SECTION_CONTACT]
    context_rows = sections.get(SECTION_SAMPLE_CONTEXT, [])

    # -- Determine platform from SheetType ----------------------------------
    sheet_type = header.get(FIELD_SHEET_TYPE, "")
    is_pacbio = "pacbio" in sheet_type.lower()
    is_tellseq = "tellseq" in sheet_type.lower()
    platform_name = PLATFORM_PACBIO if is_pacbio else PLATFORM_ILLUMINA
    sequencer = SEQUENCER_PACBIO_REVIO if is_pacbio else SEQUENCER_UNKNOWN

    # -- Build a lookup: sample_name → sample_type DB name ------------------
    # SampleContext tells us which samples are controls and their type.
    control_names: dict[str, str] = {}
    for row in context_rows:
        st = row.get(COL_SC_SAMPLE_TYPE, "")
        control_names[row[COL_SC_SAMPLE_NAME]] = CONTEXT_TYPE_MAP.get(st, st)

    # -- Resolve reference-table IDs ----------------------------------------
    assay_type_idx = _lookup_idx(cur, "assay_type", "name", header[FIELD_ASSAY])
    platform_idx = _lookup_idx(cur, "sequencing_platform", "name", platform_name)

    # Cache all sample_type IDs for quick lookup.
    cur.execute("SELECT sample_type_idx, name FROM sample_type")
    type_ids: dict[str, int] = {name: sid for sid, name in cur.fetchall()}

    # -- Resolve legacy format ID (may be NULL for native runs) -------------
    sheet_version = int(header.get(FIELD_SHEET_VERSION, 0))
    cur.execute(
        "SELECT legacy_format_idx FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (sheet_type, sheet_version),
    )
    fmt_row = cur.fetchone()
    legacy_format_idx = fmt_row[0] if fmt_row else None

    # Reject pre-v101 files with replicates (unsupported well semantics)
    _reject_unsupported_replicates(sheet_version, data_rows, bio_rows)

    # -- Insert projects (one per Bioinformatics row) -----------------------
    # Build a quick email lookup from the Contact section.
    contact_map = {r[COL_SAMPLE_PROJECT]: r.get(COL_EMAIL, "") for r in contact_rows}

    project_idxs: dict[str, int] = {}
    for bio in bio_rows:
        proj_name = bio[COL_SAMPLE_PROJECT]
        human_filt = _parse_bool_str(bio.get(COL_HUMAN_FILTERING, "True"))
        cur.execute(
            """INSERT INTO project
               (project_name, qiita_id, contact_email,
                human_filtering, library_construction_protocol,
                experiment_design_description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                proj_name,
                bio[COL_QIITA_ID],
                contact_map.get(proj_name, ""),
                human_filt,
                bio[COL_LIBRARY_CONSTRUCTION_PROTOCOL],
                bio[COL_EXPERIMENT_DESIGN_DESCRIPTION],
            ),
        )
        assert cur.lastrowid is not None
        project_idxs[proj_name] = cur.lastrowid

    # -- Insert input plates ------------------------------------------------
    # Each unique Sample_Plate in the Data section becomes an input_plate.
    # The first project seen on that plate becomes primary_project_idx.
    plate_info: dict[str, dict] = {}
    for row in data_rows:
        pname = row[COL_SAMPLE_PLATE]
        if pname not in plate_info:
            plate_info[pname] = {
                "project": row[COL_SAMPLE_PROJECT],
                "elution_vol": row.get(COL_VOL_EXTRACTED_ELUTION),
            }

    plate_idxs: dict[str, int] = {}
    for pname, info in plate_info.items():
        proj_id = project_idxs.get(info["project"])
        elution = float(info["elution_vol"]) if info["elution_vol"] else None
        cur.execute(
            "INSERT INTO input_plate (plate_name, primary_project_idx, elution_vol) "
            "VALUES (?, ?, ?)",
            (pname, proj_id, elution),
        )
        assert cur.lastrowid is not None
        plate_idxs[pname] = cur.lastrowid

    # -- Insert sequencing run ----------------------------------------------
    cur.execute(
        """INSERT INTO sequencing_run
           (experiment_name, run_date, investigator_name, sequencer,
            assay_type_idx, platform_idx, compression_plate_name,
            description, legacy_format_idx)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            header.get(FIELD_EXPERIMENT_NAME, ""),
            header.get(FIELD_DATE, ""),
            header.get(FIELD_INVESTIGATOR_NAME, ""),
            sequencer,
            assay_type_idx,
            platform_idx,
            None,
            header.get(FIELD_DESCRIPTION, ""),
            legacy_format_idx,
        ),
    )
    assert cur.lastrowid is not None
    run_idx = cur.lastrowid

    # -- Illumina-specific run config (Reads + Settings + Bioinformatics) ---
    if not is_pacbio:
        _populate_illumina_run(cur, run_idx, sections, bio_rows)

    # -- Insert samples -----------------------------------------------------
    # Figure out which column holds the well identifier.
    well_col = COL_WELL_ID_384 if COL_WELL_ID_384 in data_rows[0] else COL_SAMPLE_WELL
    has_replicates = COL_ORIG_NAME in data_rows[0]

    # Determine extra Data columns not recognized by the format's view
    extra_cols = _get_extra_columns(cur, legacy_format_idx, data_rows)

    # Three-layer dedup ladder.  Replicates share input_sample + compression_sample
    # but get distinct prepped_samples (different dest_well).  Lane
    # splits share input_sample + compression_sample + prepped_sample (same
    # dest_well, different Lane only) and produce N platform-table rows.
    input_sample_cache: dict[tuple, int] = {}
    compression_cache: dict[tuple, int] = {}
    prs_cache: dict[tuple, int] = {}
    prs_first_row: dict[int, dict] = {}

    for row in data_rows:
        sample_name = row[COL_SAMPLE_NAME]
        plate_name = row[COL_SAMPLE_PLATE]
        well = row.get(well_col, "")
        project_name = row[COL_SAMPLE_PROJECT]

        # For replicates, the real sample identity is orig_name.
        orig_name = (
            row.get(COL_ORIG_NAME, sample_name) if has_replicates else sample_name
        )
        dest_well = row.get(COL_DESTINATION_WELL_384, well) if has_replicates else well

        is_control = sample_name in control_names or orig_name in control_names
        control_key = sample_name if sample_name in control_names else orig_name

        # Create or reuse input_sample and compression_sample.
        cache_key = (plate_name, orig_name)
        if cache_key in input_sample_cache:
            cs_idx = compression_cache[cache_key]
        else:
            # Controls have NULL project_idx; they inherit via input_plate.
            if is_control:
                st_name = control_names[control_key]
                sample_project_idx = None
            else:
                st_name = SAMPLE_TYPE_STANDARD
                sample_project_idx = project_idxs.get(project_name)

            cur.execute(
                """INSERT INTO input_sample
                   (sample_name, input_plate_idxx, project_idx, sample_type_idx)
                   VALUES (?, ?, ?, ?)""",
                (
                    orig_name,
                    plate_idxs[plate_name],
                    sample_project_idx,
                    type_ids[st_name],
                ),
            )
            assert cur.lastrowid is not None
            input_sample_idx = cur.lastrowid
            input_sample_cache[cache_key] = input_sample_idx

            # Create compression_sample (one per input_sample per run)
            cur.execute(
                """INSERT INTO compression_sample
                   (run_idx, input_sample_idx, compression_well)
                   VALUES (?, ?, ?)""",
                (run_idx, input_sample_idx, well),
            )
            assert cur.lastrowid is not None
            cs_idx = cur.lastrowid
            compression_cache[cache_key] = cs_idx

        # Reuse prepped_sample for lane splits; create a new one
        # otherwise.  On reuse, every column except Lane must match.
        prs_cache_key = (plate_name, orig_name, dest_well)
        if prs_cache_key in prs_cache:
            prs_idx = prs_cache[prs_cache_key]
            _check_per_tube_consistency(prs_first_row[prs_idx], row, prs_cache_key)
        else:
            # -- prepped_sample --
            well_desc = row.get(COL_WELL_DESCRIPTION) or None
            prepped_sample_name = sample_name if has_replicates else None
            cur.execute(
                """INSERT INTO prepped_sample
                   (compression_sample_idx, prepped_well,
                    sample_name, well_description)
                   VALUES (?, ?, ?, ?)""",
                (cs_idx, dest_well, prepped_sample_name, well_desc),
            )
            assert cur.lastrowid is not None
            prs_idx = cur.lastrowid
            prs_cache[prs_cache_key] = prs_idx
            prs_first_row[prs_idx] = row

            # Per-tube tables: written once per prepped_sample, using
            # the first row's values.  Lane-split rows are guaranteed to
            # agree on these columns by _check_per_tube_consistency above.
            _populate_absquant_sample(cur, prs_idx, row)
            _populate_metatranscriptomic_sample(cur, prs_idx, row)
            _populate_extra_columns(cur, prs_idx, row, extra_cols)

        # Per-loading platform-specific row: one per CSV row, always.
        if is_pacbio:
            _populate_pacbio_sample(cur, prs_idx, row)
        elif is_tellseq:
            # TellSeq is a library prep protocol on Illumina; it uses
            # tellseq_sample instead of illumina_sample for per-sample data.
            _populate_tellseq_sample(cur, prs_idx, row)
        else:
            _populate_illumina_sample(cur, prs_idx, row)

    conn.commit()


# ---------------------------------------------------------------------------
# Platform-specific helpers
# ---------------------------------------------------------------------------


def _populate_illumina_run(cur, run_idx: int, sections: dict, bio_rows: list):
    """Insert a single illumina_run row for the given sequencing run.

    Combines data from the Reads, Settings, and Bioinformatics sections to
    populate read lengths, reverse-complement flag, adapter sequences, and
    other Illumina-specific run configuration.

    Args:
        cur: An open SQLite cursor.
        run_idx: The sequencing_run.run_idx to associate the row with.
        sections: The full parsed-sections dict (used to read "Reads"
            and "Settings").
        bio_rows: The list of Bioinformatics row dicts (adapter info is
            taken from the first row).
    """
    reads = sections.get(SECTION_READS, [])
    settings = sections.get(SECTION_SETTINGS, {})

    read1 = int(reads[0]) if len(reads) > 0 else 0
    read2 = int(reads[1]) if len(reads) > 1 else 0

    # ReverseComplement is stored as "0"/"1" in Settings.
    rc_bool = _parse_bool_str(settings.get(FIELD_REVERSE_COMPLEMENT, "False"))

    # Adapter sequences and BarcodesAreRC come from Bioinformatics
    # (same for every project in a run, so we grab from the first row).
    first_bio = bio_rows[0] if bio_rows else {}

    cur.execute(
        """INSERT INTO illumina_run
           (run_idx, read1_length, read2_length,
            reverse_complement, mask_short_reads, override_cycles,
            forward_adapter, reverse_adapter, barcodes_are_rc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_idx,
            read1,
            read2,
            rc_bool,
            settings.get(FIELD_MASK_SHORT_READS),
            settings.get(FIELD_OVERRIDE_CYCLES),
            first_bio.get(COL_FORWARD_ADAPTER, ""),
            first_bio.get(COL_REVERSE_ADAPTER, ""),
            _parse_bool_str(first_bio.get(COL_BARCODES_ARE_RC, "False")),
        ),
    )


def _populate_absquant_sample(cur, prs_idx: int, row: dict):
    """Insert a metagenomic_absquant_sample row if absquant columns are present.

    AbsQuant columns (mass_syndna_input_ng, etc.) appear in both PacBio and
    Illumina absquant sheet types.  This helper is called after the
    platform-specific sample insert.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict.
    """
    # AbsQuant columns are only present in absquant sheet types.
    if COL_MASS_SYNDNA_INPUT not in row:
        return

    mass = float(row[COL_MASS_SYNDNA_INPUT]) if row.get(COL_MASS_SYNDNA_INPUT) else None
    conc = (
        float(row[COL_EXTRACTED_GDNA_CONC])
        if row.get(COL_EXTRACTED_GDNA_CONC)
        else None
    )

    # Parse sequenced sample gDNA mass (shared across all absquant capabilities)
    gdna_mass = (
        float(row[COL_SEQUENCED_SAMPLE_GDNA_MASS])
        if row.get(COL_SEQUENCED_SAMPLE_GDNA_MASS)
        else None
    )

    # Parse optional total-sample-input metric columns
    sample_mass = (
        float(row[COL_EXTRACTED_SAMPLE_MASS])
        if row.get(COL_EXTRACTED_SAMPLE_MASS)
        else None
    )
    sample_vol = (
        float(row[COL_EXTRACTED_SAMPLE_VOLUME])
        if row.get(COL_EXTRACTED_SAMPLE_VOLUME)
        else None
    )
    sample_sa = (
        float(row[COL_EXTRACTED_SAMPLE_SURFACE_AREA])
        if row.get(COL_EXTRACTED_SAMPLE_SURFACE_AREA)
        else None
    )

    cur.execute(
        "INSERT INTO metagenomic_absquant_sample "
        "(prepped_sample_idx, syndna_pool_mass_ng, "
        " extracted_gdna_concentration, syndna_pool_number, "
        " sequenced_sample_gdna_mass_ng, "
        " extracted_sample_mass_g, extracted_sample_volume_ul, "
        " extracted_sample_surface_area_cm2) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            prs_idx,
            mass,
            conc,
            row.get(COL_SYNDNA_POOL_NUMBER) or None,
            gdna_mass,
            sample_mass,
            sample_vol,
            sample_sa,
        ),
    )


def _populate_metatranscriptomic_sample(cur, prs_idx: int, row: dict):
    """Insert a metatranscriptomic_sample row if metat columns are present.

    The total_rna_concentration_ng_ul column identifies a metatranscriptomic
    sample.  This helper is called after the platform-specific sample insert.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict.
    """
    if COL_TOTAL_RNA_CONC not in row:
        return

    conc = float(row[COL_TOTAL_RNA_CONC]) if row.get(COL_TOTAL_RNA_CONC) else None

    cur.execute(
        "INSERT INTO metatranscriptomic_sample "
        "(prepped_sample_idx, total_rna_concentration_ng_ul) "
        "VALUES (?, ?)",
        (prs_idx, conc),
    )


def _populate_pacbio_sample(cur, prs_idx: int, row: dict):
    """Insert a pacbio_sample row for a PacBio sample.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict containing PacBio-specific
            columns.
    """
    # Parse syndna_is_twisted boolean (may be empty or absent).
    twisted = _parse_bool_str(row.get(COL_SYNDNA_IS_TWISTED), nullable=True)

    cur.execute(
        "INSERT INTO pacbio_sample "
        "(prepped_sample_idx, barcode_id, twist_adaptor_id, syndna_is_twisted) "
        "VALUES (?, ?, ?, ?)",
        (
            prs_idx,
            row.get(COL_BARCODE_ID, ""),
            row.get(COL_TWIST_ADAPTOR_ID) or None,
            twisted,
        ),
    )


def _populate_tellseq_sample(cur, prs_idx: int, row: dict):
    """Insert a tellseq_sample row with barcode and lane information.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict containing TellSeq-specific
            columns (barcode_id, and optionally Lane).
    """
    # Parse lane as integer if present
    lane_val = row.get(COL_LANE)
    lane = int(lane_val) if lane_val else None

    cur.execute(
        "INSERT INTO tellseq_sample "
        "(prepped_sample_idx, barcode_id, lane) "
        "VALUES (?, ?, ?)",
        (prs_idx, row.get(COL_BARCODE_ID, ""), lane),
    )


def _populate_illumina_sample(cur, prs_idx: int, row: dict):
    """Insert an illumina_sample row with i5/i7 index information.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict containing Illumina index
            columns (I7_Index_ID, index, I5_Index_ID, index2).
    """
    # Parse lane as integer if present
    lane_val = row.get(COL_LANE)
    lane = int(lane_val) if lane_val else None

    cur.execute(
        "INSERT INTO illumina_sample "
        "(prepped_sample_idx, i7_index_id, i7_sequence, "
        " i5_index_id, i5_sequence, lane) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            prs_idx,
            row.get(COL_I7_INDEX_ID, ""),
            row.get(COL_INDEX, ""),
            row.get(COL_I5_INDEX_ID, ""),
            row.get(COL_INDEX2, ""),
            lane,
        ),
    )


def _get_extra_columns(
    cur, legacy_format_idx: int | None, data_rows: list[dict]
) -> list[str]:
    """Return sorted list of Data columns not recognized by the format's view.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The legacy format id for this run, or None.
        data_rows: The parsed Data-section row dicts.

    Returns:
        list[str]: Alphabetically sorted extra column names, or empty list.
    """
    if legacy_format_idx is None or not data_rows:
        return []

    # Look up the Data view for this format
    cur.execute(
        "SELECT view_name FROM legacy_samplesheet_view "
        "WHERE legacy_format_idx = ? AND section_name = 'Data'",
        (legacy_format_idx,),
    )
    view_row = cur.fetchone()
    if view_row is None:
        return []

    # Get the known column set from the view (CSV-side names) and apply
    # the alias map so it uses canonical DB-side names where applicable.
    # Without this step, a column like calc_mass_sample_aliquot_input_g
    # (CSV name) would not match the alias-normalized parsed column name
    # extracted_sample_mass_g (DB name), causing it to be misclassified
    # as an extra column.
    known_cols = {
        LEGACY_COLUMN_ALIASES.get(c, c) for c in get_view_columns(cur, view_row[0])
    }

    # Identify extra columns from the parsed data
    parsed_cols = set(data_rows[0].keys())
    extras = sorted(parsed_cols - known_cols)

    # Warn so callers can see which columns will be carried verbatim
    # via legacy_extra_column rather than mapped to typed DB columns
    if extras:
        warnings.warn(
            f"[Data] carrying {len(extras)} unrecognized column(s) "
            f"as extras: {extras}. These will be stored in "
            f"legacy_extra_column and round-tripped verbatim.",
            LegacyExtraColumnWarning,
            stacklevel=2,
        )

    return extras


def _populate_extra_columns(cur, prs_idx: int, row: dict, extra_cols: list[str]):
    """Insert legacy_extra_column rows for a single sample.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict.
        extra_cols: Column names to store as extra columns.
    """
    for col_name in extra_cols:
        cur.execute(
            "INSERT INTO legacy_extra_column "
            "(prepped_sample_idx, column_name, column_value) "
            "VALUES (?, ?, ?)",
            (prs_idx, col_name, row.get(col_name)),
        )
