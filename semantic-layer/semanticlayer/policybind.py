"""Publish-time policy binder — reconciles rule vocabulary with the datasource.

Every symbol a business rule conditions on must resolve to exactly one of four
namespaces before the rule may be enforced at runtime:

  column         a real column in the physical catalog (root table of the
                 intent's template first, then any table)
  request_param  a value the agent supplies on the tool call — must be declared
                 by the intent's published template (parameters / write_action)
  metric         a derived value the runtime computes (e.g. available_credit)
  caller         trusted caller context injected by identity (caller.role, …)

A symbol that resolves to none of these is a vocabulary mismatch (e.g. a rule on
`region` when the column is `region_id`): the rule is REJECTED at publish time
with a reason, so the runtime never sees unresolvable policy. The resulting
bundle embeds a per-rule ``bindings`` map plus per-condition value annotations
(`value_kind: literal|expression`, `value_refs`) so the runtime does plain
dictionary lookups — no name guessing on the hot path.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Optional

from .schema import PhysicalCatalog

_FROM = re.compile(r"\bFROM\s+([\w\"]+)", re.I)


def bind_rules(
    rules: list[dict],
    catalog: PhysicalCatalog,
    templates: list[dict],
    metrics: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict], list[str], dict]:
    """Bind every rule symbol. Returns (bound, rejected, skipped_no_intent, intents_map).

    ``templates`` are the persisted query-template dicts (they carry the
    declared parameters + write_action params per intent — the request-param
    whitelist) and the SQL (whose FROM table gives column-resolution priority).
    ``metrics`` are the APPLICATION's derived-value definitions (python
    expressions over bound symbols) — supplied by the caller, never hardcoded.
    """
    metrics = metrics or {}
    params_by_intent: dict[str, set[str]] = {}
    root_table_by_intent: dict[str, str] = {}
    for t in templates:
        intent = t.get("intent_id")
        if not intent:
            continue
        names = {p["name"] for p in (t.get("parameters") or [])}
        names |= set((t.get("write_action") or {}).get("params") or [])
        params_by_intent.setdefault(intent, set()).update(names)
        m = _FROM.search(t.get("sql") or "")
        if m:
            root_table_by_intent[intent] = m.group(1).strip('"').lower()

    bound: list[dict] = []
    rejected: list[dict] = []
    skipped: list[str] = []
    intents_map: dict[str, dict] = {}
    seen: set[str] = set()

    for r in rules:
        key = r.get("rule_key", "")
        intents = [i for i in (r.get("applies_to_intents") or []) if i]
        effect = r.get("effect") or {}
        decision = effect.get("decision", "")
        conditions = r.get("conditions") or []

        # Dedupe identical rules coming from multiple documents.
        sig = f"{key}|{sorted(intents)}|{conditions}"
        if sig in seen:
            continue
        seen.add(sig)

        if not intents:
            skipped.append(key)
            continue

        # An *allow* rule keyed on caller.role defines who may invoke the intent
        # (authorization), not a per-call predicate — fold into allowed_roles.
        if decision == "allow":
            roles = _caller_roles(conditions)
            if roles:
                for i in intents:
                    entry = intents_map.setdefault(i, {"allowed_roles": []})
                    for role in roles:
                        if role not in entry["allowed_roles"]:
                            entry["allowed_roles"].append(role)
            continue

        bindings: dict[str, dict] = {}
        problems: list[str] = []
        bound_conditions: list[dict] = []

        for c in conditions:
            field_name = str(c.get("field", "")).strip()
            op = c.get("operator")
            value = c.get("value")
            b = _bind_symbol(field_name, intents, catalog, params_by_intent, root_table_by_intent, metrics)
            if b is None:
                problems.append(f"field {field_name!r} does not resolve to a column, "
                                f"declared request parameter, metric, or caller attribute")
            else:
                bindings[field_name] = b

            value_kind, refs = _classify_value(
                value, intents, catalog, params_by_intent, root_table_by_intent, metrics
            )
            if value_kind == "unresolvable":
                problems.append(f"value {value!r} references unresolvable symbol(s)")
                value_kind = "literal"
            for name, vb in refs.items():
                bindings.setdefault(name, vb)
            bound_conditions.append({
                "field": field_name, "operator": op, "value": value,
                "value_kind": value_kind,
                "value_refs": sorted(refs),
            })

        # restricted_fields should be real columns (masking targets); warn-only.
        restricted = effect.get("restricted_fields") or []
        for f in restricted:
            b = _bind_symbol(f, intents, catalog, params_by_intent, root_table_by_intent, metrics)
            if b:
                bindings.setdefault(f, b)

        if problems:
            rejected.append({"rule_key": key, "intents": intents, "reasons": problems})
            continue

        bound.append({
            "rule_key": key,
            "rule_type": r.get("rule_type", "restriction"),
            "intents": intents,
            "conditions": bound_conditions,
            "effect": {k: v for k, v in effect.items() if v not in (None, [], "")},
            "bindings": bindings,
        })

    return bound, rejected, skipped, intents_map


# --- symbol resolution ----------------------------------------------------------


def _bind_symbol(
    name: str,
    intents: list[str],
    catalog: PhysicalCatalog,
    params_by_intent: dict[str, set[str]],
    root_table_by_intent: dict[str, str],
    metrics: dict[str, str],
) -> Optional[dict]:
    if not name:
        return None
    if name.startswith("caller."):
        return {"source": "caller", "attribute": name.split(".", 1)[1]}
    bare = name.split(".")[-1]
    low = bare.lower()
    if low in {m.lower() for m in metrics}:
        expr = metrics.get(low) or next(v for k, v in metrics.items() if k.lower() == low)
        return {"source": "metric", "expression": expr}
    # Column: the intent's root table wins collisions, then any table.
    tables = [root_table_by_intent[i] for i in intents if i in root_table_by_intent]
    tables += [t.name for t in catalog.tables]
    for tname in tables:
        t = catalog.table(tname)
        if t and t.column(bare):
            return {"source": "column", "column": f"{t.name}.{bare}"}
    # Request parameter: must be DECLARED by the intent's published template.
    for i in intents:
        if low in {p.lower() for p in params_by_intent.get(i, set())}:
            return {"source": "request_param"}
    return None


def _classify_value(value, intents, catalog, params_by_intent, root_table_by_intent, metrics):
    """literal | expression (with refs) | unresolvable."""
    if not isinstance(value, str):
        return "literal", {}
    s = value.strip()
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return "literal", {}
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    if not names:
        return "literal", {}
    # Only treat as an expression if it's arithmetic/identifiers (no calls etc.).
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name,
                                 ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div,
                                 ast.USub, ast.Load)):
            return "literal", {}
    refs: dict[str, dict] = {}
    for name in names:
        b = _bind_symbol(name, intents, catalog, params_by_intent, root_table_by_intent, metrics)
        if b is None:
            # e.g. value 'hold' parses as a Name but is just an enum literal.
            return ("literal", {}) if len(names) == 1 else ("unresolvable", {})
        refs[name] = b
    return "expression", refs


def _caller_roles(conditions: list[dict]) -> list[str]:
    roles: list[str] = []
    for c in conditions:
        if str(c.get("field", "")).strip().lower() in ("caller.role", "role"):
            v = c.get("value")
            for item in (v if isinstance(v, list) else [v]):
                if item:
                    roles.append(_norm_role(str(item)))
    return roles


def _norm_role(role: str) -> str:
    return role.strip().lower().replace(" ", "_")
