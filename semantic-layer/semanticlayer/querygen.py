"""Query-template composition (design §14) — deterministic, no LLM.

For each intent binding we compose ONE parameterized, read-only SQL SELECT,
grounded entirely in the published contract:

  * SELECT   — the binding's allowed (non-restricted) attributes -> real columns
  * FROM/JOIN — the root entity's table, joined to other required entities ONLY
                via APPROVED relationships (the agent never invents a join, §23.6)
  * WHERE    — a key predicate for single-record intents (``col = :param``), the
                caller-region scope (``region_id = :caller_region``, injected by
                Prefront — never a user input), and any policy *block* rule that
                translates safely to SQL
  * LIMIT    — bounds list/search intents

A block rule whose value is symbolic (e.g. ``credit_limit < (current_balance +
order_value)``) cannot be expressed as a static predicate; it is recorded in
``runtime_policy_predicates`` for the runtime to enforce, never emitted as broken
SQL. Because composition is pure code, every template is reviewable and provably
uses only approved columns and joins.
"""

from __future__ import annotations

import re

from .policy import PolicyHints
from .schema import (
    IntentBinding,
    PhysicalCatalog,
    QueryTemplate,
    Relationship,
    ResultColumn,
    SemanticModel,
    TemplateParameter,
    WriteAction,
)

_OP = {"==": "=", "!=": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
       "in": "IN", "not_in": "NOT IN"}
# A value that is a plain literal (number / enum word), not an expression.
_LITERAL = re.compile(r"^[\w .%-]+$")
_LIST_INTENT = ("find", "list", "search", "by_region")
# Verbs that mark an intent as mutating (-> a precheck template, not a read).
_WRITE_VERBS = ("create", "update", "delete", "set", "place", "release",
                "override", "add", "submit", "remove", "approve", "raise")
_DEFAULT_LIMIT = 100


def build_query_templates(
    model: SemanticModel,
    relationships: list[Relationship],
    bindings: list[IntentBinding],
    catalog: PhysicalCatalog,
    hints: PolicyHints,
    *,
    dialect: str = "postgres",
    metrics: dict[str, str] | None = None,
    caller_context: dict[str, str] | None = None,
) -> list[QueryTemplate]:
    """``metrics`` = derived-value definitions (names excluded from request params);
    ``caller_context`` = trusted caller attribute -> scoping column (e.g.
    {"region": "region_id"}) — both are APPLICATION inputs, never hardcoded here."""
    attr_col = _attr_column_index(model)          # 'Entity.attr' -> 'table.column'
    sens = {a: lvl for a, (_c, lvl) in _attr_meta(model).items()}
    metric_names = {m.lower() for m in (metrics or {})}
    caller_context = caller_context or {}

    templates: list[QueryTemplate] = []
    for b in bindings:
        tpl = _compose(b, model, relationships, catalog, hints, attr_col, sens,
                       dialect=dialect, metric_names=metric_names,
                       caller_context=caller_context)
        if tpl:
            b.template_ids = [tpl.template_id]   # back-link binding -> template
            templates.append(tpl)
    return templates


def _caller_scope_clauses(table, caller_context, prefix=""):
    """WHERE clauses scoping rows by trusted caller attributes (config-driven).

    Scopes rows that BELONG to the caller — an owner column that is a plain/FK
    column (accounts.user_id). It does NOT scope a table where the owner column is
    the PRIMARY KEY (users.user_id), since that's the entity's own identity row:
    scoping it would hide every other entity instead of letting policy mask it."""
    clauses, attrs = [], []
    for attr, col in caller_context.items():
        if table and table.column(col) and col not in (table.primary_key or []):
            clauses.append(f"{prefix}{col} = :caller_{attr}")
            attrs.append(attr)
    return clauses, attrs


