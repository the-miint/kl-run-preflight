"""Create the SQLite database and populate it from parsed omnibus data.

The schema DDL lives in ``schema.sql`` alongside this module so it can be
read and edited independently of the Python code.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .constants import (
    COL_BARCODE_ID,
    COL_RUN_ID,
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
    return conn


# ---------------------------------------------------------------------------
# View introspection
# ---------------------------------------------------------------------------


def introspect_view(cur, view_name: str) -> tuple[list[str], bool]:
    """Return column names (excluding run_id) and whether run_id exists.

    Issues a single PRAGMA table_info call per view so callers never
    need to re-query the view schema.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        tuple[list[str], bool]: (column names sans run_id, has_run_id).
    """
    cur.execute(f"PRAGMA table_info({view_name})")
    all_info = cur.fetchall()
    has_run_id = any(row[1] == COL_RUN_ID for row in all_info)
    cols = [row[1] for row in all_info if row[1] != COL_RUN_ID]
    return cols, has_run_id


def get_view_columns(cur, view_name: str) -> list[str]:
    """Return the column names of a SQL view, excluding run_id.

    Thin wrapper around introspect_view for callers that only need
    the column list.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        list[str]: Ordered list of column names from the view, with
        run_id omitted.
    """
    cols, _ = introspect_view(cur, view_name)
    return cols


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _lookup_id(cur, table: str, col: str, value) -> int:
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
# Main populate entry-point
# ---------------------------------------------------------------------------


def populate_db(conn: sqlite3.Connection, sections: dict) -> None:
    """Insert all parsed omnibus data into *conn*.

    Resolves reference-table IDs, inserts projects, input plates, the
    sequencing run, and all sample rows (with platform-specific child
    tables) in a single transaction committed at the end.

    Args:
        conn: An open SQLite connection (with schema already created).
        sections: The dict returned by parse_omnibus(), keyed by section
            name ("Header", "Data", "Bioinformatics", "Contact", and
            optionally "SampleContext").
    """
    cur = conn.cursor()
    header = sections[SECTION_HEADER]
    data_rows = sections[SECTION_DATA]
    bio_rows = sections[SECTION_BIOINFORMATICS]
    contact_rows = sections[SECTION_CONTACT]
    context_rows = sections.get(SECTION_SAMPLE_CONTEXT, [])

    # -- Determine platform from SheetType ----------------------------------
    sheet_type = header.get(FIELD_SHEET_TYPE, "")
    is_pacbio = "pacbio" in sheet_type.lower()
    platform_name = PLATFORM_PACBIO if is_pacbio else PLATFORM_ILLUMINA
    sequencer = SEQUENCER_PACBIO_REVIO if is_pacbio else SEQUENCER_UNKNOWN

    # -- Build a lookup: sample_name → sample_type DB name ------------------
    # SampleContext tells us which samples are controls and their type.
    control_names: dict[str, str] = {}
    for row in context_rows:
        st = row.get(COL_SC_SAMPLE_TYPE, "")
        control_names[row[COL_SC_SAMPLE_NAME]] = CONTEXT_TYPE_MAP.get(st, st)

    # -- Resolve reference-table IDs ----------------------------------------
    assay_type_id = _lookup_id(cur, "assay_type", "name", header[FIELD_ASSAY])
    platform_id = _lookup_id(cur, "sequencing_platform", "name", platform_name)

    # Cache all sample_type IDs for quick lookup.
    cur.execute("SELECT sample_type_id, name FROM sample_type")
    type_ids: dict[str, int] = {name: sid for sid, name in cur.fetchall()}

    # -- Resolve legacy format ID (may be NULL for native runs) -------------
    sheet_version = int(header.get(FIELD_SHEET_VERSION, 0))
    cur.execute(
        "SELECT legacy_format_id FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (sheet_type, sheet_version),
    )
    fmt_row = cur.fetchone()
    legacy_format_id = fmt_row[0] if fmt_row else None

    # -- Insert projects (one per Bioinformatics row) -----------------------
    # Build a quick email lookup from the Contact section.
    contact_map = {r[COL_SAMPLE_PROJECT]: r.get(COL_EMAIL, "") for r in contact_rows}

    project_ids: dict[str, int] = {}
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
        project_ids[proj_name] = cur.lastrowid

    # -- Insert input plates ------------------------------------------------
    # Each unique Sample_Plate in the Data section becomes an input_plate.
    # The first project seen on that plate becomes primary_project_id.
    plate_info: dict[str, dict] = {}
    for row in data_rows:
        pname = row[COL_SAMPLE_PLATE]
        if pname not in plate_info:
            plate_info[pname] = {
                "project": row[COL_SAMPLE_PROJECT],
                "elution_vol": row.get(COL_VOL_EXTRACTED_ELUTION),
            }

    plate_ids: dict[str, int] = {}
    for pname, info in plate_info.items():
        proj_id = project_ids.get(info["project"])
        elution = float(info["elution_vol"]) if info["elution_vol"] else None
        cur.execute(
            "INSERT INTO input_plate (plate_name, primary_project_id, elution_vol) "
            "VALUES (?, ?, ?)",
            (pname, proj_id, elution),
        )
        assert cur.lastrowid is not None
        plate_ids[pname] = cur.lastrowid

    # -- Insert sequencing run ----------------------------------------------
    cur.execute(
        """INSERT INTO sequencing_run
           (experiment_name, run_date, investigator_name, sequencer,
            assay_type_id, platform_id, compression_plate_name,
            description, legacy_format_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            header.get(FIELD_EXPERIMENT_NAME, ""),
            header.get(FIELD_DATE, ""),
            header.get(FIELD_INVESTIGATOR_NAME, ""),
            sequencer,
            assay_type_id,
            platform_id,
            None,
            header.get(FIELD_DESCRIPTION, ""),
            legacy_format_id,
        ),
    )
    assert cur.lastrowid is not None
    run_id = cur.lastrowid

    # -- Illumina-specific run config (Reads + Settings + Bioinformatics) ---
    if not is_pacbio:
        _populate_illumina_run(cur, run_id, sections, bio_rows)

    # -- Insert samples -----------------------------------------------------
    # Figure out which column holds the well identifier.
    well_col = COL_WELL_ID_384 if COL_WELL_ID_384 in data_rows[0] else COL_SAMPLE_WELL
    has_replicates = COL_ORIG_NAME in data_rows[0]

    # Cache input_samples by (plate, orig_name, well) so that replicated
    # samples (same orig_name on the same plate/well) share one input_sample
    # and get multiple compression_sample rows.
    input_sample_cache: dict[tuple, int] = {}

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

        # Create or reuse input_sample.
        cache_key = (plate_name, orig_name, well)
        if cache_key in input_sample_cache:
            input_sample_id = input_sample_cache[cache_key]
        else:
            # Controls have NULL project_id; they inherit via input_plate.
            if is_control:
                st_name = control_names[control_key]
                sample_project_id = None
            else:
                st_name = SAMPLE_TYPE_STANDARD
                sample_project_id = project_ids.get(project_name)

            cur.execute(
                """INSERT INTO input_sample
                   (sample_name, input_plate_id, well, project_id, sample_type_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    orig_name,
                    plate_ids[plate_name],
                    well,
                    sample_project_id,
                    type_ids[st_name],
                ),
            )
            assert cur.lastrowid is not None
            input_sample_id = cur.lastrowid
            input_sample_cache[cache_key] = input_sample_id

        # -- compression_sample --
        well_desc = row.get(COL_WELL_DESCRIPTION) or None
        comp_sample_name = sample_name if has_replicates else None
        cur.execute(
            """INSERT INTO compression_sample
               (run_id, input_sample_id, compression_well,
                sample_name, well_description)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, input_sample_id, dest_well, comp_sample_name, well_desc),
        )
        assert cur.lastrowid is not None
        cs_id = cur.lastrowid

        # -- Platform-specific sample tables --
        if is_pacbio:
            _populate_pacbio_sample(cur, cs_id, row)
        else:
            _populate_illumina_sample(cur, cs_id, row)

    conn.commit()


