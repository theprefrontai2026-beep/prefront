"""Rules stage — load the published policy bundle and evaluate it against facts.

The bundle (policy.yaml) is produced by the semantic layer's publish-policy
binder: every rule ships with its vocabulary already resolved (``bindings`` +
per-condition ``value_kind``/``value_refs``), so evaluation here is plain
dictionary lookups + a safe-AST arithmetic evaluator. No name guessing, no LLM.

Future module: an external policy engine (e.g. OPA) replaces evaluate() behind
the same RuleOutcome contract.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Optional

import yaml

from .context import RuleOutcome


class PolicyRegistry:
    """Loads policy.yaml and live-reloads it when the file changes (mtime)."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._mtime: Optional[float] = None
        self.bundle: Optional[dict] = None
        self.refresh()

    def refresh(self) -> None:
        if not self.path:
            return
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self.bundle, self._mtime = None, None
            return
        if mtime != self._mtime:
            doc = yaml.safe_load(open(self.path, encoding="utf-8")) or {}
            self.bundle = doc.get("policy_bundle") or None
            self._mtime = mtime

    @property
    def active(self) -> bool:
        return self.bundle is not None


def evaluate(bundle: dict, intent: str, facts: dict[str, Any]) -> list[RuleOutcome]:
    """Evaluate every published rule that applies to this intent."""
    outcomes: list[RuleOutcome] = []
    for rule in bundle.get("rules", []):
        if intent not in (rule.get("intents") or []):
            continue
        effect = rule.get("effect") or {}
        out = RuleOutcome(
            rule_key=rule.get("rule_key", ""),
            decision=effect.get("decision", ""),
            reason=effect.get("message", ""),
            approver_role=effect.get("approver_role"),
            restricted_fields=effect.get("restricted_fields") or [],
            rule_type=rule.get("rule_type", ""),
            conditions=rule.get("conditions") or [],
            source=rule.get("source") or {},
        )
        fired = True
        for cond in rule.get("conditions") or []:
            ok, missing = _eval_condition(cond, facts)
            out.missing.extend(missing)
            if ok is not True:
                fired = False
                if not missing:        # cleanly false — rule simply doesn't apply
                    out.missing = []
                    break
        out.fired = fired and not out.missing
        outcomes.append(out)
    return outcomes


# --- condition evaluation -------------------------------------------------------


def _eval_condition(cond: dict, facts: dict[str, Any]):
    """Return (result, missing_symbols). result True/False; None when indeterminate."""
    field = str(cond.get("field", ""))
    op = str(cond.get("operator", ""))
    left = _lookup(field, facts)
    if left is _MISSING:
        return None, [field]

    value = cond.get("value")
    if cond.get("value_kind") == "expression" and isinstance(value, str):
        right, missing = safe_eval(value, facts)
        if missing:
            return None, missing
    else:
        right, _ = value, []

    try:
        return _compare(op, left, right), []
    except Exception:
        return None, [f"{field} (uncomparable)"]


_MISSING = object()


def _lookup(symbol: str, facts: dict[str, Any]):
    if symbol in facts:
        return facts[symbol]
    low = symbol.lower()
    for k, v in facts.items():
        if k.lower() == low:
            return v
    return _MISSING


_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant,
                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Load)


def safe_eval(expr: str, facts: dict[str, Any]):
    """Arithmetic over fact symbols only (+ - * /). Returns (value, missing[])."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return None, [expr]
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None, [expr]

    missing: list[str] = []

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            v = _lookup(node.id, facts)
            if v is _MISSING:
                missing.append(node.id)
                return 0
            return _num(v)
        if isinstance(node, ast.UnaryOp):
            return -ev(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            try:
                return a / b
            except ZeroDivisionError:
                missing.append(f"{expr} (division by zero)")
                return 0
        raise ValueError(node)

    value = ev(tree)
    return (None, missing) if missing else (value, [])


def _compare(op: str, left, right) -> bool:
    if op in ("in", "not_in"):
        items = right if isinstance(right, list) else [right]
        hit = _norm(left) in {_norm(x) for x in items}
        return hit if op == "in" else not hit
    if op in ("==", "!="):
        eq = _norm(left) == _norm(right)
        return eq if op == "==" else not eq
    l, r = _num(left), _num(right)
    return {">": l > r, "<": l < r, ">=": l >= r, "<=": l <= r}[op]


def _norm(v):
    """Case/format-insensitive comparison key (roles: 'Senior Rep' == senior_rep)."""
    if isinstance(v, str):
        return v.strip().lower().replace(" ", "_")
    return _num(v) if isinstance(v, (int, float)) else v


def _num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        raise ValueError(f"not numeric: {v!r}")
