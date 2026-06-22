"""Physical catalog builder: PostgreSQL DDL -> PhysicalCatalog (design §5).

A self-contained, lightweight regex parser over ``CREATE TABLE`` blocks. It
captures tables, columns + types + nullability, primary keys (inline and
table-level), foreign keys (inline ``REFERENCES`` and table-level ``FOREIGN
KEY``), enum domains from ``CHECK (col IN (...))``, and any ``[SENSITIVE]`` /
``[GOVERNED]`` markers written in trailing line comments — these are the
hand-authored hints the schema author left for the governance layer.

Deterministic: no LLM. Covers standard ANSI/Postgres DDL (any typical app
schema). Nothing here imports template-store.
"""

from __future__ import annotations

import re
from pathlib import Path

from .logutil import get_logger
from .schema import ForeignKey, PhysicalCatalog, PhysicalColumn, PhysicalTable

log = get_logger(__name__)

_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)[\"`]?\s*\((.*?)\)\s*;",
    re.I | re.S,
)
_CHECK_IN = re.compile(r"CHECK\s*\(\s*[\"`]?(\w+)[\"`]?\s+IN\s*\(([^)]*)\)", re.I)
_REFERENCES = re.compile(r"REFERENCES\s+[\"`]?(\w+)[\"`]?\s*\(\s*([^)]*)\)", re.I)
_TABLE_CONSTRAINT = re.compile(
    r"^(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT)\b", re.I
)
_MARKER = re.compile(r"\[([A-Z_]+)\]")
_LEADING_IDENT = re.compile(r"^\s*[\"`]?(\w+)")

# Map a raw SQL column type to a JSON-Schema type (for tool/template parameters).
_JSON_TYPE = [
    (re.compile(r"\b(serial|bigserial)\b", re.I), "integer"),
    (re.compile(r"\b(int|integer|bigint|smallint)\b", re.I), "integer"),
    (re.compile(r"\b(numeric|decimal|real|double|float|money)\b", re.I), "number"),
    (re.compile(r"\b(bool|boolean)\b", re.I), "boolean"),
]


def json_type(sql_type: str) -> str:
    """Coarse JSON-Schema type for a SQL column type (default: string)."""
    for pat, kind in _JSON_TYPE:
        if pat.search(sql_type or ""):
            return kind
    return "string"


def build_catalog(ddl: str, *, datasource_id: str, type_: str = "postgresql") -> PhysicalCatalog:
    """Parse DDL text into a published-shaped physical catalog."""
    ddl = _strip_block_comments(ddl)
    tables: list[PhysicalTable] = []
    for m in _CREATE.finditer(ddl):
        tables.append(_parse_table(m.group(1), m.group(2)))
    return PhysicalCatalog(datasource_id=datasource_id, type=type_, tables=tables)


def build_catalog_from_file(path: str | Path, *, datasource_id: str | None = None) -> PhysicalCatalog:
    p = Path(path)
    return build_catalog(
        p.read_text(encoding="utf-8"),
        datasource_id=datasource_id or p.stem,
    )


