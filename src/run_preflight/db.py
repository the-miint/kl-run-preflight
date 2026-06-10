"""Create the SQLite database and populate it from parsed omnibus data.

The schema DDL lives in ``schema.sql`` alongside this module so it can be
read and edited independently of the Python code.
"""

from __future__ import annotations

import sqlite3
import warnings
from itertools import groupby
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


def get_single_run_idx(conn: sqlite3.Connection) -> int:
    """Return the run_idx of the sole processing_run in *conn*.

    Raises:
        ValueError: If zero or multiple processing_run rows exist.
    """
    run_idxs = [row[0] for row in conn.execute("SELECT run_idx FROM processing_run")]
    if len(run_idxs) != 1:
        raise ValueError(f"Expected exactly one processing run, found {len(run_idxs)}")
    return run_idxs[0]


def get_projects_missing_external_id(
    conn: sqlite3.Connection,
    run_idx: int,
) -> list[str]:
    """Return the names of projects reachable from *run_idx* that have NULL
    external_project_id.

    Both the primary plate project and any per-sample (secondary)
    project are in scope.  The list is sorted by project_name; the
    list is empty when every reachable project has a non-NULL value.
    """
    # Primary projects: reachable via input_plate.primary_project_idx.
    # Secondary projects: reachable via input_sample.project_idx.
    cur = conn.execute(
        """
        SELECT DISTINCT p.project_name
        FROM project p
        JOIN input_plate ip ON ip.primary_project_idx = p.project_idx
        JOIN input_sample ins ON ins.input_plate_idx = ip.input_plate_idx
        JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
        WHERE cs.run_idx = ? AND p.external_project_id IS NULL
        UNION
        SELECT DISTINCT p.project_name
        FROM project p
        JOIN input_sample ins ON ins.project_idx = p.project_idx
        JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
        WHERE cs.run_idx = ? AND p.external_project_id IS NULL
        ORDER BY project_name
        """,
        (run_idx, run_idx),
    )
    return [name for (name,) in cur.fetchall()]


