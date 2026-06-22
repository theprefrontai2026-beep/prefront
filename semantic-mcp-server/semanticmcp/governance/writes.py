"""Write executor — pure MECHANISM, no application knowledge.

Performs a template's declarative ``write_action`` on an ALLOWED decision. All
vocabulary (param->column mapping, caller-attribute columns, lifecycle defaults,
autofill tokens) was decided at DESIGN TIME, reviewed by a human with the
template, and ships in the published artifact:

    write_action:
      table: orders
      params: [customer_id, order_value, discount_percentage]
      column_map: {order_value: order_total, discount_percentage: discount_pct}
      caller_columns: {rep_id: rep_id}      # column <- caller attribute
      defaults: {status: draft}             # literals for NOT NULL enum columns
      autofill: {order_id: next_int, order_date: current_date}

Dry-run by default; ENABLE_WRITES executes. Nothing here names any table or
column — it only interprets the spec.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .. import db
from .context import Caller


def enabled() -> bool:
    return os.environ.get("ENABLE_WRITES", "").strip().lower() in ("1", "true", "yes")


def perform(
    dsn: str,
    write_action: Optional[dict],
    params: dict[str, Any],
    caller: Optional[Caller],
) -> dict:
    """Execute (or dry-run) the declarative write spec. Returns a JSON-able block.

    The spec's ``kind`` selects the mutation: insert (create), or update/delete of
    a single row matched by ``key_columns`` (+ caller scope). update/delete are
    refused if that match is empty — never an unbounded mutation."""
    wa = write_action or {}
    table = wa.get("table")
    if not table:
        return {"mode": "error", "error": "template has no write_action.table"}

    kind = wa.get("kind", "insert")
    if kind == "delete":
        return _perform_delete(dsn, wa, params, caller)
    if kind == "update":
        return _perform_update(dsn, wa, params, caller)

    column_map = wa.get("column_map") or {}
    values: dict[str, Any] = {
        column_map.get(p, p): v for p, v in (params or {}).items() if v is not None
    }
    for col, attr in (wa.get("caller_columns") or {}).items():
        v = caller.attrs.get(attr) if caller else None
        if v is not None:
            values.setdefault(col, v)
    for col, literal in (wa.get("defaults") or {}).items():
        values.setdefault(col, literal)
    autofill = {c: t for c, t in (wa.get("autofill") or {}).items() if c not in values}

    plan = {"table": table, "values": values, "autofill": autofill}
    if not enabled():
        return {"mode": "dry_run", "would_insert": plan,
                "note": "set ENABLE_WRITES=1 to execute writes"}

    import psycopg
    from psycopg import sql as psql

    try:
        with db.connect(dsn) as conn, conn.cursor() as cur:
            for col, token in autofill.items():
                if token == "next_int":
                    cur.execute(
                        psql.SQL("SELECT COALESCE(MAX({}),0)+1 FROM {}").format(
                            psql.Identifier(col), psql.Identifier(table)))
                    values[col] = cur.fetchone()[0]
                # current_date is rendered as a SQL keyword below

            names = list(values) + [c for c, t in autofill.items() if t == "current_date"]
            rendered = [psql.Placeholder(n) for n in values] + [
                psql.SQL("CURRENT_DATE")
                for _, t in autofill.items() if t == "current_date"
            ]
            stmt = psql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                psql.Identifier(table),
                psql.SQL(", ").join(psql.Identifier(n) for n in names),
                psql.SQL(", ").join(rendered),
            )
            cur.execute(stmt, values)
            inserted = cur.fetchone()
            conn.commit()
            return {"mode": "executed", "table": table,
                    "inserted": {d.name: v for d, v in zip(cur.description, inserted)}}
    except psycopg.Error as e:
        return {"mode": "error", "error": f"{type(e).__name__}: {e}", "would_insert": plan}


def _match(wa: dict, params: dict[str, Any], caller: Optional[Caller]) -> dict[str, Any]:
    """Column->value predicate that bounds an update/delete: the row's key columns
    (supplied by the caller) plus any caller-scoped columns (from identity). An
    empty result means we cannot bound the mutation — the caller refuses it."""
    match: dict[str, Any] = {}
    for col in (wa.get("key_columns") or []):
        v = (params or {}).get(col)
        if v is not None:
            match[col] = v
    for col, attr in (wa.get("caller_columns") or {}).items():
        v = caller.attrs.get(attr) if caller else None
        if v is not None:
            match.setdefault(col, v)
    return match


def _perform_delete(dsn: str, wa: dict, params: dict[str, Any], caller: Optional[Caller]) -> dict:
    table = wa["table"]
    match = _match(wa, params, caller)
    if not match:
        return {"mode": "error",
                "error": "delete has no key/scope predicate — refusing unbounded DELETE"}
    if not enabled():
        return {"mode": "dry_run", "would_delete": {"table": table, "match": match},
                "note": "set ENABLE_WRITES=1 to execute writes"}

    import psycopg
    from psycopg import sql as psql

    where = psql.SQL(" AND ").join(
        psql.SQL("{} = {}").format(psql.Identifier(c), psql.Placeholder(f"w_{c}")) for c in match)
    vals = {f"w_{c}": v for c, v in match.items()}
    try:
        with db.connect(dsn) as conn, conn.cursor() as cur:
            stmt = psql.SQL("DELETE FROM {} WHERE {} RETURNING *").format(
                psql.Identifier(table), where)
            cur.execute(stmt, vals)
            rows = cur.fetchall()
            conn.commit()
            return {"mode": "executed", "table": table, "deleted": len(rows),
                    "rows": [{d.name: v for d, v in zip(cur.description, r)} for r in rows]}
    except psycopg.Error as e:
        return {"mode": "error", "error": f"{type(e).__name__}: {e}",
                "would_delete": {"table": table, "match": match}}


def _perform_update(dsn: str, wa: dict, params: dict[str, Any], caller: Optional[Caller]) -> dict:
    table = wa["table"]
    column_map = wa.get("column_map") or {}
    key_cols = set(wa.get("key_columns") or [])
    # SET = supplied non-key params (mapped to columns); only what the call carries.
    set_values: dict[str, Any] = {}
    for p, v in (params or {}).items():
        col = column_map.get(p, p)
        if col not in key_cols and v is not None:
            set_values[col] = v
    match = _match(wa, params, caller)
    if not match:
        return {"mode": "error",
                "error": "update has no key/scope predicate — refusing unbounded UPDATE"}
    if not set_values:
        return {"mode": "error", "error": "update has no columns to set"}
    if not enabled():
        return {"mode": "dry_run",
                "would_update": {"table": table, "set": set_values, "match": match},
                "note": "set ENABLE_WRITES=1 to execute writes"}

    import psycopg
    from psycopg import sql as psql

    set_sql = psql.SQL(", ").join(
        psql.SQL("{} = {}").format(psql.Identifier(c), psql.Placeholder(f"s_{c}")) for c in set_values)
    where = psql.SQL(" AND ").join(
        psql.SQL("{} = {}").format(psql.Identifier(c), psql.Placeholder(f"w_{c}")) for c in match)
    vals = {f"s_{c}": v for c, v in set_values.items()}
    vals.update({f"w_{c}": v for c, v in match.items()})
    try:
        with db.connect(dsn) as conn, conn.cursor() as cur:
            stmt = psql.SQL("UPDATE {} SET {} WHERE {} RETURNING *").format(
                psql.Identifier(table), set_sql, where)
            cur.execute(stmt, vals)
            rows = cur.fetchall()
            conn.commit()
            return {"mode": "executed", "table": table, "updated": len(rows),
                    "rows": [{d.name: v for d, v in zip(cur.description, r)} for r in rows]}
    except psycopg.Error as e:
        return {"mode": "error", "error": f"{type(e).__name__}: {e}",
                "would_update": {"table": table, "set": set_values, "match": match}}
