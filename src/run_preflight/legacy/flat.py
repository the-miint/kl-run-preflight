"""Flat tab-delimited amplicon prep-template round-trip.

EMP amplicon prep templates are flat TAB-delimited sheets (header + one row per
sample), and their column set/order varies between studies. This path is
header-driven: it accepts whatever columns a sheet has, types the ones it
recognises (by name) into their schema homes, keeps the rest verbatim in the
existing ``legacy_extra_column``, and reconstructs in the sheet's own column
order (persisted on ``processing_run.flat_column_order``). Nothing is stored
twice; round-trip is byte-exact.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..constants import (
    ASSAY_AMPLICON,
    DB_TYPE_EXTRACTION_BLANK,
    DB_TYPE_KATHAROSEQ_POSITIVE,
    PLATFORM_ILLUMINA,
    SAMPLE_TYPE_STANDARD,
    SHEET_TYPE_AMPLICON,
    SHEET_VERSION_AMPLICON,
)

# Recognised sheet column -> its typed home, as a reconstruct SELECT expression
# over the join in reconstruct_flat. These are the only columns typed; every
# other column is kept verbatim. Homes are per-sample, or per-plate/run keys that
# the sample references, so a typed value always round-trips to its own row.
_TYPED_SELECT: tuple[tuple[str, str], ...] = (
    ("sample_name", "i.sample_name"),
    ("barcode", "a.barcode"),
    ("well_id_96", "i.well"),
    ("well_id_384", "c.compression_well"),
    ("well_description", "p.well_description"),
    ("TubeCode", "i.matrix_tube_id"),
    ("sample_plate", "pl.plate_name"),
    ("vol_extracted_elution_ul", "pl.elution_vol"),
    ("project_name", "pr.project_name"),
)
RECOGNIZED_COLUMNS: frozenset[str] = frozenset(c for c, _ in _TYPED_SELECT)

_BLANK_PREFIX = "BLANK."
_KATHARO_PREFIX = "KATHARO."


def looks_like_flat_amplicon(path: str) -> bool:
    """True if the file's first line has a tab, starts with ``sample_name``, and
    is not a ``[section]`` omnibus sheet."""
    try:
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
    except (UnicodeDecodeError, OSError):
        return False
    return "\t" in first and first.startswith("sample_name") and not first.lstrip().startswith("[")


def parse_flat_tsv(path: str) -> list[list[str]]:
    """Parse the sheet into rows (row 0 = header). No quoting and no embedded
    tabs/newlines, so a manual split is exact. Rejects a non-``sample_name`` first
    column or a ragged row."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    rows = [line.split("\t") for line in lines]
    if not rows or not rows[0] or rows[0][0] != "sample_name":
        raise ValueError("not a flat amplicon prep template (header must start with sample_name)")
    width = len(rows[0])
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            raise ValueError(f"prep template line {i} has {len(row)} columns, expected {width}")
    return rows


def _sample_type_for(sample_name: str) -> str:
    if sample_name.startswith(_BLANK_PREFIX):
        return DB_TYPE_EXTRACTION_BLANK
    if sample_name.startswith(_KATHARO_PREFIX):
        return DB_TYPE_KATHAROSEQ_POSITIVE
    return SAMPLE_TYPE_STANDARD


