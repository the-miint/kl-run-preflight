"""Create the SQLite database and populate it from parsed omnibus data.

The schema DDL lives in ``schema.sql`` alongside this module so it can be
read and edited independently of the Python code.
"""

from __future__ import annotations

import os
import sqlite3
import warnings
from itertools import groupby
from pathlib import Path
from typing import NamedTuple, get_args

from .legacy import LegacyExtraColumnWarning
from .migrate import get_latest_version
from .constants import (
    EMP_515F_PRIMER,
    COL_AMPLICON_BARCODE,
    COL_AMPLICON_LINKER,
    COL_AMPLICON_PCR_PRIMERS,
    COL_AMPLICON_PRIMER,
    COL_AMPLICON_SEQUENCING_METH,
    COL_AMPLICON_TARGET_GENE,
    COL_AMPLICON_TARGET_SUBFRAGMENT,
    COL_BARCODE_ID,
    COL_CONTAINS_REPLICATES,
    COL_EXTRACTED_SAMPLE_MASS,
    COL_EXTRACTED_SAMPLE_SURFACE_AREA,
    COL_EXTRACTED_SAMPLE_VOLUME,
    COL_EXTRACTION_ROBOT,
    COL_EXTRACTIONKIT_LOT,
    COL_SEQUENCED_SAMPLE_GDNA_MASS,
    LEGACY_COLUMN_ALIASES,
    COL_KATHAROSEQ_NUMBER_OF_CELLS,
    COL_KATHAROSEQ_RACK_ID,
    COL_LANE,
    RESERVED_VIEW_COLUMNS,
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
    COL_INSTRUMENT_MODEL,
    COL_LIBRARY_CONSTRUCTION_PROTOCOL,
    COL_MASS_SYNDNA_INPUT,
    COL_MATRIX_TUBE_ID,
    COL_PLATE_CONTENTS_DESCRIPTION,
    COL_PLATEMAP_GENERATION_DATE,
    COL_PLATING,
    COL_PRIMER_PLATE,
    COL_ORIG_NAME,
    COL_QIITA_ID,
    COL_REVERSE_ADAPTER,
    COL_SAMPLE_PROJECT,
    COL_SC_SAMPLE_NAME,
    COL_SC_SAMPLE_TYPE,
    COL_SYNDNA_IS_TWISTED,
    COL_SYNDNA_POOL_NUMBER,
    COL_TOTAL_RNA_CONC,
    COL_TWIST_ADAPTOR_ID,
    COL_VOL_EXTRACTED_ELUTION,
    COL_WELL_ID_96,
    CONTEXT_TYPE_MAP,
    DO_NOT_USE_TOKEN,
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
    IN_MEMORY_PATH,
    PLATFORM_ILLUMINA,
    PlatformSpecificSampleKind,
    SAMPLE_TYPE_STANDARD,
    SECTION_BIOINFORMATICS,
    SECTION_CONTACT,
    SECTION_DATA,
    SECTION_HEADER,
    SECTION_READS,
    SECTION_SAMPLE_CONTEXT,
    SECTION_SETTINGS,
    SHEET_TYPE_AMPLICON,
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
            created, or ``:memory:`` for a transient database. An
            existing file is rejected rather than reused or overwritten.

    Returns:
        sqlite3.Connection: An open connection to the new database with
        foreign-key enforcement enabled.

    Raises:
        FileExistsError: If a file already exists at *db_path*.
    """
    # The schema DDL is unguarded, so reusing an existing file would either
    # collide with its tables or graft a second schema onto foreign data
    if db_path != IN_MEMORY_PATH and os.path.lexists(db_path):
        raise FileExistsError(f"refusing to overwrite existing file: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_load_schema_sql())
    # Stamp the database with the current schema version
    conn.execute(f"PRAGMA user_version = {get_latest_version()}")
    return conn


# ---------------------------------------------------------------------------
# View introspection
# ---------------------------------------------------------------------------


def introspect_view(cur, view_name: str) -> tuple[list[str], frozenset[str]]:
    """Return column names (excluding run_idx) and whether run_idx exists.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        tuple[list[str], frozenset[str]]: the view's emitted column names,
        and which reserved columns it carries.
    """
    cur.execute(f"PRAGMA table_info({view_name})")
    names = [row[1] for row in cur.fetchall()]
    cols = [name for name in names if name not in RESERVED_VIEW_COLUMNS]
    reserved_present = RESERVED_VIEW_COLUMNS & set(names)
    return cols, reserved_present


def get_view_columns(cur, view_name: str) -> list[str]:
    """Return the column names a SQL view emits, excluding reserved ones.

    Args:
        cur: An open SQLite cursor.
        view_name: Name of the SQL view to introspect.

    Returns:
        list[str]: Ordered list of column names from the view, with the
        reserved columns omitted.
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


def get_run_projects(
    conn: sqlite3.Connection,
    run_idx: int,
) -> list[tuple[str, str | None]]:
    """Return (project_name, external_project_id) for every project in *run_idx*.

    Both the primary plate project and any per-sample (secondary)
    project are in scope, sorted by project_name. external_project_id
    is None for a reachable project that has none set.
    """
    # Primary projects: reachable via input_plate.primary_project_idx.
    # Secondary projects: reachable via input_sample.project_idx.
    cur = conn.execute(
        """
        SELECT DISTINCT p.project_name, p.external_project_id
        FROM project p
        JOIN input_plate ip ON ip.primary_project_idx = p.project_idx
        JOIN input_sample ins ON ins.input_plate_idx = ip.input_plate_idx
        JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
        WHERE cs.run_idx = ?
        UNION
        SELECT DISTINCT p.project_name, p.external_project_id
        FROM project p
        JOIN input_sample ins ON ins.project_idx = p.project_idx
        JOIN compression_sample cs ON cs.input_sample_idx = ins.input_sample_idx
        WHERE cs.run_idx = ?
        ORDER BY project_name
        """,
        (run_idx, run_idx),
    )
    return cur.fetchall()


class PreflightDataFacts(NamedTuple):
    """Which classes of post-preflight data a run preflight has populated.

    has_accessions is true when any NCBI biosample or bioproject accession is
    set; has_pacbio_placement is true when any PacBio SMRT Cell placement is
    set. Both false denotes a true preflight.
    """

    has_accessions: bool
    has_pacbio_placement: bool


def get_preflight_data_facts(conn: sqlite3.Connection) -> PreflightDataFacts:
    """Return which classes of accrued data are populated in the database.

    Derives each fact from what the database actually holds, so a caller
    (e.g. a fixture classifier) need not know the underlying columns.
    """
    cur = conn.cursor()

    # Accessions: any biosample on a sample or bioproject on a project
    cur.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM input_sample WHERE biosample_accession IS NOT NULL) "
        "+ (SELECT COUNT(*) FROM project WHERE bioproject_accession IS NOT NULL)"
    )
    (accession_count,) = cur.fetchone()

    # PacBio placement: any SMRT Cell well or movie-context assignment
    cur.execute(
        "SELECT COUNT(*) FROM pacbio_sample "
        "WHERE smrt_cell_well_sample_id IS NOT NULL OR movie_context_id IS NOT NULL"
    )
    (placement_count,) = cur.fetchone()

    has_accessions = bool(accession_count)
    has_pacbio_placement = bool(placement_count)
    return PreflightDataFacts(has_accessions, has_pacbio_placement)


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
    return [
        name
        for name, external_project_id in get_run_projects(conn, run_idx)
        if external_project_id is None
    ]


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


