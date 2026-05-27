"""Generate the interactive schema diagram HTML from schema.sql.

Introspects the SQLite schema to produce an HTML file with an interactive
SVG diagram showing all tables, columns, foreign keys, and views.
"""

from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from pathlib import Path

# ============================================================
# Category definitions — update this dict when adding new
# tables or categories to schema.sql.  Each category defines
# its tables, diagram x-position, color, and legend label.
# Any table not listed here appears as "uncategorized".
# ============================================================

CATEGORIES: dict[str, dict] = {
    "ref": {
        "label": "Reference",
        "tables": ["assay_type", "sequencing_platform", "sample_type"],
        "x": 40,
        "color": "#3fb950",
    },
    "core": {
        "label": "Core",
        "tables": [
            "project",
            "input_plate",
            "input_sample",
            "processing_run",
            "compression_sample",
            "prepped_sample",
        ],
        "x": 380,
        "color": "#d2a8ff",
    },
    "platform": {
        "label": "Platform",
        "tables": [
            "illumina_run",
            "illumina_sample",
            "tellseq_sample",
            "pacbio_sample",
        ],
        "x": 720,
        "color": "#f778ba",
    },
    "workflow": {
        "label": "Workflow",
        "tables": [
            "metagenomic_absquant_sample",
            "metatranscriptomic_sample",
            "katharoseq_sample",
        ],
        "x": 1020,
        "color": "#79c0ff",
    },
    "legacy": {
        "label": "Legacy Registry",
        "tables": [
            "legacy_section_format",
            "legacy_samplesheet_format",
            "legacy_samplesheet_view",
            "legacy_samplesheet_optional_columns",
            "legacy_extra_column",
        ],
        "x": 40,
        "color": "#ffa657",
    },
    "uncategorized": {
        "label": "Uncategorized",
        "tables": [],
        "x": 380,
        "color": "#8b949e",
    },
}

# ============================================================
# Paths
# ============================================================

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "run_preflight"
    / "sql"
    / "schema.sql"
)
_OUTPUT_PATH = _SCHEMA_PATH.parent / "schema_diagram.html"

# ============================================================
# Layout constants
# ============================================================

_ROW_H = 18
_HEADER_H = 26
_PAD = 8
_TABLE_GAP = 16
_SECTION_MARGIN = 60
_VIEW_GRID_COLS = 5
_VIEW_LEFT_MARGIN = 40
_VIEW_COL_SPACING = 280
_VIEW_ROW_SPACING = 36

# ============================================================
# Derived lookup
# ============================================================


def _build_table_to_category(
    categories: dict[str, dict],
) -> dict[str, str]:
    """Invert CATEGORIES to {table_name: category}."""
    result: dict[str, str] = {}
    for category, info in categories.items():
        for table in info["tables"]:
            result[table] = category
    return result


# ============================================================
# SQL comment extraction
# ============================================================


def _extract_column_comments(
    schema_sql: str,
) -> dict[str, dict[str, str]]:
    """Parse inline -- comments from CREATE TABLE statements.

    Returns {table_name: {column_name: comment_text}}.
    """
    result: dict[str, dict[str, str]] = {}

    # Match each CREATE TABLE name(...); block, capturing the table
    # name and the multi-line body between the parens.  re.DOTALL
    # lets .*? span newlines so the body capture crosses lines.
    for match in re.finditer(r"CREATE TABLE (\w+)\s*\((.*?)\);", schema_sql, re.DOTALL):
        table_name = match.group(1)
        body = match.group(2)
        comments: dict[str, str] = {}
        current_col: str | None = None

        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # Standalone comment line — attach to current column
            if stripped.startswith("--"):
                if current_col is not None:
                    # strip off the leading -- and any extra whitespace
                    comment_text = stripped.lstrip("- ").strip()
                    if current_col in comments:
                        comments[current_col] += " " + comment_text
                    else:
                        comments[current_col] = comment_text
                continue

            # Table-level constraints - skip and reset current col
            upper = stripped.upper()
            if upper.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK")):
                current_col = None
                continue

            # Column definition line - capture the column name and optional trailing comment
            col_match = re.match(r"(\w+)\s+\w+", stripped)
            if col_match:
                col_name = col_match.group(1)
                current_col = col_name

                # Check for trailing comment on the same line
                trailing = re.search(r"--\s*(.+)$", stripped)
                if trailing:
                    comments[col_name] = trailing.group(1).strip()

        if comments:
            result[table_name] = comments

    return result