# ---------------------------------------------------------------------------
# Platform-specific helpers
# ---------------------------------------------------------------------------


def _populate_illumina_run(cur, run_id: int, sections: dict, bio_rows: list):
    """Insert a single illumina_run row for the given sequencing run.

    Combines data from the Reads, Settings, and Bioinformatics sections to
    populate read lengths, reverse-complement flag, adapter sequences, and
    other Illumina-specific run configuration.

    Args:
        cur: An open SQLite cursor.
        run_id: The sequencing_run.run_id to associate the row with.
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
           (run_id, read1_length, read2_length,
            reverse_complement, mask_short_reads, override_cycles,
            forward_adapter, reverse_adapter, barcodes_are_rc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
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


def _populate_pacbio_sample(cur, cs_id: int, row: dict):
    """Insert a pacbio_sample row and optionally a metagenomic_absquant_sample.

    All PacBio runs get a pacbio_sample row (barcode, twist adaptor, synDNA
    flag).  Only absquant runs (those whose Data rows contain absquant
    columns like mass_syndna_input_ng) also get a metagenomic_absquant_sample
    row.

    Args:
        cur: An open SQLite cursor.
        cs_id: The compression_sample_id for this sample.
        row: A single Data-section row dict containing PacBio-specific
            columns.
    """
    # Parse syndna_is_twisted boolean (may be empty or absent).
    twisted = _parse_bool_str(row.get(COL_SYNDNA_IS_TWISTED), nullable=True)

    cur.execute(
        "INSERT INTO pacbio_sample "
        "(compression_sample_id, barcode_id, twist_adaptor_id, syndna_is_twisted) "
        "VALUES (?, ?, ?, ?)",
        (
            cs_id,
            row.get(COL_BARCODE_ID, ""),
            row.get(COL_TWIST_ADAPTOR_ID) or None,
            twisted,
        ),
    )

    # AbsQuant columns are only present in absquant sheet types.
    if COL_MASS_SYNDNA_INPUT not in row:
        return

    mass = float(row[COL_MASS_SYNDNA_INPUT]) if row.get(COL_MASS_SYNDNA_INPUT) else None
    conc = (
        float(row[COL_EXTRACTED_GDNA_CONC])
        if row.get(COL_EXTRACTED_GDNA_CONC)
        else None
    )

    cur.execute(
        "INSERT INTO metagenomic_absquant_sample "
        "(compression_sample_id, syndna_pool_mass_ng, "
        " extracted_gdna_concentration, syndna_pool_number) "
        "VALUES (?, ?, ?, ?)",
        (cs_id, mass, conc, row.get(COL_SYNDNA_POOL_NUMBER) or None),
    )


def _populate_illumina_sample(cur, cs_id: int, row: dict):
    """Insert an illumina_sample row with i5/i7 index information.

    Args:
        cur: An open SQLite cursor.
        cs_id: The compression_sample_id for this sample.
        row: A single Data-section row dict containing Illumina index
            columns (I7_Index_ID, index, I5_Index_ID, index2).
    """
    cur.execute(
        "INSERT INTO illumina_sample "
        "(compression_sample_id, i7_index_id, i7_sequence, i5_index_id, i5_sequence) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            cs_id,
            row.get(COL_I7_INDEX_ID, ""),
            row.get(COL_INDEX, ""),
            row.get(COL_I5_INDEX_ID, ""),
            row.get(COL_INDEX2, ""),
        ),
    )