class FormatLoadConfig(NamedTuple):
    """What a legacy format declares about loading a file of that format.

    sample_kind is None for a format whose runs have no platform-specific
    per-sample rows; callers populate no such table in that case. The
    *_column fields name the Data columns this format uses for each fact,
    which differ between formats.
    """

    platform_idx: int
    platform_name: str
    default_instrument_type: str
    sample_kind: str | None
    sample_name_column: str
    plate_column: str
    project_column: str
    well_description_column: str
    well_column: str
    replicates_supported: bool


def get_format_load_config(cur, legacy_format_idx: int) -> FormatLoadConfig:
    """Return the load configuration a legacy format declares.

    Raises:
        ValueError: If the format is unknown, or declares no platform or
            instrument. A guard test keeps every registered row complete,
            so this signals a registry gap rather than a caller error.
    """
    cur.execute(
        "SELECT f.platform_idx, sp.name, f.default_instrument_type, f.sample_kind, "
        " f.sample_name_column, f.plate_column, f.project_column, "
        " f.well_description_column, f.well_column, f.replicates_supported "
        "FROM legacy_samplesheet_format f "
        "JOIN sequencing_platform sp ON f.platform_idx = sp.platform_idx "
        "WHERE f.legacy_format_idx = ?",
        (legacy_format_idx,),
    )
    row = cur.fetchone()
    if row is None or row[0] is None or row[2] is None:
        raise ValueError(
            f"Legacy format {legacy_format_idx} declares no platform or "
            "instrument; it cannot be loaded"
        )
    config = FormatLoadConfig(*row)
    return config


def get_amplicon_format_for_header(cur, header: list[str]) -> tuple[int, int]:
    """Resolve a flat amplicon prep template's header to its registered format.

    These sheets carry no SheetType or SheetVersion, so the column header is
    the only signal. A format matches when its Data view's typed columns --
    minus any optional group the sheet omits -- are all present in *header*;
    the match declaring the most typed columns wins, which resolves the case
    of one layout's typed set being a subset of a later layout's.

    Returns:
        tuple[int, int]: the legacy_format_idx and legacy_version.

    Raises:
        ValueError: If no registered format matches, naming the header so the
            unrecognised layout can be registered.
    """
    header_set = set(header)
    formats = cur.execute(
        "SELECT f.legacy_format_idx, f.legacy_version, lv.view_name "
        "FROM legacy_samplesheet_format f "
        "JOIN legacy_samplesheet_view lv ON f.legacy_format_idx = lv.legacy_format_idx "
        "WHERE f.legacy_sheet_type = ? AND lv.section_name = ? "
        "ORDER BY f.legacy_version",
        (SHEET_TYPE_AMPLICON, SECTION_DATA),
    ).fetchall()

    # Widest match wins; an optional group the sheet omits is subtracted first.
    best: tuple[int, int, int] | None = None
    for legacy_format_idx, version, view_name in formats:
        typed = set(get_view_columns(cur, view_name))
        optional = get_optional_columns_by_section(cur, legacy_format_idx)
        candidate = typed - (optional.get(SECTION_DATA, set()) - header_set)
        if candidate <= header_set and (best is None or len(candidate) > best[2]):
            best = (legacy_format_idx, version, len(candidate))

    if best is None:
        raise ValueError(
            "No registered amplicon format matches this prep template's header; "
            f"columns were: {header}"
        )
    return best[0], best[1]


