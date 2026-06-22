"""Derive a default domain pack from a datasource DDL.

The binder's authority is the real schema, so a pack built from the columns is
the most bind-faithful baseline we can offer: every column becomes a
``binds_to: column`` field, guaranteeing the symbols we ground the LLM in are the
ones the runtime can actually resolve. It is intentionally *partial* — a schema
yields columns (and, via CHECK constraints, some enums) but not roles, intents,
request params, metrics, or business aliases. Those come from a curated named
pack, layered on top via :func:`merge_packs`.

This is a lightweight, dependency-free DDL reader: it finds ``CREATE TABLE``
blocks, splits their column definitions, and maps SQL types onto the pack's
field types. It is a design-time pre-check, not a SQL parser.
"""

from __future__ import annotations

import logging
import re

from .schema import DomainPack, PackField

log = logging.getLogger(__name__)

# Column-name hints that turn a numeric type into "money" rather than "number".
_MONEY_HINTS = (
    "amount", "balance", "price", "cost", "total", "limit", "fee",
    "revenue", "salary", "payment", "credit", "debit", "value",
)
# Leading tokens that mark a table-level constraint, not a column definition.
_CONSTRAINT_KW = (
    "primary", "foreign", "unique", "check", "constraint", "key", "exclude",
    "like", "index",
)


def _humanize(col: str) -> str:
    """current_balance -> 'current balance' (a free, if shallow, alias)."""
    return col.replace("_", " ").strip()


def _field_type(col: str, sqltype: str) -> str:
    t = sqltype.lower()
    if any(x in t for x in ("int", "serial", "numeric", "decimal", "real", "double", "float", "money")):
        return "money" if any(h in col.lower() for h in _MONEY_HINTS) else "number"
    return "string"


def _table_bodies(ddl: str) -> list[tuple[str, str]]:
    """Yield (table_name, paren_body) for each CREATE TABLE, paren-balanced."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([^\s(]+)\s*\(", ddl, re.I):
        name = m.group(1).strip('`"').split(".")[-1]
        depth = 0
        start = m.end()
        for j in range(m.end() - 1, len(ddl)):
            if ddl[j] == "(":
                depth += 1
            elif ddl[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append((name, ddl[start:j]))
                    break
    return out


def _split_columns(body: str) -> list[str]:
    """Split a table body on top-level commas (ignoring commas inside parens)."""
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _enum_values(coldef: str) -> list[str] | None:
    """Pull allowed values from an inline CHECK (col IN ('a','b'))."""
    m = re.search(r"\bin\s*\(([^)]*)\)", coldef, re.I)
    if not m:
        return None
    vals = re.findall(r"'([^']*)'", m.group(1))
    return vals or None


def pack_from_schema(ddl: str, *, domain: str = "schema") -> DomainPack:
    """Build a column-only :class:`DomainPack` from a datasource DDL."""
    fields: dict[str, PackField] = {}
    for _table, body in _table_bodies(ddl or ""):
        for coldef in _split_columns(body):
            coldef = coldef.strip()
            if not coldef:
                continue
            tokens = coldef.split()
            first = tokens[0].strip('`"').lower()
            if first in _CONSTRAINT_KW or len(tokens) < 2:
                continue
            col = tokens[0].strip('`"')
            sqltype = tokens[1]
            # Last definition wins if a column name repeats across tables; that's
            # fine for a grounding pre-check (the binder dedups by real catalog).
            fields[col] = PackField(
                type=_field_type(col, sqltype),
                binds_to="column",
                allowed_values=_enum_values(coldef),
                aliases=[_humanize(col)],
            )
    log.info("derived schema pack: %d column field(s) from DDL", len(fields))
    return DomainPack(domain=domain, version="schema", fields=fields)


def merge_packs(base: DomainPack, overlay: DomainPack) -> DomainPack:
    """Layer a curated pack (``overlay``) over a schema pack (``base``).

    The overlay wins on every collision (its types, enums, roles, intents,
    metrics, reason codes are authoritative); aliases are unioned so the curated
    business synonyms and the schema's humanized names both resolve.
    """
    fields: dict[str, PackField] = {}
    for k in set(base.fields) | set(overlay.fields):
        b, o = base.fields.get(k), overlay.fields.get(k)
        if b and o:
            fields[k] = PackField(
                type=o.type or b.type,
                binds_to=o.binds_to or b.binds_to,
                allowed_values=o.allowed_values if o.allowed_values is not None else b.allowed_values,
                aliases=list(dict.fromkeys([*b.aliases, *o.aliases])),
            )
        else:
            fields[k] = o or b  # type: ignore[assignment]
    return DomainPack(
        domain=overlay.domain or base.domain,
        version=overlay.version,
        fields=fields,
        roles={**base.roles, **overlay.roles},
        actions={**base.actions, **overlay.actions},
        reason_codes={**base.reason_codes, **overlay.reason_codes},
    )
