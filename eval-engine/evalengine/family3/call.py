"""Call level: is this approved work at all?

catalog_membership | entitlement | version_conformance | side_effect_class.
Off-catalog (no membership) short-circuits the other three for that step -
checking entitlement/shape/effect against an intent that doesn't exist is
meaningless (Hard Rule 16: nothing to compare against).
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from .catalog import IntentCatalog

CHECK_MEMBERSHIP = "catalog_membership"
CHECK_ENTITLEMENT = "entitlement"
CHECK_VERSION = "version_conformance"
CHECK_SIDE_EFFECT = "side_effect_class"


def evaluate(session: Session, catalog: IntentCatalog, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        evidence = Evidence(span_ids=(step.span_id,), excerpt=step.tool_name)
        entry = catalog.intents.get(step.intent) if step.intent else None
        if entry is None:
            out.append(Verdict(
                check_id=CHECK_MEMBERSHIP, family="family3", status="violated", effect="block",
                session_id=session.session_id, evidence=evidence,
                detail=f"{step.tool_name} binds to no approved intent (intent={step.intent!r})",
            ))
            continue
        out.append(Verdict(
            check_id=CHECK_MEMBERSHIP, family="family3", status="satisfied", effect="allow",
            session_id=session.session_id, evidence=evidence,
            detail=f"{step.tool_name} binds to approved intent {entry.intent}",
        ))

        role_ok = not entry.allowed_roles or session.caller_role in entry.allowed_roles
        channel_ok = not entry.allowed_channels or session.channel in entry.allowed_channels
        if role_ok and channel_ok:
            out.append(Verdict(
                check_id=CHECK_ENTITLEMENT, family="family3", status="satisfied", effect="allow",
                session_id=session.session_id, evidence=evidence,
                detail=f"caller {session.caller_role}/{session.channel} entitled to {entry.intent}",
                source=catalog.source_for(entry),
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_ENTITLEMENT, family="family3", status="violated", effect="block",
                session_id=session.session_id, evidence=evidence,
                detail=(f"caller {session.caller_role}/{session.channel} not entitled to {entry.intent} "
                       f"(allowed roles={list(entry.allowed_roles)}, channels={list(entry.allowed_channels)})"),
                source=catalog.source_for(entry),
            ))

        unknown = sorted(set(step.args.keys()) - set(entry.params))
        if unknown:
            out.append(Verdict(
                check_id=CHECK_VERSION, family="family3", status="violated", effect="flag",
                session_id=session.session_id, evidence=evidence,
                detail=f"{entry.intent} call carries undeclared param(s) {unknown}",
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_VERSION, family="family3", status="satisfied", effect="allow",
                session_id=session.session_id, evidence=evidence,
                detail=f"{entry.intent} call params within declared schema",
            ))

        if step.side_effect:
            if entry.side_effect == "read" and step.side_effect == "write":
                out.append(Verdict(
                    check_id=CHECK_SIDE_EFFECT, family="family3", status="violated", effect="block",
                    session_id=session.session_id, evidence=evidence,
                    detail=f"{entry.intent} is approved read-only; call performed a write",
                    source=catalog.source_for(entry),
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_SIDE_EFFECT, family="family3", status="satisfied", effect="allow",
                    session_id=session.session_id, evidence=evidence,
                    detail=f"{entry.intent} side effect ({step.side_effect}) matches approval ({entry.side_effect})",
                    source=catalog.source_for(entry),
                ))
    return out