def build_catalog_from_dsn(
    dsn: str, *, schema: str = "public", datasource_id: str | None = None
) -> PhysicalCatalog:
    """Introspect a live PostgreSQL database into a physical catalog (no LLM).

    Reads tables/columns (type + nullability) and primary/foreign keys from
    ``information_schema``. Enum domains and [SENSITIVE] markers are DDL-only and
    not recovered here.
    """
    import psycopg

    cat = PhysicalCatalog(datasource_id=datasource_id or "datasource", type="postgresql")
    tables: dict[str, PhysicalTable] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT table_name, column_name, data_type, is_nullable
               FROM information_schema.columns
               WHERE table_schema = %s
               ORDER BY table_name, ordinal_position""",
            (schema,),
        )
        for tname, cname, dtype, nullable in cur.fetchall():
            t = tables.setdefault(tname, PhysicalTable(name=tname))
            t.columns.append(
                PhysicalColumn(name=cname, type=dtype, nullable=(nullable == "YES"))
            )

        cur.execute(
            """SELECT tc.constraint_type, kcu.table_name, kcu.column_name,
                      ccu.table_name AS ref_table, ccu.column_name AS ref_column
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                 ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
               LEFT JOIN information_schema.constraint_column_usage ccu
                 ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
               WHERE tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
                 AND tc.table_schema = %s""",
            (schema,),
        )
        for ctype, tname, cname, ref_table, ref_column in cur.fetchall():
            t = tables.get(tname)
            if not t:
                continue
            if ctype == "PRIMARY KEY":
                if cname not in t.primary_key:
                    t.primary_key.append(cname)
                col = t.column(cname)
                if col:
                    col.is_primary_key = True
            elif ctype == "FOREIGN KEY" and ref_table:
                t.foreign_keys.append(
                    ForeignKey(from_columns=[cname], to_table=ref_table, to_columns=[ref_column])
                )
    cat.tables = list(tables.values())
    return cat


# --- internals ----------------------------------------------------------------


def _parse_table(name: str, body: str) -> PhysicalTable:
    # First pass: line-scan to map each column name -> its comment markers. A
    # marker always sits on the line that *starts* with the column name, even
    # when the trailing comment follows the terminating comma.
    markers: dict[str, list[str]] = {}
    for line in body.splitlines():
        if "--" not in line:
            continue
        ident = _LEADING_IDENT.match(line)
        comment = line.split("--", 1)[1]
        found = _MARKER.findall(comment)
        if ident and found:
            markers.setdefault(ident.group(1).lower(), []).extend(found)

    # Second pass: structural parse over comment-stripped, comma-split items.
    table = PhysicalTable(name=name)
    for item in _split_top_level(_strip_line_comments(body)):
        if _TABLE_CONSTRAINT.match(item):
            _parse_table_constraint(item, table)
            continue
        col = _parse_column(item, table)
        if col:
            col.markers = markers.get(col.name.lower(), [])
            table.columns.append(col)
    if not table.primary_key:
        table.primary_key = [c.name for c in table.columns if c.is_primary_key]
    return table


def _parse_column(item: str, table: PhysicalTable) -> PhysicalColumn | None:
    tokens = item.split()
    if not tokens:
        return None
    name = tokens[0].strip('"`')
    sql_type = tokens[1] if len(tokens) > 1 else "text"
    upper = item.upper()
    col = PhysicalColumn(
        name=name,
        type=sql_type,
        nullable="NOT NULL" not in upper and "PRIMARY KEY" not in upper,
        is_primary_key="PRIMARY KEY" in upper,
    )
    chk = _CHECK_IN.search(item)
    if chk and chk.group(1).strip('"`').lower() == name.lower():
        col.enum_values = _value_list(chk.group(2))
    ref = _REFERENCES.search(item)
    if ref:
        table.foreign_keys.append(
            ForeignKey(
                from_columns=[name],
                to_table=ref.group(1),
                to_columns=[c.strip().strip('"`') for c in ref.group(2).split(",")],
            )
        )
    return col


def _parse_table_constraint(item: str, table: PhysicalTable) -> None:
    up = item.upper()
    if up.startswith("PRIMARY KEY"):
        cols = re.search(r"\(([^)]*)\)", item)
        if cols:
            table.primary_key = [c.strip().strip('"`') for c in cols.group(1).split(",")]
    elif up.startswith("FOREIGN KEY"):
        m = re.search(
            r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+[\"`]?(\w+)[\"`]?\s*\(([^)]*)\)",
            item, re.I,
        )
        if m:
            table.foreign_keys.append(
                ForeignKey(
                    from_columns=[c.strip().strip('"`') for c in m.group(1).split(",")],
                    to_table=m.group(2),
                    to_columns=[c.strip().strip('"`') for c in m.group(3).split(",")],
                )
            )
    elif up.startswith("CHECK"):
        chk = _CHECK_IN.search(item)
        if chk:
            col = table.column(chk.group(1))
            if col:
                col.enum_values = _value_list(chk.group(2))


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE-body on commas that are not inside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _strip_line_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _strip_block_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.S)


def _value_list(raw: str) -> list[str]:
    return [v.strip().strip("'\"") for v in raw.split(",") if v.strip()]


# --- intent suggestion (default operations a human then curates) -------------


def _singular(name: str) -> str:
    """Best-effort singularize a table name for entity-shaped intent verbs
    (customers -> customer, categories -> category, addresses -> address)."""
    n = name.lower()
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    for suf in ("ches", "shes", "sses", "xes", "zes", "ses"):
        if n.endswith(suf):
            return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def suggest_intents(catalog: PhysicalCatalog) -> list[str]:
    """Derive a default, EDITABLE set of governed operations from the schema.

    Per table: ``find_<table>`` + ``get_<entity>`` (reads); plus ``create_`` and
    ``delete_<entity>`` for tables with a single-column primary key (a top-level
    entity, not a composite-key junction table like ``order_items``). ``update_``
    is intentionally NOT suggested — "update which fields" is underspecified for
    auto-generation and should be authored deliberately (the engine still supports
    an update write_action when an update intent is defined by hand).

    These are only *suggestions* a human curates — Prefront never auto-exposes a
    CRUD surface to agents; the operation set is a deliberate, approved choice.
    Domain verbs (release_order, approve_credit) are not entity-derivable and are
    added by hand.
    """
    out: list[str] = []
    for t in catalog.tables:
        plural = t.name.lower()
        entity = _singular(plural)
        out += [f"find_{plural}", f"get_{entity}"]
        if len(t.primary_key) == 1:
            out += [f"create_{entity}", f"delete_{entity}"]
    seen: set[str] = set()
    uniq: list[str] = []
    for i in out:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    log.debug("suggest_intents: %d tables -> %d suggested intents: %s",
              len(catalog.tables), len(uniq), uniq)
    return uniq