def get_format_file_shape(cur, legacy_format_idx: int) -> tuple[str, bool]:
    """Return a format's field delimiter and whether it carries section labels.

    Args:
        cur: An open SQLite cursor.
        legacy_format_idx: The legacy format identifier.

    Returns:
        tuple[str, bool]: the delimiter, and whether the file labels its
        sections. A file with no labels holds exactly one written section.
    """
    cur.execute(
        "SELECT delimiter, has_section_labels FROM legacy_samplesheet_format "
        "WHERE legacy_format_idx = ?",
        (legacy_format_idx,),
    )
    delimiter, has_section_labels = cur.fetchone()
    return delimiter, bool(has_section_labels)


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


def _do_not_use_filter(include_do_not_use: bool, alias: str = "") -> str:
    """Return an ``AND <alias>do_not_use = 0`` SQL clause, or ``""``.

    Returns the empty string when *include_do_not_use* is True (no
    filtering). *alias* is a table/view alias prefix ending in ``.`` (or
    ``""`` for an unqualified column); callers place the clause between
    the surrounding WHERE and ORDER BY fragments.
    """
    if include_do_not_use:
        return ""
    return f"AND {alias}do_not_use = 0"


def get_input_sample_project_info(
    conn: sqlite3.Connection,
    *,
    include_do_not_use: bool = False,
) -> list[tuple[str, str | None, bool]]:
    """Return distinct (sample_name, external_project_id, is_control) rows.

    Resolves the sole processing_run via get_single_run_idx and returns
    one row per distinct triple, ordered by sample_name; rows sharing all
    three collapse, so both replicates and a sample appearing on multiple
    plates yield a single row. external_project_id is that of the sample's
    effective project (its own project, or the plate's primary project for
    controls); is_control is True when input_sample.project_idx is NULL.

    Unless *include_do_not_use* is True, prep rows whose effective
    do_not_use flag is set are dropped before the collapse, so a sample
    disappears only when every one of its preps is flagged.
    """
    run_idx = get_single_run_idx(conn)
    do_not_use_filter = _do_not_use_filter(include_do_not_use, "psn.")

    # Reuse prepped_sample_project's effective-project resolution, then map
    # back to the input_sample for its sample_name and join out to the
    # project for its external_project_id; DISTINCT collapses replicate rows.
    cur = conn.execute(
        f"""
        SELECT DISTINCT ins.sample_name,
               proj.external_project_id,
               ins.project_idx IS NULL
        FROM prepped_sample_project psp
        JOIN prepped_sample prs ON prs.prepped_sample_idx = psp.prepped_sample_idx
        JOIN prepped_sample_name psn
            ON psn.prepped_sample_idx = prs.prepped_sample_idx
        JOIN compression_sample cs
            ON prs.compression_sample_idx = cs.compression_sample_idx
        JOIN input_sample ins ON cs.input_sample_idx = ins.input_sample_idx
        JOIN project proj ON psp.project_idx = proj.project_idx
        WHERE cs.run_idx = ?
        {do_not_use_filter}
        ORDER BY ins.sample_name
        """,
        (run_idx,),
    )
    return [
        (name, ext_id, bool(is_control)) for name, ext_id, is_control in cur.fetchall()
    ]


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


class _SampleKindNames(NamedTuple):
    """Derived DB object names for a platform-specific sample kind."""

    table: str
    idx_col: str
    run_view: str


def sample_kind_names(kind: PlatformSpecificSampleKind) -> _SampleKindNames:
    """Derive the DB object names for a platform-specific sample kind.

    Every ``<kind>_sample`` table and its primary-key column follow one
    naming convention, so both derive from the kind token; the run-scoped
    view ``run_<kind>_sample`` follows the same convention but exists only
    for kinds surfaced through a run view (illumina, pacbio). A convention
    guard test checks that each declared kind's table and idx column exist.

    Raises:
        ValueError: If *kind* is not a declared PlatformSpecificSampleKind.
    """
    valid_kinds = get_args(PlatformSpecificSampleKind)
    if kind not in valid_kinds:
        raise ValueError(
            f"Unknown sample kind {kind!r}; expected one of {', '.join(valid_kinds)}"
        )
    table = f"{kind}_sample"
    idx_col = f"{kind}_sample_idx"
    run_view = f"run_{kind}_sample"
    return _SampleKindNames(table, idx_col, run_view)