def _extract_view_comments(schema_sql: str) -> dict[str, str]:
    """Extract the -- comment line immediately preceding each CREATE VIEW.

    Returns {view_name: comment_text}.
    """
    result: dict[str, str] = {}
    lines = schema_sql.split("\n")

    for i, line in enumerate(lines):
        view_match = re.match(r"CREATE VIEW (\w+)", line.strip())
        if not view_match:
            continue

        view_name = view_match.group(1)

        # Collect comment lines immediately above the CREATE VIEW,
        # stopping at the first blank line or non-comment line.
        comment_parts: list[str] = []
        j = i - 1
        while j >= 0:
            prev = lines[j].strip()
            if prev.startswith("--"):
                text = prev.lstrip("- ").strip()
                comment_parts.append(text)
                j -= 1
            else:
                break

        # join comments into string
        if comment_parts:
            # Reverse because collected bottom-up
            comment_parts.reverse()
            result[view_name] = " ".join(comment_parts)

    return result


# ============================================================
# SQLite introspection
# ============================================================


def _create_db(schema_sql: str) -> sqlite3.Connection:
    """Create an in-memory SQLite DB from schema DDL."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema_sql)
    return conn


def _introspect_tables(
    conn: sqlite3.Connection,
    column_comments: dict[str, dict[str, str]],
    table_to_category: dict[str, str],
) -> list[dict]:
    """Build table metadata list from SQLite introspection.

    Tables are returned in CATEGORY_TABLES order, with any
    uncategorized tables appended alphabetically at the end.
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    all_names = {row[0] for row in cursor.fetchall()}

    # Build ordered list: CATEGORIES order first, then
    # any uncategorized tables sorted alphabetically
    ordered_names: list[str] = []
    for info in CATEGORIES.values():
        for name in info["tables"]:
            if name in all_names:
                ordered_names.append(name)
    uncategorized = sorted(all_names - set(ordered_names))
    ordered_names.extend(uncategorized)
    table_names = ordered_names

    tables = []
    for tname in table_names:
        # Get FK info: build {from_column: referenced_table}
        fk_rows = conn.execute(f"PRAGMA foreign_key_list({tname})").fetchall()
        fk_map: dict[str, str] = {}
        for fk_row in fk_rows:
            fk_map[fk_row[3]] = fk_row[2]

        # Get column info: build list of {name, type, pk, fk, note} dicts for each column
        col_rows = conn.execute(f"PRAGMA table_info({tname})").fetchall()

        columns = []
        tbl_comments = column_comments.get(tname, {})
        for col_row in col_rows:
            col_name = col_row[1]
            col_type = col_row[2] or "TEXT"
            is_pk = col_row[5] > 0

            col_entry: dict = {"name": col_name, "type": col_type}
            if is_pk:
                col_entry["pk"] = True
            if col_name in fk_map:
                col_entry["fk"] = fk_map[col_name]
            if col_name in tbl_comments:
                col_entry["note"] = tbl_comments[col_name]

            columns.append(col_entry)

        # Add table, category, and columns to result list
        category = table_to_category.get(tname, "uncategorized")
        tables.append({"id": tname, "category": category, "columns": columns})

    return tables


