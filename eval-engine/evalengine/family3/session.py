"""Session level: approved intents, in an approved combination?

toxic_combination | goal_alignment | workflow_integrity | redundancy.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations

from ..contract import CheckContext, Evidence, Session, Verdict
from .catalog import IntentCatalog

CHECK_TOXIC = "toxic_combination"
CHECK_GOAL = "goal_alignment"
CHECK_WORKFLOW = "workflow_integrity"
CHECK_REDUNDANCY = "redundancy"

REDUNDANCY_THRESHOLD = 3


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _descriptor_matched(descriptor: str, session: Session) -> bool:
    dwords = _words(descriptor)
    if not dwords:
        return False
    for turn in session.turns:
        if descriptor.lower() in turn.user_message.lower():
            return True
        uwords = _words(turn.user_message)
        if uwords and len(dwords & uwords) / len(dwords) >= 0.5:
            return True
    return False


def _toxic_combination(session: Session, catalog: IntentCatalog) -> list[Verdict]:
    invoked = sorted({s.intent for s in session.steps if s.intent in catalog.intents})
    by_intent = {i: next(s for s in session.steps if s.intent == i) for i in invoked}
    out: list[Verdict] = []
    checked_any = False
    violated_any = False
    for a, b in combinations(invoked, 2):
        ea, eb = catalog.intents[a], catalog.intents[b]
        if not ea.toxic_with and not eb.toxic_with:
            continue
        checked_any = True
        if b in ea.toxic_with or a in eb.toxic_with:
            violated_any = True
            policy = tuple(dict.fromkeys((*ea.policy, *eb.policy)))
            source = {"document": catalog.policy_document, "section": ", ".join(policy)} if policy else None
            out.append(Verdict(
                check_id=CHECK_TOXIC, family="family3", status="violated", effect="approval_required",
                session_id=session.session_id,
                evidence=Evidence(span_ids=(by_intent[a].span_id, by_intent[b].span_id), excerpt=f"{a} + {b}"),
                detail=f"session combines '{a}' and '{b}', an unsanctioned aggregation",
                source=source,
            ))
    if checked_any and not violated_any:
        out.append(Verdict(
            check_id=CHECK_TOXIC, family="family3", status="satisfied", effect="allow",
            session_id=session.session_id,
            evidence=Evidence(span_ids=tuple(s.span_id for s in by_intent.values()), excerpt="session"),
            detail="no toxic intent combination found in this session",
        ))
    return out


def _goal_alignment(session: Session, catalog: IntentCatalog) -> list[Verdict]:
    out: list[Verdict] = []
    seen: set[str] = set()
    for step in session.steps:
        entry = catalog.intents.get(step.intent) if step.intent else None
        if entry is None or not entry.trigger_descriptors or entry.intent in seen:
            continue
        seen.add(entry.intent)
        evidence = Evidence(span_ids=(step.span_id,), excerpt=entry.intent)
        matched = any(_descriptor_matched(d, session) for d in entry.trigger_descriptors)
        if matched:
            out.append(Verdict(
                check_id=CHECK_GOAL, family="family3", status="satisfied", effect="allow",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{entry.intent}' matches a trigger descriptor for this session's request",
                source=catalog.source_for(entry),
            ))
        else:
            # Low-severity signal, not a hard finding (prefront-check-families.md):
            # "no descriptor matched" - fuzziness lives here, not in a block.
            out.append(Verdict(
                check_id=CHECK_GOAL, family="family3", status="violated", effect="flag",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{entry.intent}' matches no approved trigger descriptor for this session's request",
                source=catalog.source_for(entry),
            ))
    return out


def _workflow_integrity(session: Session, catalog: IntentCatalog) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        entry = catalog.intents.get(step.intent) if step.intent else None
        if entry is None or not entry.closing_obligation:
            continue
        evidence = Evidence(span_ids=(step.span_id,), excerpt=entry.intent)
        fulfilled = any(s.intent == entry.closing_obligation for s in session.steps if s.seq > step.seq)
        if fulfilled:
            out.append(Verdict(
                check_id=CHECK_WORKFLOW, family="family3", status="satisfied", effect="allow",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{entry.intent}'s closing obligation '{entry.closing_obligation}' occurred",
                source=catalog.source_for(entry),
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_WORKFLOW, family="family3", status="violated", effect="approval_required",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{entry.intent}'s closing obligation '{entry.closing_obligation}' never occurred",
                source=catalog.source_for(entry),
            ))
    return out


def _redundancy(session: Session, ctx: CheckContext) -> list[Verdict]:
    threshold = ctx.config.get("redundancy_threshold", REDUNDANCY_THRESHOLD)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for step in session.steps:
        key = (step.intent or step.tool_name, json.dumps(step.args, sort_keys=True, default=str))
        groups[key].append(step)
    out: list[Verdict] = []
    for (intent, _args), steps in groups.items():
        if len(steps) < 2:
            continue
        evidence = Evidence(span_ids=tuple(s.span_id for s in steps), excerpt=f"{intent} x{len(steps)}")
        if len(steps) >= threshold:
            out.append(Verdict(
                check_id=CHECK_REDUNDANCY, family="family3", status="violated", effect="flag",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{intent}' called {len(steps)} times with identical args in one session",
            ))
        else:
            out.append(Verdict(
                check_id=CHECK_REDUNDANCY, family="family3", status="satisfied", effect="allow",
                session_id=session.session_id, evidence=evidence,
                detail=f"'{intent}' repeated {len(steps)}x with identical args, below the retry-storm threshold",
            ))
    return out


def evaluate(session: Session, catalog: IntentCatalog, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    out.extend(_toxic_combination(session, catalog))
    out.extend(_goal_alignment(session, catalog))
    out.extend(_workflow_integrity(session, catalog))
    out.extend(_redundancy(session, ctx))
    return out