class PacbioSampleRow(NamedTuple):
    """PacBio-specific columns of one pacbio_sample row.

    Field names match the pacbio_sample DB columns; the field order is
    load-bearing — it drives the SELECT column list built in
    _get_platform_specific_sample_info. Build instances from a raw run-view
    value tuple via from_run_view, which applies storage-to-domain coercion.
    """

    barcode_id: str
    twist_adaptor_id: str | None
    syndna_is_twisted: bool | None
    smrt_cell_well_sample_id: str | None
    movie_context_id: str | None

    @classmethod
    def sample_kind(cls) -> PlatformSpecificSampleKind:
        """Return the platform-specific sample kind this row represents."""
        return "pacbio"

    @classmethod
    def source_names(cls) -> _SampleKindNames:
        """DB object names for the sample source this row reads from."""
        return sample_kind_names(cls.sample_kind())

    @classmethod
    def from_run_view(cls, values: tuple) -> PacbioSampleRow:
        """Build from a run_pacbio_sample value tuple in field order."""
        row = cls._make(values)

        # syndna_is_twisted is a SQLite BOOLEAN stored as 0/1/NULL; surface
        # it to consumers as a genuine bool | None.
        stored = row.syndna_is_twisted
        is_twisted = None if stored is None else bool(stored)
        return row._replace(syndna_is_twisted=is_twisted)


class IlluminaSampleRow(NamedTuple):
    """Illumina-specific columns of one illumina_sample row.

    Field names match the illumina_sample DB columns; the field order is
    load-bearing — it drives the SELECT column list built in
    _get_platform_specific_sample_info. Build instances from a raw run-view
    value tuple via from_run_view.
    """

    i7_index_id: str
    i7_sequence: str
    i5_index_id: str
    i5_sequence: str
    lane: int | None

    @classmethod
    def sample_kind(cls) -> PlatformSpecificSampleKind:
        """Return the platform-specific sample kind this row represents."""
        return "illumina"

    @classmethod
    def source_names(cls) -> _SampleKindNames:
        """DB object names for the sample source this row reads from."""
        return sample_kind_names(cls.sample_kind())

    @classmethod
    def from_run_view(cls, values: tuple) -> IlluminaSampleRow:
        """Build from a run_illumina_sample value tuple in field order.

        No column needs storage-to-domain coercion, so this is a direct map.
        """
        return cls._make(values)


class AmpliconSampleRow(NamedTuple):
    """Amplicon-specific columns of one amplicon_sample row.

    Amplicon is deliberately not a PlatformSpecificSampleKind — a run carries a
    single in-line Golay barcode, not an i5/i7 pair — so this row reads from the
    run_amplicon_sample view directly rather than through the sample-kind naming
    convention, supplying its own source_names. barcode + barcodes_are_rc are the
    per-sample facts a golay-demux consumer needs (barcodes_are_rc is run-constant
    but surfaced per row for the roster). Field names match the view columns; the
    field order drives the SELECT built in _get_platform_specific_sample_info.
    """

    barcode: str
    barcodes_are_rc: bool

    @classmethod
    def source_names(cls) -> _SampleKindNames:
        """DB object names for the amplicon sample source.

        amplicon_sample has no surrogate key, so its per-sample handle is
        prepped_sample_idx rather than an <kind>_sample_idx.
        """
        return _SampleKindNames(
            table="amplicon_sample",
            idx_col="prepped_sample_idx",
            run_view="run_amplicon_sample",
        )

    @classmethod
    def from_run_view(cls, values: tuple) -> AmpliconSampleRow:
        """Build from a run_amplicon_sample value tuple in field order.

        barcodes_are_rc is a SQLite BOOLEAN stored as 0/1; surface it as bool.
        """
        row = cls._make(values)
        return row._replace(barcodes_are_rc=bool(row.barcodes_are_rc))


class PlatformSampleInfo(NamedTuple):
    """Per-sample accession + identity info for one platform sample row.

    Bundles the platform-independent identity and accession fields with
    *kind_row*, the platform-specific columns. sample_type is the DB
    sample_type.name (e.g. "standard", "extraction_blank");
    secondary_bioproject_accessions is populated only for project-agnostic
    controls added at prep-time and is empty for all other samples
    (including controls project-specific controls).
    """

    sample_idx: int
    sample_type: str
    biosample_accession: str
    primary_bioproject_accession: str
    secondary_bioproject_accessions: list[str]
    kind_row: IlluminaSampleRow | PacbioSampleRow | AmpliconSampleRow


# Error categories and per-row labels for invariant/accession violations.
ERR_CATEGORY_INVARIANT = "control / project_idx invariant violation"
ERR_CATEGORY_MISSING_ACCESSION = "missing required accession"
LABEL_STANDARD_NO_PROJECT = "standard sample_type with NULL project_idx"
LABEL_NONSTANDARD_WITH_PROJECT = "non-standard sample_type with non-NULL project_idx"


def _raise_violations(
    category: str,
    offenders: list[tuple[int, str]],
    idx_col_name: str,
) -> None:
    """Raise ValueError summarizing per-row violations, if any.

    Each offender is (sample_idx, label) describing what is wrong on that
    row; *idx_col_name* names the sample-idx column for the message. No-op
    when *offenders* is empty.
    """
    if not offenders:
        return
    items = ", ".join(f"{idx_col_name}={idx} ({label})" for idx, label in offenders)
    raise ValueError(f"{category}: {items}")