def _introspect_views(
    conn: sqlite3.Connection,
    view_comments: dict[str, str],
) -> list[dict]:
    """Build view metadata list from SQLite introspection."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    )
    views = []
    for (name,) in cursor.fetchall():
        note = view_comments.get(name, "")
        views.append({"id": name, "note": note})

    return views


# ============================================================
# Layout computation
# ============================================================


def _table_height(table: dict) -> int:
    """Pixel height for a table box."""
    return _HEADER_H + len(table["columns"]) * _ROW_H + _PAD


def _compute_positions(tables: list[dict]) -> dict[str, dict[str, int]]:
    """Compute x,y positions for each table, grouped by category."""
    # Group tables by category, preserving introspection order
    by_category: dict[str, list[dict]] = {}
    for t in tables:
        by_category.setdefault(t["category"], []).append(t)

    # key is table name, value is dict with 'x' and 'y' pixel positions
    positions: dict[str, dict[str, int]] = {}

    # Track y-offset per x column to stack tables vertically
    col_y: dict[int, int] = {}

    # each category of tables goes in a specific x column, and
    # tables within a category stack vertically in the order they were introspected
    for cat, info in CATEGORIES.items():
        x = info["x"]
        if cat not in by_category:
            continue

        # Initialize y for this x column if not already set
        if x not in col_y:
            col_y[x] = _SECTION_MARGIN

        for t in by_category[cat]:
            positions[t["id"]] = {"x": x, "y": col_y[x]}
            col_y[x] += _table_height(t) + _TABLE_GAP

    return positions


def _compute_view_positions(
    views: list[dict], tables_max_y: int
) -> dict[str, dict[str, int]]:
    """Compute x,y positions for views in a grid below the tables."""
    # key is view name, value is dict with 'x' and 'y' pixel positions
    positions: dict[str, dict[str, int]] = {}
    start_y = tables_max_y + _SECTION_MARGIN

    for i, v in enumerate(views):
        col = (
            i % _VIEW_GRID_COLS
        )  # mod (remainder after int division) gives column index
        row = i // _VIEW_GRID_COLS  # int division gives row index
        x = _VIEW_LEFT_MARGIN + col * _VIEW_COL_SPACING
        y = start_y + row * _VIEW_ROW_SPACING
        positions[v["id"]] = {"x": x, "y": y}

    return positions


# ============================================================
# JS generation
# ============================================================


def _to_js_object(obj: object, indent: int = 2) -> str:
    """Serialize a Python object to a JS-compatible JSON string."""
    return json.dumps(obj, indent=indent)


def _build_category_colors_js(used_categories: set[str]) -> str:
    """Build the CATEGORY_COLORS JS object from CATEGORIES."""
    # Build manually to avoid quoting the var() references
    lines = []
    for cat in CATEGORIES:
        if cat in used_categories:
            lines.append(f"  {cat}: 'var(--{cat}-table)'")
    return "{\n" + ",\n".join(lines) + "\n}"


def _build_positions_js(positions: dict[str, dict[str, int]]) -> str:
    """Build a JS object mapping names to {x, y} positions."""
    lines = []
    for name, pos in positions.items():
        lines.append(f"  '{name}': {{ x: {pos['x']}, y: {pos['y']} }}")
    return "{\n" + ",\n".join(lines) + "\n}"


# ============================================================
# HTML template
# ============================================================


def _build_html(
    schema_js: str,
    category_colors_js: str,
    positions_js: str,
    view_positions_js: str,
    used_categories: set[str],
) -> str:
    """Assemble the full HTML file from template and generated JS."""

    # Generate CSS variables for each used category
    category_css_lines = []
    for cat, info in CATEGORIES.items():
        if cat in used_categories:
            category_css_lines.append(f"    --{cat}-table: {info['color']};")
    category_css = "\n".join(category_css_lines)

    # Generate legend items for each used category
    legend_items = []
    for cat, info in CATEGORIES.items():
        if cat in used_categories:
            legend_items.append(
                f'  <div class="legend-item">'
                f'<div class="legend-dot" '
                f'style="background:var(--{cat}-table)"></div>'
                f" {info['label']}</div>"
            )
    category_legend = "\n".join(legend_items)

    return textwrap.dedent("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sequencing Sample Sheet Schema v3</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=DM+Sans:wght@400;500;700&display=swap');

  :root {{
    --bg: #0e1117;
    --surface: #161b22;
    --surface-hover: #1c2129;
    --border: #30363d;
    --border-accent: #58a6ff;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --text-dim: #484f58;
    --pk: #f0883e;
    --fk: #58a6ff;
{category_css}
    --view-color: #56d364;
    --link-line: #30363d;
    --link-line-hover: #58a6ff;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    overflow: hidden;
    height: 100vh;
  }}

  #toolbar {{
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 100;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 10px 20px;
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 13px;
  }}

  #toolbar h1 {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
  }}

  #toolbar .sep {{
    width: 1px;
    height: 20px;
    background: var(--border);
  }}

  .legend-item {{
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: var(--text-muted);
  }}
  .legend-dot {{
    width: 8px;
    height: 8px;
    border-radius: 2px;
  }}

  #toolbar button {{
    background: var(--surface-hover);
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 11px;
  }}
  #toolbar button:hover {{ border-color: var(--border-accent); color: var(--text); }}

  svg {{
    width: 100%;
    height: 100%;
    cursor: grab;
  }}
  svg:active {{ cursor: grabbing; }}

  .table-group {{ cursor: move; }}
  .table-group:hover .table-bg {{ stroke: var(--border-accent); }}

  .table-bg {{
    fill: var(--surface);
    stroke: var(--border);
    stroke-width: 1;
    rx: 6;
  }}

  .table-header-bg {{ rx: 6; }}
  .table-header-text {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    font-weight: 600;
    fill: var(--bg);
  }}

  .column-text {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10.5px;
    fill: var(--text-muted);
  }}
  .column-text.pk {{ fill: var(--pk); }}
  .column-text.fk {{ fill: var(--fk); }}

  .type-text {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9.5px;
    fill: var(--text-dim);
  }}

  .badge {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 8px;
    font-weight: 600;
  }}

  .link-line {{
    fill: none;
    stroke: var(--link-line);
    stroke-width: 1.2;
    stroke-dasharray: 4,3;
    pointer-events: none;
  }}

  .view-group {{ cursor: move; }}
  .view-group:hover .view-bg {{ stroke: var(--view-color); }}

  .view-bg {{
    fill: none;
    stroke: var(--border);
    stroke-width: 1;
    stroke-dasharray: 3,2;
    rx: 6;
  }}

  .view-header-bg {{
    fill: var(--view-color);
    opacity: 0.15;
    rx: 6;
  }}
  .view-header-text {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    fill: var(--view-color);
  }}

  #tooltip {{
    position: fixed;
    display: none;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 11px;
    color: var(--text-muted);
    font-family: 'IBM Plex Mono', monospace;
    z-index: 200;
    pointer-events: none;
    max-width: 320px;
    line-height: 1.5;
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
  }}
</style>
</head>
<body>

<div id="toolbar">
  <h1>Schema v3</h1>
  <div class="sep"></div>
{category_legend}
  <div class="legend-item"><div class="legend-dot" style="background:var(--view-color)"></div> Views</div>
  <div class="sep"></div>
  <div class="legend-item"><span style="color:var(--pk)">&#9632;</span> PK</div>
  <div class="legend-item"><span style="color:var(--fk)">&#9632;</span> FK</div>
  <div class="sep"></div>
  <button onclick="resetView()">Reset View</button>
  <button onclick="autoLayout()">Auto Layout</button>
</div>

<svg id="canvas"></svg>
<div id="tooltip"></div>

<script>
var SCHEMA = {schema_js};

var CATEGORY_COLORS = {category_colors_js};

var COL_W = 260, ROW_H = {row_h}, HEADER_H = {header_h}, PAD = {pad};

function tableHeight(t) {{ return HEADER_H + t.columns.length * ROW_H + PAD; }}

var POSITIONS = {positions_js};

var VIEW_POSITIONS = {view_positions_js};

var svg = document.getElementById('canvas');
var tooltip = document.getElementById('tooltip');
var transform = {{ x: 20, y: 20, k: 1 }};

function applyTransform() {{
  var g = document.getElementById('main-group');
  g.setAttribute('transform', 'translate(' + transform.x + ',' + transform.y + ') scale(' + transform.k + ')');
}}

function resetView() {{
  transform = {{ x: 20, y: 50, k: 0.85 }};
  applyTransform();
}}

function svgEl(tag, attrs) {{
  var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (var k in attrs) {{
    if (attrs.hasOwnProperty(k)) el.setAttribute(k, attrs[k]);
  }}
  return el;
}}

function clearElement(el) {{
  while (el.firstChild) {{
    el.removeChild(el.firstChild);
  }}
}}

function render() {{
  clearElement(svg);
  var mainG = svgEl('g', {{ id: 'main-group' }});
  svg.appendChild(mainG);

  var linkG = svgEl('g', {{ id: 'links' }});
  mainG.appendChild(linkG);

  var fkLinks = [];
  for (var ti = 0; ti < SCHEMA.tables.length; ti++) {{
    var t = SCHEMA.tables[ti];
    var pos = POSITIONS[t.id];
    for (var ci = 0; ci < t.columns.length; ci++) {{
      var col = t.columns[ci];
      if (col.fk) {{
        var targetPos = POSITIONS[col.fk];
        if (!targetPos) continue;
        var fromX = pos.x;
        var fromY = pos.y + HEADER_H + ci * ROW_H + ROW_H / 2;
        var toX = targetPos.x + COL_W;
        var toY = targetPos.y + HEADER_H / 2;
        fkLinks.push({{ from: {{ x: fromX, y: fromY }}, to: {{ x: toX, y: toY }} }});
      }}
    }}
  }}

  for (var li = 0; li < fkLinks.length; li++) {{
    var link = fkLinks[li];
    var dx = link.to.x - link.from.x;
    var dy = link.to.y - link.from.y;
    var cx1 = link.from.x + dx * 0.3;
    var cx2 = link.to.x - dx * 0.3;
    var path = svgEl('path', {{
      class: 'link-line',
      d: 'M' + link.from.x + ',' + link.from.y + ' C' + cx1 + ',' + link.from.y + ' ' + cx2 + ',' + link.to.y + ' ' + link.to.x + ',' + link.to.y
    }});
    linkG.appendChild(path);
  }}

  for (var ti2 = 0; ti2 < SCHEMA.tables.length; ti2++) {{
    (function(t) {{
      var pos = POSITIONS[t.id];
      var h = tableHeight(t);
      var color = CATEGORY_COLORS[t.category];
      var g = svgEl('g', {{ class: 'table-group', 'data-id': t.id }});
      g.setAttribute('transform', 'translate(' + pos.x + ',' + pos.y + ')');

      g.appendChild(svgEl('rect', {{ class: 'table-bg', width: COL_W, height: h }}));

      g.appendChild(svgEl('rect', {{
        class: 'table-header-bg', width: COL_W, height: HEADER_H,
        fill: color, opacity: 0.9
      }}));
      var headerText = svgEl('text', {{
        class: 'table-header-text', x: 10, y: HEADER_H - 8
      }});
      headerText.textContent = t.id;
      g.appendChild(headerText);

      t.columns.forEach(function(col, i) {{
        var y = HEADER_H + i * ROW_H + ROW_H - 4;
        var cls = col.pk ? 'column-text pk' : col.fk ? 'column-text fk' : 'column-text';

        var nameText = svgEl('text', {{ class: cls, x: 10, y: y }});
        nameText.textContent = (col.pk ? '\\u26BF ' : '') + col.name;
        g.appendChild(nameText);

        var typeText = svgEl('text', {{ class: 'type-text', x: COL_W - 10, y: y, 'text-anchor': 'end' }});
        typeText.textContent = col.type;
        g.appendChild(typeText);

        if (col.fk) {{
          var badge = svgEl('text', {{
            class: 'badge', x: COL_W - 10, y: y - 10, 'text-anchor': 'end',
            fill: 'var(--fk)', opacity: 0.5
          }});
          badge.textContent = '\\u2192 ' + col.fk;
          g.appendChild(badge);
        }}

        if (col.note) {{
          var hoverRect = svgEl('rect', {{
            x: 0, y: HEADER_H + i * ROW_H, width: COL_W, height: ROW_H,
            fill: 'transparent', cursor: 'help'
          }});
          hoverRect.addEventListener('mouseenter', function(e) {{
            tooltip.style.display = 'block';
            tooltip.textContent = col.note;
            tooltip.style.left = (e.clientX + 12) + 'px';
            tooltip.style.top = (e.clientY - 8) + 'px';
          }});
          hoverRect.addEventListener('mouseleave', function() {{ tooltip.style.display = 'none'; }});
          hoverRect.addEventListener('mousemove', function(e) {{
            tooltip.style.left = (e.clientX + 12) + 'px';
            tooltip.style.top = (e.clientY - 8) + 'px';
          }});
          g.appendChild(hoverRect);
        }}
      }});

      var dragStart = null;
      g.addEventListener('mousedown', function(e) {{
        e.stopPropagation();
        dragStart = {{ mx: e.clientX, my: e.clientY, ox: pos.x, oy: pos.y }};
      }});
      window.addEventListener('mousemove', function(e) {{
        if (!dragStart) return;
        pos.x = dragStart.ox + (e.clientX - dragStart.mx) / transform.k;
        pos.y = dragStart.oy + (e.clientY - dragStart.my) / transform.k;
        render();
      }});
      window.addEventListener('mouseup', function() {{ dragStart = null; }});

      mainG.appendChild(g);
    }})(SCHEMA.tables[ti2]);
  }}

  for (var vi = 0; vi < SCHEMA.views.length; vi++) {{
    (function(v) {{
      var pos = VIEW_POSITIONS[v.id];
      var g = svgEl('g', {{ class: 'view-group' }});
      g.setAttribute('transform', 'translate(' + pos.x + ',' + pos.y + ')');

      var w = Math.max(v.id.length * 7.2 + 20, 160);
      g.appendChild(svgEl('rect', {{ class: 'view-bg', width: w, height: 28 }}));
      g.appendChild(svgEl('rect', {{ class: 'view-header-bg', width: w, height: 28 }}));
      var vText = svgEl('text', {{ class: 'view-header-text', x: 8, y: 18 }});
      vText.textContent = v.id;
      g.appendChild(vText);

      var hoverRect = svgEl('rect', {{ x: 0, y: 0, width: w, height: 28, fill: 'transparent', cursor: 'help' }});
      hoverRect.addEventListener('mouseenter', function(e) {{
        tooltip.style.display = 'block';
        tooltip.textContent = v.note;
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY - 8) + 'px';
      }});
      hoverRect.addEventListener('mouseleave', function() {{ tooltip.style.display = 'none'; }});
      hoverRect.addEventListener('mousemove', function(e) {{
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY - 8) + 'px';
      }});
      g.appendChild(hoverRect);

      var vDrag = null;
      g.addEventListener('mousedown', function(e) {{
        e.stopPropagation();
        vDrag = {{ mx: e.clientX, my: e.clientY, ox: pos.x, oy: pos.y }};
      }});
      window.addEventListener('mousemove', function(e) {{
        if (!vDrag) return;
        pos.x = vDrag.ox + (e.clientX - vDrag.mx) / transform.k;
        pos.y = vDrag.oy + (e.clientY - vDrag.my) / transform.k;
        render();
      }});
      window.addEventListener('mouseup', function() {{ vDrag = null; }});

      mainG.appendChild(g);
    }})(SCHEMA.views[vi]);
  }}

  applyTransform();
}}

var panStart = null;
svg.addEventListener('mousedown', function(e) {{
  if (e.target === svg || e.target.id === 'main-group') {{
    panStart = {{ mx: e.clientX, my: e.clientY, ox: transform.x, oy: transform.y }};
  }}
}});
window.addEventListener('mousemove', function(e) {{
  if (!panStart) return;
  transform.x = panStart.ox + (e.clientX - panStart.mx);
  transform.y = panStart.oy + (e.clientY - panStart.my);
  applyTransform();
}});
window.addEventListener('mouseup', function() {{ panStart = null; }});

svg.addEventListener('wheel', function(e) {{
  e.preventDefault();
  var scaleFactor = e.deltaY < 0 ? 1.08 : 0.92;
  var newK = Math.max(0.3, Math.min(2.5, transform.k * scaleFactor));
  var rect = svg.getBoundingClientRect();
  var cx = e.clientX - rect.left;
  var cy = e.clientY - rect.top;
  transform.x = cx - (cx - transform.x) * (newK / transform.k);
  transform.y = cy - (cy - transform.y) * (newK / transform.k);
  transform.k = newK;
  applyTransform();
}});

function autoLayout() {{
  var tables = SCHEMA.tables;
  for (var iter = 0; iter < 50; iter++) {{
    for (var i = 0; i < tables.length; i++) {{
      for (var j = i + 1; j < tables.length; j++) {{
        var pi = POSITIONS[tables[i].id], pj = POSITIONS[tables[j].id];
        var dx = pj.x - pi.x, dy = pj.y - pi.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var minDist = 300;
        if (dist < minDist) {{
          var force = (minDist - dist) * 0.05;
          var fx = (dx / dist) * force, fy = (dy / dist) * force;
          pi.x -= fx; pi.y -= fy;
          pj.x += fx; pj.y += fy;
        }}
      }}
    }}
  }}
  render();
}}

render();
resetView();
</script>
</body>
</html>
""").format(
        category_css=category_css,
        category_legend=category_legend,
        schema_js=schema_js,
        category_colors_js=category_colors_js,
        positions_js=positions_js,
        view_positions_js=view_positions_js,
        row_h=_ROW_H,
        header_h=_HEADER_H,
        pad=_PAD,
    )


