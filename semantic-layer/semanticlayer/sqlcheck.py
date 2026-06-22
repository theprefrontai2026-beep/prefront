"""Design-time SQL validation of generated query templates (design §14).

Parses each template's SQL into an AST (via sqlglot) and asserts it conforms to
the approved contract — BEFORE the template is ever published/served. This runs
at generation time only; the runtime executes the fixed, pre-validated template
verbatim (binding parameter values), so it does not re-parse on the hot path.

Checks (the §14 subset verifiable from the SQL text + catalog/sensitivity):
  * read-only — a single SELECT, no DML/DDL
  * only approved tables — every table exists in the physical catalog
  * real columns — every referenced column exists on a used table
  * joins are FK-backed — each join column pair matches an approved relationship
  * restricted fields absent — a `read` template never selects a restricted column
    (a `precheck` template intentionally selects governed decision inputs, so its
    projection is exempt)
  * parameters safely bound — every ``:placeholder`` is a declared parameter or an
    injected ``:caller_*`` context value (nothing inlined / undeclared)
"""

from __future__ import annotations

import re

from .schema import (
    PhysicalCatalog,
    QueryTemplate,
    Relationship,
    SemanticModel,
    SensitivityRule,
)

_PH = re.compile(r"(?<!:):(\w+)")  # :name placeholders (not ::casts)


def check_templates(
    catalog: PhysicalCatalog,
    model: SemanticModel,
    relationships: list[Relationship],
    sensitivity: list[SensitivityRule],
    templates: list[QueryTemplate],
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for all templates. Graceful no-op if sqlglot is absent."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return [], ["sqlglot not installed — SQL §14 checks skipped"]

    restricted_bare = {s.physical_column.split(".")[-1].lower() for s in sensitivity}
    # approved join column pairs (both directions), bare-column form
    approved_joins = set()
    for r in relationships:
        a = r.join.from_.split(".")[-1].lower()
        b = r.join.to.split(".")[-1].lower()
        approved_joins.add((a, b))
        approved_joins.add((b, a))

    errors: list[str] = []
    warnings: list[str] = []
    for t in templates:
        tag = f"template {t.template_id!r}"
        placeholders = set(_PH.findall(t.sql))
        # Substitute placeholders with a literal so the AST parses cleanly; the
        # placeholder NAMES are validated separately from the regex set above.
        parseable = _PH.sub("1", t.sql)
        try:
            tree = sqlglot.parse_one(parseable, read=t.dialect or "postgres")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag}: unparseable SQL — {e}")
            continue
        if tree is None:
            errors.append(f"{tag}: empty SQL")
            continue

        # read-only: a SELECT with no mutating statement anywhere
        if tree.find(exp.Select) is None or any(
            tree.find(node) for node in (exp.Insert, exp.Update, exp.Delete,
                                         exp.Create, exp.Drop, exp.Alter)
        ):
            errors.append(f"{tag}: not read-only (must be a single SELECT)")

        used_tables = {tbl.name.lower() for tbl in tree.find_all(exp.Table)}
        for name in used_tables:
            if not catalog.table(name):
                errors.append(f"{tag}: references unknown table {name!r} (§14.2)")

        # every referenced column must exist on one of the used tables
        cat_tables = [catalog.table(n) for n in used_tables if catalog.table(n)]
        for col in tree.find_all(exp.Column):
            cname = col.name
            if cname and not any(t2.column(cname) for t2 in cat_tables):
                errors.append(f"{tag}: references unknown column {cname!r} (§14.2)")

        # joins must be FK-backed (approved relationships)
        for join in tree.find_all(exp.Join):
            cols = [c.name.lower() for c in join.find_all(exp.Column)]
            pair = tuple(cols[:2])
            if len(pair) == 2 and pair not in approved_joins:
                errors.append(f"{tag}: join on {pair} is not an approved relationship (§14.4)")

        # restricted columns must not appear in a read template's projection
        if t.kind == "read":
            select = tree.find(exp.Select)
            projected = {c.name.lower() for e in (select.expressions if select else [])
                         for c in e.find_all(exp.Column)}
            leaked = sorted(projected & restricted_bare)
            if leaked:
                errors.append(f"{tag}: selects restricted column(s) {leaked} (§14.6)")

        # every placeholder must be a declared parameter or injected caller context
        declared = {p.name for p in t.parameters} | {
            f"caller_{c}" for c in t.required_caller_context
        }
        undeclared = sorted(placeholders - declared)
        if undeclared:
            errors.append(f"{tag}: undeclared placeholder(s) {undeclared} (§14.8)")

    return errors, warnings