def _get_platform_specific_sample_info(
    conn: sqlite3.Connection,
    row_cls: type[PacbioSampleRow] | type[IlluminaSampleRow] | type[AmpliconSampleRow],
    *,
    include_do_not_use: bool = False,
) -> list[PlatformSampleInfo]:
    """Return per-sample biosample + bioproject accession info for the run.

    Resolves the sole processing_run via get_single_run_idx and returns
    one PlatformSampleInfo per *row_cls* sample row, ordered by the source's
    sample-idx. secondary_bioproject_accessions lists accessions for every
    non-primary plate project (populated only for controls; empty for
    non-control samples), sorted by accession value; kind_row is a *row_cls*
    instance carrying the platform-specific columns (its field order drives
    the SELECT). Rows whose effective do_not_use flag is set are excluded
    unless *include_do_not_use* is True.

    Raises:
        ValueError: If the control / project_idx pairing is violated
            on any row (raised before any accession check), or if any
            required accession (biosample, primary bioproject accession, or any
            secondary bioproject accession) is None on any row.
    """
    names = row_cls.source_names()
    run_idx = get_single_run_idx(conn)
    cur = conn.cursor()
    do_not_use_filter = _do_not_use_filter(include_do_not_use, "rs.")

    # Pull the platform-specific columns from the run view in field order,
    # so they land at fixed trailing positions in each result row.
    kind_select = ", ".join(f"rs.{field}" for field in row_cls._fields)

    # One row per (sample x non-primary plate project); LEFT JOINs keep a
    # single row for non-controls / single-project plates. The leading
    # ORDER BY on the sample-idx is load-bearing: the groupby() below
    # relies on adjacent same-key rows.
    cur.execute(
        f"""
        SELECT
            rs.{names.idx_col},
            ins.project_idx,
            ins.biosample_accession,
            st.name,
            COALESCE(own_proj.bioproject_accession,
                     primary_proj.bioproject_accession),
            ipp.project_idx,
            secondary_proj.bioproject_accession,
            {kind_select}
        FROM {names.run_view} rs
        JOIN input_sample ins
            ON rs.input_sample_idx = ins.input_sample_idx
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
        WHERE rs.run_idx = ?
        {do_not_use_filter}
        ORDER BY rs.{names.idx_col}, secondary_proj.bioproject_accession
        """,
        (run_idx,),
    )
    rows = cur.fetchall()

    # Group result rows by sample-idx and validate per-group.
    invariant_offenders: list[tuple[int, str]] = []
    accession_offenders: list[tuple[int, str]] = []
    results: list[PlatformSampleInfo] = []
    for sample_idx, group in groupby(rows, key=lambda r: r[0]):
        group_rows = list(group)
        first_row = group_rows[0]
        _, project_idx, biosample, sample_type_name, primary_bioproject = first_row[:5]

        # The kind columns are constant across a control's grouped rows;
        # read them from the first row and rebuild the platform-specific row
        # (from_run_view applies any storage-to-domain coercion).
        kind_row = row_cls.from_run_view(first_row[7:])

        # Enforce control / project_idx pairing before reading accessions
        is_standard = sample_type_name == SAMPLE_TYPE_STANDARD
        has_project = project_idx is not None
        if is_standard and not has_project:
            invariant_offenders.append((sample_idx, LABEL_STANDARD_NO_PROJECT))
            continue
        if not is_standard and has_project:
            invariant_offenders.append((sample_idx, LABEL_NONSTANDARD_WITH_PROJECT))
            continue

        # Collect non-primary plate projects' bioproject_accessions
        secondary = [r[6] for r in group_rows if r[5] is not None]

        # Record any missing accession for the summary report
        if biosample is None:
            accession_offenders.append((sample_idx, "biosample_accession"))
        if primary_bioproject is None:
            accession_offenders.append((sample_idx, "primary_bioproject_accession"))
        if any(b is None for b in secondary):
            accession_offenders.append((sample_idx, "secondary_bioproject_accessions"))

        results.append(
            PlatformSampleInfo(
                sample_idx,
                sample_type_name,
                biosample,
                primary_bioproject,
                secondary,
                kind_row,
            )
        )

    # Invariant violations indicate corrupt data; raise before accession checks
    _raise_violations(ERR_CATEGORY_INVARIANT, invariant_offenders, names.idx_col)
    _raise_violations(
        ERR_CATEGORY_MISSING_ACCESSION, accession_offenders, names.idx_col
    )
    return results


def get_illumina_sample_info(
    conn: sqlite3.Connection,
    *,
    include_do_not_use: bool = False,
) -> list[PlatformSampleInfo]:
    """Return per-illumina_sample accession + illumina row; see shared helper.

    Requires every referenced project to have its NCBI accessions populated; a
    preflight with a required accession still NULL raises ``ValueError``.
    """
    return _get_platform_specific_sample_info(
        conn, IlluminaSampleRow, include_do_not_use=include_do_not_use
    )


def get_pacbio_sample_info(
    conn: sqlite3.Connection,
    *,
    include_do_not_use: bool = False,
) -> list[PlatformSampleInfo]:
    """Return per-pacbio_sample accession + pacbio row; see shared helper.

    Requires every referenced project to have its NCBI accessions populated; a
    preflight with a required accession still NULL raises ``ValueError``.
    """
    return _get_platform_specific_sample_info(
        conn, PacbioSampleRow, include_do_not_use=include_do_not_use
    )


