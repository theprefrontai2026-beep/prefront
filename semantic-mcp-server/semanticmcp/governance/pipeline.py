"""Pipeline — run the governance stages for one tool call.

The server resolves the caller and fetches the precheck row (it owns the DB);
this orchestrates the policy-only stages: authz -> facts -> rules -> decide.
Each stage is swappable (see package docstring) — new concerns are inserted
here without touching the others.
"""

from __future__ import annotations

from typing import Any, Optional

from .context import Caller, Decision, GovernanceContext
from .decide import aggregate
from .facts import build_facts
from .rules import evaluate


def govern(
    *,
    intent: str,
    kind: str,
    args: dict[str, Any],
    caller: Optional[Caller],
    row: Optional[dict[str, Any]],
    bundle: Optional[dict],
    write_fields: Optional[set[str]] = None,
) -> GovernanceContext:
    ctx = GovernanceContext(intent=intent, kind=kind, args=args, caller=caller)

    # -- authz: caller role vs the intent's allowed_roles (from allow-rules) ----
    allowed_roles = ((bundle or {}).get("intents", {}).get(intent, {})
                     .get("allowed_roles") or [])
    if allowed_roles and caller is not None:
        if _norm(caller.role) not in {_norm(r) for r in allowed_roles}:
            ctx.decision = Decision(
                status="blocked",
                reasons=[f"role_not_permitted: role {caller.role!r} is not allowed "
                         f"to call {intent!r} (allowed: {allowed_roles})"],
            )
            return ctx

    # -- facts ------------------------------------------------------------------
    metrics = (bundle or {}).get("metrics", {})
    ctx.facts = build_facts(args, caller, row, metrics)

    # -- rules + decide -----------------------------------------------------------
    outcomes = evaluate(bundle, intent, ctx.facts) if bundle else []
    ctx.decision = aggregate(outcomes, kind, write_fields)
    return ctx


def _norm(v: str) -> str:
    return str(v).strip().lower().replace(" ", "_")