def get_section_formats(conn: sqlite3.Connection) -> dict[str, str]:
    """Return a mapping of section name to section format from the DB.

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


def get_legacy_format_idx(cur, sheet_type: str, sheet_version: int) -> int | None:
    """Return the legacy_format_idx for (sheet_type, sheet_version), or None.

    Args:
        cur: An open SQLite cursor.
        sheet_type: The legacy SheetType string.
        sheet_version: The legacy SheetVersion integer.

    Returns:
        int | None: The matching legacy_format_idx, or None if no row
        matches.
    """
    cur.execute(
        "SELECT legacy_format_idx FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (sheet_type, sheet_version),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_format_sections(cur, legacy_format_idx: int) -> list[tuple[str, str, str]]:
    """Return ordered (section_name, view_name, section_format) tuples.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The legacy format identifier.

    Returns:
        list[tuple[str, str, str]]: One tuple per section in
        section_order, each (section_name, view_name, section_format).
    """
    cur.execute(
        "SELECT section_name, view_name, section_format "
        "FROM legacy_samplesheet_view "
        "WHERE legacy_format_idx = ? ORDER BY section_order",
        (legacy_format_idx,),
    )
    return cur.fetchall()


def get_run_legacy_format(cur, run_idx: int) -> tuple[int, str, int] | None:
    """Return (legacy_format_idx, sheet_type, version) for the run, or None.

    Args:
        cur: An open SQLite cursor.
        run_idx: The processing_run.run_idx.

    Returns:
        tuple[int, str, int] | None: The format triple, or None if the
        run has no legacy format assigned.
    """
    cur.execute(
        "SELECT lf.legacy_format_idx, lf.legacy_sheet_type, lf.legacy_version "
        "FROM processing_run sr "
        "JOIN legacy_samplesheet_format lf "
        "ON sr.legacy_format_idx = lf.legacy_format_idx "
        "WHERE sr.run_idx = ?",
        (run_idx,),
    )
    return cur.fetchone()


def get_optional_columns_by_section(cur, legacy_format_idx: int) -> dict[str, set[str]]:
    """Return {section_name: set of optional column names} for a format.

    Columns from multiple groups targeting the same section accumulate
    into a single set.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The legacy format identifier.

    Returns:
        dict[str, set[str]]: Section name to the set of optional column
        names declared for that section.
    """
    cur.execute(
        "SELECT section_name, column_names "
        "FROM legacy_samplesheet_optional_columns "
        "WHERE legacy_format_idx = ?",
        (legacy_format_idx,),
    )
    result: dict[str, set[str]] = {}
    for section_name, col_names_csv in cur.fetchall():
        cols = {c.strip() for c in col_names_csv.split(",")}
        result.setdefault(section_name, set()).update(cols)
    return result


def get_optional_column_groups(
    cur, legacy_format_idx: int, section_name: str
) -> list[tuple[str, str, str]]:
    """Return (group_name, column_names_csv, check_function) per group.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The legacy format identifier.
        section_name: The section whose optional groups to fetch.

    Returns:
        list[tuple[str, str, str]]: One row per optional column group
        defined on the given section, in DB row order.
    """
    cur.execute(
        "SELECT group_name, column_names, check_function "
        "FROM legacy_samplesheet_optional_columns "
        "WHERE legacy_format_idx = ? AND section_name = ?",
        (legacy_format_idx, section_name),
    )
    return cur.fetchall()


def lookup_input_samples_by_name(cur, sample_name: str) -> list[tuple[int, str | None]]:
    """Return distinct (input_sample_idx, biosample_accession) rows by Sample_Name.

    Resolves Sample_Name via the legacy rule (replicate aliases collapse
    via DISTINCT to a single input_sample).

    Args:
        cur: An open SQLite cursor.
        sample_name: The effective Sample_Name to match.

    Returns:
        list[tuple[int, str | None]]: Distinct matching rows. Empty list
        means no match; multiple rows mean the name is ambiguous.
    """
    cur.execute(
        "SELECT DISTINCT ins.input_sample_idx, ins.biosample_accession "
        "FROM input_sample ins "
        "JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx "
        "JOIN prepped_sample prs ON prs.compression_sample_idx = cs.compression_sample_idx "
        "JOIN prepped_sample_name psn ON prs.prepped_sample_idx = psn.prepped_sample_idx "
        "WHERE psn.sample_name = ?",
        (sample_name,),
    )
    return cur.fetchall()


def lookup_projects_by_key(
    cur, key_col: str, key_value: str
) -> list[tuple[int, str | None]]:
    """Return (project_idx, bioproject_accession) rows where key_col = key_value.

    *key_col* must be "project_name" or "external_project_id" (closed
    set; interpolated into SQL).

    Args:
        cur: An open SQLite cursor.
        key_col: The project lookup key column.
        key_value: The value to match in *key_col*.

    Returns:
        list[tuple[int, str | None]]: Matching rows. Empty list means
        no match; multiple rows mean the key resolved ambiguously.

    Raises:
        ValueError: If *key_col* is not a supported lookup column.
    """
    if key_col not in ("project_name", "external_project_id"):
        raise ValueError(f"Unsupported key_col {key_col!r}")
    cur.execute(
        f"SELECT project_idx, bioproject_accession FROM project WHERE {key_col} = ?",
        (key_value,),
    )
    return cur.fetchall()


# Error categories and per-row labels for invariant/accession violations.
ERR_CATEGORY_INVARIANT = "control / project_idx invariant violation"
ERR_CATEGORY_MISSING_ACCESSION = "missing required accession"
LABEL_STANDARD_NO_PROJECT = "standard sample_type with NULL project_idx"
LABEL_NONSTANDARD_WITH_PROJECT = "non-standard sample_type with non-NULL project_idx"


def _raise_violations(
    category: str,
    offenders: list[tuple[int, str]],
) -> None:
    """Raise ValueError summarizing per-row violations, if any.

    Each offender is (illumina_sample_idx, label) describing what is
    wrong on that row; no-op when *offenders* is empty.
    """
    if not offenders:
        return
    items = ", ".join(
        f"illumina_sample_idx={idx} ({label})" for idx, label in offenders
    )
    raise ValueError(f"{category}: {items}")


def get_illumina_sample_info(
    conn: sqlite3.Connection,
) -> list[tuple[int, str, str, list[str]]]:
    """Return per-illumina_sample biosample + bioproject info for the run.

    Resolves the sole processing_run via get_single_run_idx and returns
    one tuple per illumina_sample row, ordered by illumina_sample_idx:
    (illumina_sample_idx, biosample_accession,
    primary_bioproject_accession, secondary_bioproject_accessions),
    where secondary_bioproject_accessions is a list of accessions for
    every non-primary plate project (populated only for controls;
    empty for non-control samples), sorted by accession value.

    Raises:
        ValueError: If the control / project_idx pairing is violated
            on any row (raised before any accession check), or if any
            required accession (biosample, primary bioproject, or any
            secondary bioproject) is None on any row.
    """
    run_idx = get_single_run_idx(conn)
    cur = conn.cursor()

    # One row per (illumina_sample x non-primary plate project); LEFT
    # JOINs keep a single row for non-controls / single-project plates.
    # The leading ORDER BY illumina_sample_idx is load-bearing: the
    # groupby() below relies on adjacent same-key rows.
    cur.execute(
        """
        SELECT
            ris.illumina_sample_idx,
            ins.project_idx,
            ins.biosample_accession,
            st.name,
            COALESCE(own_proj.bioproject_accession,
                     primary_proj.bioproject_accession),
            ipp.project_idx,
            secondary_proj.bioproject_accession
        FROM run_illumina_sample ris
        JOIN input_sample ins
            ON ris.input_sample_idx = ins.input_sample_idx
        JOIN sample_type st
            ON ins.sample_type_idx = st.sample_type_idx
        JOIN input_plate ip
            ON ins.input_plate_idx = ip.input_plate_idx
        JOIN project primary_proj
            ON ip.primary_project_idx = primary_proj.project_idx
        LEFT JOIN project own_proj
            ON ins.project_idx = own_proj.project_idx
        LEFT JOIN input_plate_projects ipp
            ON ipp.input_plate_idx = ins.input_plate_idx
            AND ins.project_idx IS NULL
            AND ipp.project_idx != ip.primary_project_idx
        LEFT JOIN project secondary_proj
            ON ipp.project_idx = secondary_proj.project_idx
        WHERE ris.run_idx = ?
        ORDER BY ris.illumina_sample_idx, secondary_proj.bioproject_accession
        """,
        (run_idx,),
    )
    rows = cur.fetchall()

    # Group result rows by illumina_sample_idx and validate per-group.
    invariant_offenders: list[tuple[int, str]] = []
    accession_offenders: list[tuple[int, str]] = []
    results: list[tuple[int, str, str, list[str]]] = []
    for ils_idx, group in groupby(rows, key=lambda r: r[0]):
        group_rows = list(group)
        _, project_idx, biosample, st_name, primary_bp, _, _ = group_rows[0]

        # Enforce control / project_idx pairing before reading accessions
        is_standard = st_name == SAMPLE_TYPE_STANDARD
        has_project = project_idx is not None
        if is_standard and not has_project:
            invariant_offenders.append((ils_idx, LABEL_STANDARD_NO_PROJECT))
            continue
        if not is_standard and has_project:
            invariant_offenders.append((ils_idx, LABEL_NONSTANDARD_WITH_PROJECT))
            continue

        # Collect non-primary plate projects' bioproject_accessions
        secondary = [r[6] for r in group_rows if r[5] is not None]

        # Record any missing accession for the summary report
        if biosample is None:
            accession_offenders.append((ils_idx, "biosample_accession"))
        if primary_bp is None:
            accession_offenders.append((ils_idx, "primary_bioproject_accession"))
        if any(b is None for b in secondary):
            accession_offenders.append((ils_idx, "secondary_bioproject_accessions"))

        results.append((ils_idx, biosample, primary_bp, secondary))

    # Invariant violations indicate corrupt data; raise before accession checks
    _raise_violations(ERR_CATEGORY_INVARIANT, invariant_offenders)
    _raise_violations(ERR_CATEGORY_MISSING_ACCESSION, accession_offenders)
    return results


def get_illumina_sample_rows(
    conn: sqlite3.Connection,
) -> list[tuple[int, int | None, str, str, str, str]]:
    """Return per-illumina_sample data tuples for the sole processing run.

    Each tuple is (illumina_sample_idx, lane, i7_sequence, i5_sequence,
    project_name, sample_name), ordered by illumina_sample_idx.
    sample_name follows the legacy rule: prepped_sample.sample_name when
    populated for a replicate, else input_sample.sample_name.

    Raises:
        ValueError: If *conn* lacks exactly one processing run.
    """
    run_idx = get_single_run_idx(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT illumina_sample_idx, lane, i7_sequence, i5_sequence, "
        "project_name, sample_name "
        "FROM run_illumina_sample "
        "WHERE run_idx = ? "
        "ORDER BY illumina_sample_idx",
        (run_idx,),
    )
    return cur.fetchall()


def get_illumina_settings(
    conn: sqlite3.Connection,
) -> dict[str, str | None]:
    """Return the [Settings] dict for the sole illumina_run.

    Keys are the public [Settings] field names (MaskShortReads,
    OverrideCycles); values are None for any column that is NULL on
    illumina_run.

    Raises:
        ValueError: If *conn* lacks exactly one processing run.
    """
    run_idx = get_single_run_idx(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT mask_short_reads, override_cycles FROM illumina_run WHERE run_idx = ?",
        (run_idx,),
    )
    mask_short_reads, override_cycles = cur.fetchone()
    return {
        FIELD_MASK_SHORT_READS: mask_short_reads,
        FIELD_OVERRIDE_CYCLES: override_cycles,
    }


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


def _opt_float(row: dict, col: str) -> float | None:
    """Return ``float(row[col])`` or None if the column is absent/empty."""
    val = row.get(col)
    return float(val) if val else None


def _opt_int(row: dict, col: str) -> int | None:
    """Return ``int(row[col])`` or None if the column is absent/empty."""
    val = row.get(col)
    return int(val) if val else None


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
    processing run, and all sample rows (with platform- and protocol-
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
    instrument_type = SEQUENCER_PACBIO_REVIO if is_pacbio else SEQUENCER_UNKNOWN

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
    legacy_format_idx = get_legacy_format_idx(cur, sheet_type, sheet_version)

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
               (project_name, external_project_id, contact_email,
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

    # -- Insert processing run ----------------------------------------------
    cur.execute(
        """INSERT INTO processing_run
           (experiment_name, run_date, investigator_name, instrument_type,
            assay_type_idx, platform_idx, compression_plate_name,
            description, legacy_format_idx)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            header.get(FIELD_EXPERIMENT_NAME, ""),
            header.get(FIELD_DATE, ""),
            header.get(FIELD_INVESTIGATOR_NAME, ""),
            instrument_type,
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
                   (sample_name, input_plate_idx, project_idx, sample_type_idx)
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
    """Insert a single illumina_run row for the given processing run.

    Combines data from the Reads, Settings, and Bioinformatics sections to
    populate read lengths, reverse-complement flag, adapter sequences, and
    other Illumina-specific run configuration.

    Args:
        cur: An open SQLite cursor.
        run_idx: The processing_run.run_idx to associate the row with.
        sections: The full parsed-sections dict (used to read "Reads"
            and "Settings").
        bio_rows: The list of Bioinformatics row dicts (adapter info is
            taken from the first row).
    """
    reads = sections.get(SECTION_READS, [])
    settings = sections.get(SECTION_SETTINGS, {})

    read1 = int(reads[0]) if len(reads) > 0 else 0
    read2 = int(reads[1]) if len(reads) > 1 else 0

    # ReverseComplement is optional in Settings; absent values are stored
    # as NULL so reconstruction NULL-skips and round-trips byte-equal.
    rc_bool = _parse_bool_str(settings.get(FIELD_REVERSE_COMPLEMENT), nullable=True)

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
            _opt_float(row, COL_MASS_SYNDNA_INPUT),
            _opt_float(row, COL_EXTRACTED_GDNA_CONC),
            row.get(COL_SYNDNA_POOL_NUMBER) or None,
            _opt_float(row, COL_SEQUENCED_SAMPLE_GDNA_MASS),
            _opt_float(row, COL_EXTRACTED_SAMPLE_MASS),
            _opt_float(row, COL_EXTRACTED_SAMPLE_VOLUME),
            _opt_float(row, COL_EXTRACTED_SAMPLE_SURFACE_AREA),
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

    cur.execute(
        "INSERT INTO metatranscriptomic_sample "
        "(prepped_sample_idx, total_rna_concentration_ng_ul) "
        "VALUES (?, ?)",
        (prs_idx, _opt_float(row, COL_TOTAL_RNA_CONC)),
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
    cur.execute(
        "INSERT INTO tellseq_sample "
        "(prepped_sample_idx, barcode_id, lane) "
        "VALUES (?, ?, ?)",
        (prs_idx, row.get(COL_BARCODE_ID, ""), _opt_int(row, COL_LANE)),
    )


def _populate_illumina_sample(cur, prs_idx: int, row: dict):
    """Insert an illumina_sample row with i5/i7 index information.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict containing Illumina index
            columns (I7_Index_ID, index, I5_Index_ID, index2).
    """
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
            _opt_int(row, COL_LANE),
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