def _to_real(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def _fmt_real(value) -> str:
    """REAL back to its sheet string: whole numbers drop the '.0', NULL is ''."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def populate_amplicon(conn: sqlite3.Connection, rows: list[list[str]]) -> None:
    """Populate the DB from parsed rows. Types recognised columns into their
    homes and keeps the rest verbatim in legacy_extra_column. Does not commit."""
    header, data = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}

    def v(row: list[str], name: str) -> str:
        i = col.get(name)
        return row[i] if i is not None else ""

    cur = conn.cursor()
    assay_idx = cur.execute(
        "SELECT assay_type_idx FROM assay_type WHERE name = ?", (ASSAY_AMPLICON,)
    ).fetchone()[0]
    platform_idx = cur.execute(
        "SELECT platform_idx FROM sequencing_platform WHERE name = ?", (PLATFORM_ILLUMINA,)
    ).fetchone()[0]
    fmt_idx = cur.execute(
        "SELECT legacy_format_idx FROM legacy_samplesheet_format "
        "WHERE legacy_sheet_type = ? AND legacy_version = ?",
        (SHEET_TYPE_AMPLICON, SHEET_VERSION_AMPLICON),
    ).fetchone()[0]

    first = data[0]
    # One run. run_date/instrument are kept verbatim; the NOT NULL columns take
    # first-row projections. flat_column_order persists the header order so the
    # sheet reconstructs in its own column order.
    cur.execute(
        "INSERT INTO processing_run (experiment_name, run_date, instrument_type, "
        " assay_type_idx, platform_idx, legacy_format_idx, flat_column_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (v(first, "project_name"), v(first, "run_date"), v(first, "instrument_model"),
         assay_idx, platform_idx, fmt_idx, "\t".join(header)),
    )
    run_idx = cur.lastrowid

    verbatim_cols = [name for name in header if name not in RECOGNIZED_COLUMNS]
    project_by_name: dict[str, int] = {}
    plate_by_key: dict[tuple[int, str], int] = {}

    for row in data:
        project_name = v(row, "project_name")
        if project_name not in project_by_name:
            cur.execute(
                "INSERT INTO project (project_name, external_project_id, "
                " library_construction_protocol, experiment_design_description) "
                "VALUES (?, ?, ?, ?)",
                (project_name, project_name, v(row, "library_construction_protocol"),
                 v(row, "experiment_design_description")),
            )
            project_by_name[project_name] = cur.lastrowid
        project_idx = project_by_name[project_name]

        plate_key = (project_idx, v(row, "sample_plate"))
        if plate_key not in plate_by_key:
            cur.execute(
                "INSERT INTO input_plate (plate_name, primary_project_idx, elution_vol) "
                "VALUES (?, ?, ?)",
                (v(row, "sample_plate"), project_idx, _to_real(v(row, "vol_extracted_elution_ul"))),
            )
            plate_by_key[plate_key] = cur.lastrowid
        plate_idx = plate_by_key[plate_key]

        sample_name = v(row, "sample_name")
        sample_type = _sample_type_for(sample_name)
        st_idx = cur.execute(
            "SELECT sample_type_idx FROM sample_type WHERE name = ?", (sample_type,)
        ).fetchone()[0]
        sample_project = project_idx if sample_type == SAMPLE_TYPE_STANDARD else None
        cur.execute(
            "INSERT INTO input_sample (sample_name, input_plate_idx, well, "
            " project_idx, sample_type_idx, matrix_tube_id) VALUES (?, ?, ?, ?, ?, ?)",
            (sample_name, plate_idx, v(row, "well_id_96"), sample_project, st_idx,
             v(row, "TubeCode")),
        )
        input_sample_idx = cur.lastrowid
        cur.execute(
            "INSERT INTO compression_sample (run_idx, input_sample_idx, compression_well) "
            "VALUES (?, ?, ?)",
            (run_idx, input_sample_idx, v(row, "well_id_384")),
        )
        compression_sample_idx = cur.lastrowid
        cur.execute(
            "INSERT INTO prepped_sample (compression_sample_idx, prepped_well, "
            " sample_name, well_description) VALUES (?, ?, ?, ?)",
            (compression_sample_idx, v(row, "well_id_384"), sample_name,
             v(row, "well_description")),
        )
        prepped_sample_idx = cur.lastrowid
        cur.execute(
            "INSERT INTO amplicon_sample (prepped_sample_idx, barcode) VALUES (?, ?)",
            (prepped_sample_idx, v(row, "barcode")),
        )
        cur.executemany(
            "INSERT INTO legacy_extra_column (prepped_sample_idx, column_name, "
            " column_value) VALUES (?, ?, ?)",
            [(prepped_sample_idx, name, v(row, name)) for name in verbatim_cols],
        )


def reconstruct_flat(conn: sqlite3.Connection) -> str:
    """Rebuild the sheet from its typed homes + the verbatim store, in the
    persisted header order, tab-delimited, no trailing newline."""
    cur = conn.cursor()
    header = cur.execute(
        "SELECT flat_column_order FROM processing_run WHERE flat_column_order IS NOT NULL"
    ).fetchone()[0].split("\t")

    typed_exprs = ", ".join(expr for _, expr in _TYPED_SELECT)
    typed_names = [name for name, _ in _TYPED_SELECT]
    typed_rows = cur.execute(
        f"SELECT p.prepped_sample_idx, {typed_exprs} "
        "FROM prepped_sample p "
        "JOIN amplicon_sample a ON a.prepped_sample_idx = p.prepped_sample_idx "
        "JOIN compression_sample c ON p.compression_sample_idx = c.compression_sample_idx "
        "JOIN input_sample i ON c.input_sample_idx = i.input_sample_idx "
        "JOIN input_plate pl ON i.input_plate_idx = pl.input_plate_idx "
        "JOIN project pr ON pl.primary_project_idx = pr.project_idx "
        "ORDER BY p.prepped_sample_idx"
    ).fetchall()

    lines = ["\t".join(header)]
    for typed_row in typed_rows:
        values = dict(zip(typed_names, typed_row[1:]))
        values["vol_extracted_elution_ul"] = _fmt_real(values["vol_extracted_elution_ul"])
        values.update(
            cur.execute(
                "SELECT column_name, column_value FROM legacy_extra_column "
                "WHERE prepped_sample_idx = ?",
                (typed_row[0],),
            ).fetchall()
        )
        lines.append("\t".join(values[name] for name in header))
    # EMP prep templates end with a trailing newline.
    return "\n".join(lines) + "\n"


def run_is_flat_amplicon(conn: sqlite3.Connection) -> bool:
    """True if the DB's single run is a flat amplicon run."""
    row = conn.execute(
        "SELECT f.legacy_sheet_type FROM processing_run r "
        "JOIN legacy_samplesheet_format f ON r.legacy_format_idx = f.legacy_format_idx"
    ).fetchone()
    return row is not None and row[0] == SHEET_TYPE_AMPLICON


def load_flat_amplicon(path: str) -> sqlite3.Connection:
    """Parse a flat amplicon prep template into a fresh in-memory DB (committed)."""
    from ..db import create_db  # local import avoids a db<->legacy cycle

    conn = create_db(":memory:")
    populate_amplicon(conn, parse_flat_tsv(path))
    conn.commit()
    return conn


def save_flat_amplicon(conn: sqlite3.Connection, path: str) -> None:
    """Write the reconstructed prep template (UTF-8, no trailing newline)."""
    Path(path).write_text(reconstruct_flat(conn), encoding="utf-8")