def get_amplicon_sample_info(
    conn: sqlite3.Connection,
    *,
    include_do_not_use: bool = False,
) -> list[PlatformSampleInfo]:
    """Return per-amplicon_sample accession + amplicon row; see shared helper.

    Requires every referenced project to have its NCBI accessions populated (a
    preflight with a required accession still NULL raises ``ValueError``), so it
    is the reader a consumer calls to provision a run keyed by biosample
    accession. kind_row is an AmpliconSampleRow carrying the sample's Golay
    barcode and barcodes_are_rc (all a golay-demux roster needs), and samples are
    keyed and ordered by prepped_sample_idx (amplicon_sample has no surrogate
    key).
    """
    return _get_platform_specific_sample_info(
        conn, AmpliconSampleRow, include_do_not_use=include_do_not_use
    )


class KatharoseqSampleInfo(NamedTuple):
    """One katharoseq positive-control sample's titration facts.

    Keyed by input_sample (a control is a plate-level input_sample, not a
    prepped_sample). ``number_of_cells`` is the known input for the
    read-count-vs-cells curve KatharoSeq fits.
    """

    input_sample_idx: int
    sample_name: str
    number_of_cells: int | None
    rack_id: str | None


def add_katharoseq_sample(
    conn: sqlite3.Connection,
    input_sample_idx: int,
    *,
    number_of_cells: int | None,
    rack_id: str | None = None,
) -> None:
    """Record the KatharoSeq titration facts for one control input_sample.

    Makes a KatharoSeq run explicit: the input_sample should already be typed
    ``katharoseq_cells_positive_control``. The tube barcode lives on
    ``input_sample.matrix_tube_id``, not here. Does not commit; the caller owns
    the transaction (matching the populate/seed helpers).
    """
    conn.execute(
        "INSERT INTO katharoseq_sample "
        "(input_sample_idx, rack_id, number_of_cells) "
        "VALUES (?, ?, ?)",
        (input_sample_idx, rack_id, number_of_cells),
    )


def get_katharoseq_sample_info(
    conn: sqlite3.Connection,
) -> list[KatharoseqSampleInfo]:
    """Return one row per katharoseq_sample, joined to its input_sample name,
    ordered by input_sample_idx."""
    cur = conn.execute(
        "SELECT k.input_sample_idx, ins.sample_name, k.number_of_cells, k.rack_id "
        "FROM katharoseq_sample k "
        "JOIN input_sample ins ON k.input_sample_idx = ins.input_sample_idx "
        "ORDER BY k.input_sample_idx"
    )
    return [KatharoseqSampleInfo(*row) for row in cur.fetchall()]


def get_illumina_sample_rows(
    conn: sqlite3.Connection,
    *,
    include_do_not_use: bool = False,
) -> list[tuple[int, int | None, str, str, str, str]]:
    """Return per-illumina_sample data tuples for the sole processing run.

    Each tuple is (illumina_sample_idx, lane, i7_sequence, i5_sequence,
    project_name, sample_name), ordered by illumina_sample_idx.
    sample_name follows the legacy rule: prepped_sample.sample_name when
    populated for a replicate, else input_sample.sample_name. Rows whose
    effective do_not_use flag is set are excluded unless
    *include_do_not_use* is True.

    Raises:
        ValueError: If *conn* lacks exactly one processing run.
    """
    run_idx = get_single_run_idx(conn)
    cur = conn.cursor()
    do_not_use_filter = _do_not_use_filter(include_do_not_use)
    cur.execute(
        "SELECT illumina_sample_idx, lane, i7_sequence, i5_sequence, "
        "project_name, sample_name "
        "FROM run_illumina_sample "
        "WHERE run_idx = ? "
        f"{do_not_use_filter} "
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

# Plate-constant prep facts, in the order input_plate stores them.
_PLATE_TIER_COLUMNS = (
    COL_PRIMER_PLATE,
    COL_PLATING,
    COL_EXTRACTIONKIT_LOT,
    COL_EXTRACTION_ROBOT,
    COL_PLATEMAP_GENERATION_DATE,
    COL_PLATE_CONTENTS_DESCRIPTION,
)


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
    replicates_supported: bool,
    sheet_version: int,
    data_rows: list[dict],
    bio_rows: list[dict],
) -> None:
    """Raise ValueError if a file whose format cannot carry replicates has them.

    Replicate well semantics changed at standard_metag v101; earlier
    versions of that family use well_id_384 in a way that cannot be
    round-tripped. Which formats those are is declared on the format
    rather than derived from the version number, which is comparable
    only within one format family.
    """
    if replicates_supported:
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


