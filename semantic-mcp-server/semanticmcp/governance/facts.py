"""Facts stage — assemble the value namespace the rules evaluate against.

facts = precheck row (columns) ∪ request args ∪ caller context ∪ derived metrics.
Vocabulary was already reconciled at publish time, so this is pure value
collection — bare column names from the precheck SELECT line up with the rule
symbols by construction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from .context import Caller
from .rules import safe_eval


def build_facts(
    args: dict[str, Any],
    caller: Optional[Caller],
    row: Optional[dict[str, Any]],
    metrics: dict[str, str],
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    if row:
        for k, v in row.items():
            facts[k] = float(v) if isinstance(v, Decimal) else v
    for k, v in (args or {}).items():
        facts.setdefault(k, v)
    if caller:
        # Every configured identity attribute is addressable as caller.<attr>.
        for k, v in caller.attrs.items():
            facts[f"caller.{k}"] = v
    # Derived metrics (e.g. available_credit) — skipped when inputs are absent.
    for name, expr in (metrics or {}).items():
        value, missing = safe_eval(expr, facts)
        if not missing:
            facts[name] = value
    return facts