def _compose(b, model, relationships, catalog, hints, attr_col, sens, *,
             dialect, metric_names, caller_context):
    entities = b.required_entities or [model.entities[0].entity_key] if model.entities else []
    if not entities:
        return None
    root = entities[0]
    root_tbl = _table_of(model, root)
    if not root_tbl:
        return None
    if _is_write(b.intent_id):
        return _compose_precheck(b, model, catalog, hints, sens,
                                 root_tbl=root_tbl, dialect=dialect,
                                 metric_names=metric_names, caller_context=caller_context)
    used_tables = {root_tbl: root}
    joins: list[str] = []
    for ent in entities[1:]:
        tbl = _table_of(model, ent)
        rel = _approved_join(relationships, used_tables.values(), ent)
        if not tbl or not rel:
            continue  # cannot reach this entity by an approved join — skip it
        joins.append(f"JOIN {tbl} ON {rel.join.from_} = {rel.join.to}")
        used_tables[tbl] = ent
    multi = len(used_tables) > 1

    # SELECT list — allowed attributes only (restricted columns never selected).
    select_cols, result_cols = [], []
    for a in b.allowed_attributes:
        col = attr_col.get(a)
        if not col:
            continue
        select_cols.append(col if multi else col.split(".")[-1])
        result_cols.append(ResultColumn(name=a.split(".")[-1], sensitivity=sens.get(a, "normal")))
    if not select_cols:
        select_cols = ["*"]

    where: list[str] = []
    params: list[TemplateParameter] = []
    caller_ctx: list[str] = []
    is_list = any(w in b.intent_id.lower() for w in _LIST_INTENT)

    # Key predicate for single-record intents.
    pk_col = _pk_bare(catalog, root_tbl)
    if not is_list and pk_col:
        where.append(f"{(root_tbl + '.' if multi else '')}{pk_col} = :{pk_col}")
        params.append(TemplateParameter(name=pk_col, type=_col_type(catalog, root_tbl, pk_col), required=True))

    # Caller row scoping (config-driven; injected by Prefront, never a user param).
    scope_clauses, scope_attrs = _caller_scope_clauses(
        catalog.table(root_tbl), caller_context, prefix=(root_tbl + "." if multi else ""))
    where.extend(scope_clauses)
    caller_ctx.extend(scope_attrs)

    # Block-rule policies: inline what translates safely, defer the rest.
    required_policies, runtime_preds = [], []
    col_names = {c.name.lower() for t in used_tables for c in (catalog.table(t).columns if catalog.table(t) else [])}
    for rule in hints.rules_for_intent(b.intent_id):
        if rule.decision != "block" or not rule.data_conditions():
            continue
        required_policies.append(rule.rule_key)
        sql_pred, extra_params = _translate(rule.data_conditions(), col_names, multi, used_tables)
        if sql_pred is None:
            runtime_preds.append("NOT (" + _human(rule.data_conditions()) + ")  -- " + rule.rule_key)
        else:
            where.append(f"NOT ({sql_pred})")
            for p in extra_params:
                if p.name not in {x.name for x in params}:
                    params.append(p)

    sql = _render_sql(select_cols, root_tbl, joins, where, is_list, dialect)

    return QueryTemplate(
        template_id=f"tmpl_{b.intent_id}_v1",
        intent_id=b.intent_id,
        semantic_model_id=model.semantic_model_id,
        semantic_model_version=model.version,
        semantic_entities=list(used_tables.values()),
        read_only=True,
        dialect=dialect,
        sql=sql,
        parameters=params,
        required_caller_context=caller_ctx,
        result_columns=result_cols,
        required_policies=[hints.skill_id] + required_policies,
        runtime_policy_predicates=runtime_preds,
    )


def _compose_precheck(b, model, catalog, hints, sens, *, root_tbl, dialect,
                      metric_names, caller_context):
    """A read-only SELECT that gathers the governed inputs a write intent's
    policies need; the write itself is described in ``write_action``."""
    tbl = catalog.table(root_tbl)
    col_to_attr = {a.column.split(".")[-1].lower(): f"{e.entity_key}.{a.attribute_key}"
                   for e in model.entities for a in e.attributes
                   if e.entity_key == b.required_entities[0]} if b.required_entities else {}

    # Decision inputs: governed columns the rules condition on (incl. restricted).
    decision_fields: list[str] = []
    for rule in hints.rules_for_intent(b.intent_id):
        for f in [c.get("field") for c in rule.data_conditions()] + list(rule.restricted_fields):
            bare = str(f).split(".")[-1]
            if bare and tbl and tbl.column(bare) and bare not in decision_fields:
                decision_fields.append(bare)
    decision_inputs = [
        ResultColumn(name=f, sensitivity=sens.get(col_to_attr.get(f, ""), "normal"))
        for f in decision_fields
    ]

    where, params, caller_ctx = [], [], []
    pk_col = _pk_bare(catalog, root_tbl)
    if pk_col:
        where.append(f"{pk_col} = :{pk_col}")
        params.append(TemplateParameter(name=pk_col, type=_col_type(catalog, root_tbl, pk_col), required=True))
    scope_clauses, scope_attrs = _caller_scope_clauses(tbl, caller_context)
    where.extend(scope_clauses)
    caller_ctx.extend(scope_attrs)

    # The write's request params: the key + non-column, non-metric data fields.
    write_params = [pk_col] if pk_col else []
    for rule in hints.rules_for_intent(b.intent_id):
        for c in rule.data_conditions():
            bare = str(c.get("field", "")).split(".")[-1]
            low = bare.lower()
            if bare and low not in metric_names and not (tbl and tbl.column(bare)) and bare not in write_params:
                write_params.append(bare)

    # All block/approval predicates the gateway evaluates (now inputs are available).
    runtime_preds, required_policies = [], []
    for rule in hints.rules_for_intent(b.intent_id):
        if rule.decision in ("block", "approval_required") and rule.data_conditions():
            required_policies.append(rule.rule_key)
            verb = "BLOCK" if rule.decision == "block" else "APPROVAL"
            runtime_preds.append(f"{verb} when {_human(rule.data_conditions())}  -- {rule.rule_key}")

    # When no governed columns are read, confirm the row exists/scopes by its key
    # (SELECT <pk> …) rather than a bare SELECT 1.
    select = [c.name for c in decision_inputs] or ([pk_col] if pk_col else ["1"])
    sql = _render_sql(select, root_tbl, [], where, False, dialect)

    restricted_cols = {a.column.lower() for e in model.entities for a in e.attributes
                       if a.sensitivity_level == "restricted"}

    return QueryTemplate(
        template_id=f"tmpl_{b.intent_id}_precheck_v1",
        intent_id=b.intent_id,
        semantic_model_id=model.semantic_model_id,
        semantic_model_version=model.version,
        kind="precheck",
        semantic_entities=[b.required_entities[0]] if b.required_entities else [],
        read_only=True,
        dialect=dialect,
        sql=sql,
        parameters=params,
        required_caller_context=caller_ctx,
        result_columns=[],  # precheck returns nothing to the agent
        decision_inputs=decision_inputs,
        write_action=_compose_write_action(b.intent_id, catalog, write_params, caller_context,
                                           restricted_cols=restricted_cols),
        required_policies=[hints.skill_id] + required_policies,
        runtime_policy_predicates=runtime_preds,
    )


