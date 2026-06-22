"""Decide stage — aggregate rule outcomes into one governance decision.

Precedence: block > approval_required > allow.

Field-restriction nuance: a fired rule carrying ``restricted_fields`` means
"this caller may not touch these fields". On a READ intent that is enforced as
masking (the read proceeds, the fields are redacted); on a WRITE/precheck intent
it blocks ONLY when the write actually touches a restricted field — creating an
order does not "alter credit_limit", so the order proceeds while a write that
sets credit_limit would be blocked.

Fail-safe drift handling: a gating rule (block/approval) that is INDETERMINATE
(a symbol missing from facts — schema drifted since publish) degrades the call
to approval_required rather than silently allowing it.
"""

from __future__ import annotations

from .context import Decision, RuleOutcome


def aggregate(
    outcomes: list[RuleOutcome], kind: str, write_fields: set[str] | None = None
) -> Decision:
    d = Decision(status="allowed", outcomes=outcomes)
    touched = {f.lower() for f in (write_fields or set())}

    for o in outcomes:
        if o.fired and (o.restricted_fields or o.decision == "mask"):
            restricted = {f.split(".")[-1].lower() for f in o.restricted_fields}
            if kind == "read" or o.decision == "mask":
                for f in o.restricted_fields:
                    if f not in d.mask_fields:
                        d.mask_fields.append(f)
                continue
            if not (restricted & touched):
                continue  # write doesn't touch any restricted field — rule is moot
            d.status = "blocked"
            d.reasons.append(
                f"{o.rule_key}: write touches restricted field(s) "
                f"{sorted(restricted & touched)} — {o.reason or 'not permitted'}"
            )
            continue

        if o.fired and o.decision == "block":
            d.status = "blocked"
            d.reasons.append(f"{o.rule_key}: {o.reason or 'blocked by policy'}")
        elif o.fired and o.decision == "approval_required":
            if d.status != "blocked":
                d.status = "approval_required"
            d.reasons.append(f"{o.rule_key}: {o.reason or 'approval required'}")
            if o.approver_role and o.approver_role not in d.approver_roles:
                d.approver_roles.append(o.approver_role)
        elif o.indeterminate and o.decision in ("block", "approval_required"):
            # Drift can gate a call, never bypass a control.
            if d.status == "allowed":
                d.status = "approval_required"
            d.reasons.append(
                f"{o.rule_key}: indeterminate (missing: {', '.join(o.missing)}) — fail-safe"
            )
    return d
