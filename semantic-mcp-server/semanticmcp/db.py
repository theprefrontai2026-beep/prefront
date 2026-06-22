"""Postgres access (psycopg 3).

The templates use ``:name`` placeholders; psycopg wants ``%(name)s``. We translate
``:name`` -> ``%(name)s`` (leaving ``::type`` casts alone) and pass a dict of binds.
Reads run inside a read-only transaction.
"""

from __future__ import annotations

import re
from typing import Any

# :name  (not preceded by ':' so '::cast' is untouched, not followed by another ':')
_PLACEHOLDER = re.compile(r"(?<!:):(\w+)\b(?!:)")

DEFAULT_DSN = "postgresql://example:example@localhost:5433/example"


def placeholders(sql: str) -> list[str]:
    """Distinct ``:name`` placeholders in declaration order."""
    seen: dict[str, None] = {}
    for m in _PLACEHOLDER.finditer(sql):
        seen.setdefault(m.group(1), None)
    return list(seen)


def to_pyformat(sql: str) -> str:
    """Rewrite ``:name`` placeholders to psycopg ``%(name)s``."""
    return _PLACEHOLDER.sub(lambda m: f"%({m.group(1)})s", sql)


def connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn)


def run_select(dsn: str, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute a read-only SELECT and return rows as dicts."""
    from psycopg.rows import dict_row

    with connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(to_pyformat(sql), binds)
            return [dict(r) for r in cur.fetchall()]


def ping(dsn: str) -> str:
    """Return the server version string, raising on failure."""
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            return cur.fetchone()[0]