def _has_do_not_use_token(name: str | None) -> bool:
    """Return True if *name* carries the do-not-use token as a dot segment.

    Matching is case-insensitive against dot-delimited segments, so the
    token is recognized at any position but not as a substring of a
    larger word. None or empty yields False.
    """
    if not name:
        return False
    return DO_NOT_USE_TOKEN in name.lower().split(".")


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

    # -- Resolve the format, which declares how a file of it loads ----------
    sheet_type = header.get(FIELD_SHEET_TYPE, "")
    sheet_version = int(header.get(FIELD_SHEET_VERSION, 0))
    legacy_format_idx = get_legacy_format_idx(cur, sheet_type, sheet_version)
    if legacy_format_idx is None:
        raise ValueError(f"Unknown legacy format: {sheet_type} v{sheet_version}")
    load_config = get_format_load_config(cur, legacy_format_idx)

    # -- Build a lookup: sample_name → sample_type DB name ------------------
    # SampleContext tells us which samples are controls and their type.
    control_names: dict[str, str] = {}
    for row in context_rows:
        st = row.get(COL_SC_SAMPLE_TYPE, "")
        control_names[row[COL_SC_SAMPLE_NAME]] = CONTEXT_TYPE_MAP.get(st, st)

    # -- Resolve reference-table IDs ----------------------------------------
    assay_type_idx = _lookup_idx(cur, "assay_type", "name", header[FIELD_ASSAY])

    # Cache all sample_type IDs for quick lookup.
    cur.execute("SELECT sample_type_idx, name FROM sample_type")
    type_ids: dict[str, int] = {name: sid for sid, name in cur.fetchall()}

    # Reject pre-v101 files with replicates (unsupported well semantics)
    _reject_unsupported_replicates(
        load_config.replicates_supported, sheet_version, data_rows, bio_rows
    )

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
                bio.get(COL_EXPERIMENT_DESIGN_DESCRIPTION, ""),
            ),
        )
        assert cur.lastrowid is not None
        project_idxs[proj_name] = cur.lastrowid

    # -- Insert input plates ------------------------------------------------
    # Each unique Sample_Plate in the Data section becomes an input_plate.
    # The first project seen on that plate becomes primary_project_idx.
    plate_info: dict[str, dict] = {}
    for row in data_rows:
        pname = row[load_config.plate_column]
        if pname not in plate_info:
            plate_info[pname] = {
                "project": row[load_config.project_column],
                "elution_vol": row.get(COL_VOL_EXTRACTED_ELUTION),
                # Plate-constant prep facts; absent from the omnibus formats.
                "tier": tuple(
                    row.get(col) or None for col in _PLATE_TIER_COLUMNS
                ),
            }

    plate_idxs: dict[str, int] = {}
    for pname, info in plate_info.items():
        proj_id = project_idxs.get(info["project"])
        elution = float(info["elution_vol"]) if info["elution_vol"] else None
        cur.execute(
            "INSERT INTO input_plate "
            "(plate_name, primary_project_idx, elution_vol, primer_plate, "
            " plating, extractionkit_lot, extraction_robot, "
            " platemap_generation_date, plate_contents_description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pname, proj_id, elution, *info["tier"]),
        )
        assert cur.lastrowid is not None
        plate_idxs[pname] = cur.lastrowid

    # -- Insert processing run ----------------------------------------------
    # The format's declared instrument stands unless the sheet names one; an
    # empty value it does name is still what that sheet states.
    instrument_type = data_rows[0].get(
        COL_INSTRUMENT_MODEL, load_config.default_instrument_type
    )
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
            load_config.platform_idx,
            None,
            header.get(FIELD_DESCRIPTION, ""),
            legacy_format_idx,
        ),
    )
    assert cur.lastrowid is not None
    run_idx = cur.lastrowid

    # -- Illumina-specific run config (Reads + Settings + Bioinformatics) ---
    if load_config.platform_name == PLATFORM_ILLUMINA:
        _populate_illumina_run_from_sections(cur, run_idx, sections, bio_rows)
    _populate_amplicon_run(cur, run_idx, data_rows[0])

    # -- Insert samples -----------------------------------------------------
    # The format declares which column holds the well identifier.
    well_col = load_config.well_column
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
        sample_name = row[load_config.sample_name_column]
        plate_name = row[load_config.plate_column]
        well = row.get(well_col, "")
        project_name = row[load_config.project_column]

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
                sample_type_name = control_names[control_key]
                sample_project_idx = None
            else:
                sample_type_name = SAMPLE_TYPE_STANDARD
                sample_project_idx = project_idxs.get(project_name)

            cur.execute(
                """INSERT INTO input_sample
                   (sample_name, input_plate_idx, well, project_idx,
                    sample_type_idx, do_not_use, matrix_tube_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    orig_name,
                    plate_idxs[plate_name],
                    row.get(COL_WELL_ID_96) or None,
                    sample_project_idx,
                    type_ids[sample_type_name],
                    _has_do_not_use_token(orig_name),
                    row.get(COL_MATRIX_TUBE_ID) or None,
                ),
            )
            assert cur.lastrowid is not None
            input_sample_idx = cur.lastrowid
            input_sample_cache[cache_key] = input_sample_idx
            if is_control:
                _populate_katharoseq_sample(cur, input_sample_idx, row)

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
            well_desc = row.get(load_config.well_description_column) or None
            # Populated only when it differs from the input sample's name;
            # an equal value would restate what input_sample already holds.
            prepped_sample_name = (
                sample_name if has_replicates and sample_name != orig_name else None
            )
            # Prep-level override is two-state: True when a replicate's
            # Sample_Name carries the token, NULL otherwise (inherit input).
            prepped_do_not_use = (
                True
                if prepped_sample_name is not None
                and _has_do_not_use_token(prepped_sample_name)
                else None
            )
            cur.execute(
                """INSERT INTO prepped_sample
                   (compression_sample_idx, prepped_well,
                    sample_name, do_not_use, well_description)
                   VALUES (?, ?, ?, ?, ?)""",
                (cs_idx, dest_well, prepped_sample_name, prepped_do_not_use, well_desc),
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
            _populate_amplicon_sample(cur, prs_idx, row)
            _populate_extra_columns(cur, prs_idx, row, extra_cols)

        # Per-loading platform-specific row: one per CSV row, always.
        # A format declaring no sample_kind has no such table to write.
        populate_sample = _SAMPLE_POPULATORS.get(load_config.sample_kind)
        if populate_sample is not None:
            populate_sample(cur, prs_idx, row)

    conn.commit()


# ---------------------------------------------------------------------------
# Platform-specific helpers
# ---------------------------------------------------------------------------


def _populate_illumina_run_from_sections(
    cur, run_idx: int, sections: dict, bio_rows: list
):
    """Insert a single illumina_run row from parsed omnibus sections.

    Combines data from the Reads, Settings, and Bioinformatics sections to
    populate read lengths, reverse-complement flag, adapter sequences, and
    other Illumina-specific run configuration. Callers must have validated
    the sections before reaching this function.

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

    # A source document recording no run configuration has no [Reads] section;
    # NULL read lengths say "not stated", which 0 could not express.
    read1 = int(reads[0]) if len(reads) > 0 else None
    read2 = int(reads[1]) if len(reads) > 1 else None

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


def _barcodes_are_rc_for_primer(primer: str) -> bool:
    """Whether an amplicon run's Golay barcodes are stored reverse-complemented.

    The prep template does not state barcode orientation; it is a property of the
    assay. An EMP 515f run uses the reverse-complemented 515rcbc Golay set, so its
    barcodes are stored RC. Only that assay is recognised today, so an
    unrecognised primer raises rather than guessing an orientation that would
    silently mis-demux. Extend the mapping when another amplicon assay is added.
    """
    if primer == EMP_515F_PRIMER:
        return True
    raise ValueError(
        f"cannot determine barcode orientation for amplicon primer {primer!r}: "
        f"only the EMP 515f primer ({EMP_515F_PRIMER}) is recognised"
    )


def _populate_amplicon_run(cur, run_idx: int, row: dict):  # same-pattern-ok: D18 extended to run-level populators (R6)
    """Insert an amplicon_run row if the amplicon prep columns are present.

    Every column is constant across a run -- the wet lab does not mix primers
    within one -- so the first Data row supplies them all. barcodes_are_rc is not
    a sheet column; it is inferred from the primer (fail-loud) and stored, so the
    orientation is a queryable fact rather than re-derived by every reader.

    Args:
        cur: An open SQLite cursor.
        run_idx: The processing_run.run_idx to associate the row with.
        row: The run's first Data-section row dict.
    """
    if COL_AMPLICON_PRIMER not in row:
        return

    primer = row[COL_AMPLICON_PRIMER]
    cur.execute(
        "INSERT INTO amplicon_run "
        "(run_idx, primer, linker, target_gene, target_subfragment, "
        " pcr_primers, sequencing_meth, barcodes_are_rc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_idx,
            primer,
            row[COL_AMPLICON_LINKER],
            row[COL_AMPLICON_TARGET_GENE],
            row[COL_AMPLICON_TARGET_SUBFRAGMENT],
            row[COL_AMPLICON_PCR_PRIMERS],
            row[COL_AMPLICON_SEQUENCING_METH],
            _barcodes_are_rc_for_primer(primer),
        ),
    )