# ============================================================
# Main
# ============================================================


def main():
    schema_sql = _SCHEMA_PATH.read_text()

    # Build inverted lookup from CATEGORIES definition at top of file
    table_to_category = _build_table_to_category(CATEGORIES)

    # Extract column and view comments from the raw SQL source
    column_comments = _extract_column_comments(schema_sql)
    view_comments = _extract_view_comments(schema_sql)

    # Get table and view details by creating a dummy db and
    # introspecting the schema via SQLite
    conn = _create_db(schema_sql)
    tables = _introspect_tables(conn, column_comments, table_to_category)
    views = _introspect_views(conn, view_comments)
    conn.close()

    # Compute layout positions
    table_positions = _compute_positions(tables)
    # find max y of tables to know where to start placing views below
    tables_max_y = max(
        pos["y"] + _table_height(t)
        for t, pos in ((t, table_positions[t["id"]]) for t in tables)
    )
    view_positions = _compute_view_positions(views, tables_max_y)

    # Collect which categories are actually used by tables
    used_categories = {t["category"] for t in tables}

    # Generate JS fragments
    schema_js = _to_js_object({"tables": tables, "views": views})
    category_colors_js = _build_category_colors_js(used_categories)
    table_positions_js = _build_positions_js(table_positions)
    view_positions_js = _build_positions_js(view_positions)

    # Assemble and write HTML
    html = _build_html(
        schema_js,
        category_colors_js,
        table_positions_js,
        view_positions_js,
        used_categories,
    )
    _OUTPUT_PATH.write_text(html)
    print(f"Generated {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