def _compose_write_action(intent, catalog, write_params, caller_context,
                          *, restricted_cols=frozenset()) -> WriteAction:
    """Build the fully DECLARATIVE write spec at design time, where the catalog
    (vocabulary) lives. The runtime executor applies it mechanically; humans
    review it with the template before approval — so the param->column matching
    below is a *suggestion heuristic*, not silent runtime behavior.

    The verb selects the mutation shape: delete/remove -> DELETE by primary key,
    update/set -> UPDATE the supplied columns by primary key, everything else ->
    INSERT. update/delete are always bounded by the key columns (+ caller scope),
    never unbounded."""
    table_name = _write_table(intent, catalog)
    t = catalog.table(table_name)
    pk = list(t.primary_key) if t else []
    verb = intent.lower().split("_")[0]

    # Trusted caller attributes that map onto write-table columns. For INSERT
    # these are values to set; for UPDATE/DELETE they scope the WHERE (e.g. a rep
    # can only delete rows in their own region).
    caller_columns = {col: attr for attr, col in (caller_context or {}).items()
                      if t and t.column(col)}

    if verb in ("delete", "remove"):
        return WriteAction(table=table_name, kind="delete", params=list(pk),
                           key_columns=list(pk), caller_columns=caller_columns)

    if verb in ("update", "set"):
        # SET surface = the table's non-key, non-restricted columns the agent may
        # supply; the runtime only writes the ones actually present on the call.
        updatable = [c.name for c in (t.columns if t else [])
                     if c.name not in pk
                     and f"{table_name}.{c.name}".lower() not in restricted_cols]
        return WriteAction(table=table_name, kind="update", params=list(pk) + updatable,
                           key_columns=list(pk), caller_columns=caller_columns)

    # INSERT (create/add/place/submit/raise/…)
    pk_set = set(pk)
    column_map: dict[str, str] = {}
    for p in write_params:
        col = _match_column(p, t, exclude=pk_set)
        if col and col != p:
            column_map[p] = col

    defaults: dict = {}
    autofill: dict = {}
    mapped_cols = {column_map.get(p, p) for p in write_params} | set(caller_columns)
    for c in (t.columns if t else []):
        if c.name in mapped_cols or c.nullable:
            continue
        if c.name in pk_set and c.type.lower().startswith(("int", "serial", "bigint", "smallint")):
            autofill[c.name] = "next_int"
        elif "date" in c.type.lower() or "timestamp" in c.type.lower():
            autofill[c.name] = "current_date"
        elif c.enum_values:
            defaults[c.name] = c.enum_values[0]   # first lifecycle state, reviewable

    return WriteAction(table=table_name, kind="insert", params=write_params,
                       column_map=column_map, caller_columns=caller_columns,
                       defaults=defaults, autofill=autofill)


def _match_column(param: str, table, exclude: set[str] = frozenset()):
    """Suggest the physical column a request param writes to: exact name match,
    else a unique same-first-token, type-compatible candidate (order_value ->
    order_total). Returns None when ambiguous — humans resolve it in review."""
    if table is None:
        return None
    if table.column(param):
        return param
    token = param.lower().split("_")[0]
    wants_number = _guessed_numeric(param)
    cands = [c.name for c in table.columns
             if c.name not in exclude
             and c.name.lower().split("_")[0] == token
             and (not wants_number or _col_is_numeric(c))]
    return cands[0] if len(cands) == 1 else None


