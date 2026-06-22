"""Executability validator — the design-time mirror of the runtime binder.

Every condition field (and every identifier inside an arithmetic ``value``) must
resolve to one of the four namespaces — column / request_param / metric /
caller — exactly as ``semantic-layer/semanticlayer/policybind.py`` requires at
publish time. A symbol that resolves to none of them is reported as an unresolved
item (so it is fixed before publish, not rejected after).
"""

from __future__ import annotations

import ast
import re
from typing import Optional

from ..schema import CandidateRule

_SIMPLE_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def resolve_symbol(
    name: str,
    pack,
    declared_params: set[str],
    metrics: set[str],
) -> Optional[str]:
    """Namespace a symbol binds to, or None. Mirrors policybind._bind_symbol."""
    if not name:
        return None
    if name.startswith("caller."):
        return "caller"
    bare = name.split(".")[-1].lower()
    if pack is not None:
        ns = pack.resolve_field(name)
        if ns:
            return ns
    if bare in {m.lower() for m in metrics}:
        return "metric"
    if bare in {p.lower() for p in declared_params}:
        return "request_param"
    return None


def _expr_names(value) -> list[str]:
    """Identifier names if ``value`` is a safe arithmetic expression, else []."""
    if not isinstance(value, str):
        return []
    s = value.strip()
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return []
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    if not names:
        return []
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant,
             ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Load),
        ):
            return []
    return names


def check(
    rule: CandidateRule,
    pack,
    declared_params: set[str],
    metrics: set[str],
) -> tuple[bool, list[dict]]:
    """Return (executable, problems). Each problem is a dict the unresolved
    builder turns into an UnresolvedItem."""
    problems: list[dict] = []

    if not rule.applies_to_intents:
        problems.append({
            "type": "non_executable_language",
            "severity": "high",
            "issue": "rule has no applies_to_intents; the binder would skip it",
            "recommended_action": "add the intent(s) this rule governs",
        })

    for c in rule.conditions:
        field = str(c.field).strip()
        if not _SIMPLE_SYMBOL.match(field):
            problems.append({
                "type": "non_executable_language",
                "severity": "high",
                "issue": f"condition field {field!r} is not a simple symbol "
                         "(left-side arithmetic is not evaluable at runtime)",
                "recommended_action": "move arithmetic to a metric and test it as a simple symbol",
            })
            continue
        if resolve_symbol(field, pack, declared_params, metrics) is None:
            problems.append({
                "type": _kind(field, pack),
                "severity": "high",
                "issue": f"field {field!r} does not resolve to a column, request "
                         "parameter, metric, or caller attribute",
                "recommended_action": "map it in the domain pack, declare a template "
                                      "param, or define a metric",
            })

        # Right-hand arithmetic value: every identifier must resolve too, except
        # a single bare name that is just an enum literal (e.g. value: hold).
        names = _expr_names(c.value)
        unresolved_names = [
            n for n in names
            if resolve_symbol(n, pack, declared_params, metrics) is None
        ]
        if names and unresolved_names and not (len(names) == 1):
            problems.append({
                "type": "missing_metric",
                "severity": "high",
                "issue": f"value {c.value!r} references unresolvable symbol(s) "
                         f"{unresolved_names}",
                "recommended_action": "define the referenced metric(s)/param(s)",
            })

    return (len(problems) == 0, problems)


def _kind(field: str, pack) -> str:
    """Pick the most specific unresolved type for an unmappable left symbol."""
    if field.lower() in ("role", "caller_role"):
        return "unknown_role"
    return "unmappable_symbol"