def _populate_amplicon_sample(cur, prs_idx: int, row: dict):  # same-pattern-ok: sixth sibling of the family D18 keeps parallel
    """Insert an amplicon_sample row if the in-line Golay barcode is present.

    Args:
        cur: An open SQLite cursor.
        prs_idx: The prepped_sample_idx for this sample.
        row: A single Data-section row dict.
    """
    if COL_AMPLICON_BARCODE not in row:
        return

    cur.execute(
        "INSERT INTO amplicon_sample (prepped_sample_idx, barcode) VALUES (?, ?)",
        (prs_idx, row[COL_AMPLICON_BARCODE]),
    )


def _populate_katharoseq_sample(cur, input_sample_idx: int, row: dict):  # same-pattern-ok: D18 extended (R6)
    """Insert a katharoseq_sample row if the sheet supplies either metric.

    Membership already lives in input_sample.sample_type; a row with both
    metrics NULL would assert nothing, and would wrongly signal that the run
    carries KatharoSeq metric columns.

    Args:
        cur: An open SQLite cursor.
        input_sample_idx: The input_sample this control belongs to.
        row: A single Data-section row dict.
    """
    rack_id = row.get(COL_KATHAROSEQ_RACK_ID) or None
    number_of_cells = _opt_float(row, COL_KATHAROSEQ_NUMBER_OF_CELLS)
    if rack_id is None and number_of_cells is None:
        return

    cur.execute(
        "INSERT INTO katharoseq_sample "
        "(input_sample_idx, rack_id, number_of_cells) VALUES (?, ?, ?)",
        (input_sample_idx, rack_id, number_of_cells),
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


# Map legacy_samplesheet_format.sample_kind -> the callable that writes that
# kind's per-sample row. Defined after its members, as _CHECK_FUNCTIONS is.
_SAMPLE_POPULATORS = {
    "illumina": _populate_illumina_sample,
    "pacbio": _populate_pacbio_sample,
    "tellseq": _populate_tellseq_sample,
}


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