def _guessed_numeric(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in ("value", "amount", "total", "pct", "percent",
                                "limit", "balance", "score", "qty", "quantity"))


def _col_is_numeric(col) -> bool:
    return any(s in col.type.lower() for s in ("int", "numeric", "decimal", "real",
                                               "double", "float", "money"))


# --- SQL rendering ------------------------------------------------------------


def _render_sql(select_cols, root_tbl, joins, where, is_list, dialect) -> str:
    lines = ["SELECT", "  " + ",\n  ".join(select_cols), f"FROM {root_tbl}"]
    lines += joins
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if is_list:
        lines.append(f"LIMIT {_DEFAULT_LIMIT}")
    return "\n".join(lines)


def _translate(conditions, col_names, multi, used_tables):
    """Return (sql_predicate, []) for a condition set, or (None, []) if it can't be
    expressed as a static SQL filter — in which case it's deferred to runtime.

    A condition is only inlinable when EVERY field is a real column of the queried
    table(s) and every value is a plain literal. Conditions referencing a request
    value or a non-column field (e.g. a vague 'region' vs the real 'region_id', or
    an order amount) are NOT query filters — inlining them produced bogus
    predicates like ``:region <> 'requested_region'`` — so we defer them.
    """
    parts = []
    for c in conditions:
        field = str(c.get("field", "")).split(".")[-1]
        op = _OP.get(str(c.get("operator")))
        val = c.get("value")
        if not field or op is None:
            return None, []
        if field.lower() not in col_names:
            return None, []  # not a column of this query — enforce at runtime, not in WHERE
        rhs = _literal(val)
        if rhs is None:
            return None, []  # symbolic / non-literal value — defer to runtime
        parts.append(f"{field} {op} {rhs}")
    return " AND ".join(parts), []


def _literal(val):
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        inner = ", ".join(x for x in (_literal(v) for v in val) if x is not None)
        return f"({inner})" if inner else None
    if isinstance(val, str):
        s = val.strip()
        # Reject anything that looks like an expression (operators / parens / multi-word).
        if not _LITERAL.match(s) or any(ch in s for ch in "()+*/") or "+" in s:
            return None
        return "'" + s.replace("'", "''") + "'"
    return None


# --- small helpers ------------------------------------------------------------


def _attr_column_index(model: SemanticModel) -> dict[str, str]:
    return {f"{e.entity_key}.{a.attribute_key}": a.column
            for e in model.entities for a in e.attributes}


def _attr_meta(model: SemanticModel) -> dict[str, tuple[str, str]]:
    return {f"{e.entity_key}.{a.attribute_key}": (a.column, a.sensitivity_level)
            for e in model.entities for a in e.attributes}


def _table_of(model: SemanticModel, entity_key: str) -> str | None:
    e = model.entity(entity_key)
    return e.primary_table if e else None


def _approved_join(relationships, included_entities, target_entity):
    inc = set(included_entities)
    for r in relationships:
        if not r.approved:
            continue
        if (r.from_entity == target_entity and r.to_entity in inc) or (
            r.to_entity == target_entity and r.from_entity in inc):
            return r
    return None


def _pk_bare(catalog: PhysicalCatalog, table: str) -> str | None:
    t = catalog.table(table)
    return t.primary_key[0] if t and t.primary_key else None


def _col_type(catalog: PhysicalCatalog, table: str, col: str) -> str:
    """JSON-Schema type of a column (e.g. customer_id INT -> 'integer')."""
    from .catalog import json_type

    t = catalog.table(table)
    c = t.column(col) if t else None
    return json_type(c.type) if c else "string"


def _is_write(intent: str) -> bool:
    return intent.lower().split("_")[0] in _WRITE_VERBS


def _write_table(intent: str, catalog: PhysicalCatalog) -> str:
    """Best-effort: the noun after the verb -> a real table (e.g. create_order -> orders)."""
    parts = intent.lower().split("_")
    noun = parts[1] if len(parts) > 1 else parts[0]
    for cand in (noun, noun + "s", noun + "es"):
        if catalog.table(cand):
            return cand
    return noun + "s"


def _numeric(val) -> bool:
    return isinstance(val, (int, float)) or (
        isinstance(val, str) and val.strip().replace(".", "", 1).isdigit())


def _human(conditions) -> str:
    out = []
    for c in conditions:
        v = c.get("value")
        v = f"'{v}'" if isinstance(v, str) else v
        out.append(f"{c.get('field')} {c.get('operator')} {v}")
    return " AND ".join(out)
