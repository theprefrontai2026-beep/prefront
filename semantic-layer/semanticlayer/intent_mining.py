"""Reverse-engineer a deployment's intents — and the policy behind them — from
observed behaviour.

`intent_learning_design.md` phase L2, the synthesis half. eval-engine's
`behavior/` package counts what the traces contain; this module turns those
counts into CANDIDATE intents and, for each, an LLM's reading of the business
policy that appears to be in force.

THE SPLIT THAT MATTERS: structure is counted, language is inferred.

Everything enforceable — tool name, params, side effect, returned fields,
observed callers and channels, the row-count ceiling, the caller-scoping
invariant — is copied from the aggregates verbatim and never passes through the
model. The LLM is asked for exactly two things it is good at and counting is
not: a NAME for the operation, and a plain-language statement of the RULE the
observed pattern implies. If the model hallucinates, a candidate gets a poor
name and a wrong policy sentence; it cannot invent a field, a role or a
threshold, because those are not its to produce.

WHY THE POLICY SENTENCE IS ALWAYS A HYPOTHESIS. You cannot learn "must never"
from observation: absence of evidence is not evidence of prohibition, and
frequency is not legitimacy. An agent that leaked for six months makes leaking
look like policy. So each candidate carries the Family 2 `contested` overlay
from the sessions supporting it, and every inferred policy is labelled by
confidence and by what it rests on — never presented as a clause. Nothing here
is auto-approved (`review_status="pending"`, like every candidate surface in
this repo).
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from .llm import LLMClient

# gpt-4o-mini by default: this is naming and summarising over a small
# structured digest, not reasoning over a long document, and it runs once per
# tool across a whole catalogue. Overridable — provider/model resolution lives
# in LLMClient.
DEFAULT_MINING_MODEL = "gpt-4o-mini"

# Support floors below which a pattern is an anecdote (design §7 q1).
# Deliberate defaults, not thresholds pretending to be science: a reviewer can
# lower them, but nothing should propose a rule off two calls unasked.
MIN_SESSIONS = 3
MIN_ROLE_SHARE = 0.05   # a caller under this share of sessions is flagged rare


class InferredPolicy(BaseModel):
    """What the model thinks the observed behaviour implies, and why."""
    statement: str = Field(description="the rule, as one sentence a reviewer could adopt")
    rationale: str = Field(description="the specific observation it is drawn from")
    confidence: str = Field(default="low", description="high | medium | low")
    caveats: list[str] = Field(default_factory=list)


class CandidateIntent(BaseModel):
    """A mined intent. Structural fields are COUNTED; the rest is inferred.

    Mirrors preflight.CandidateScenario's posture: pydantic-validated, never
    auto-approved, and dropped with a reason rather than coerced when the model
    returns something that does not fit.
    """
    # ── counted (never from the LLM) ──
    tool_name: str
    params: list[str] = Field(default_factory=list)
    side_effect: str = "read"
    fields: list[str] = Field(default_factory=list)
    observed_roles: list[dict] = Field(default_factory=list)
    observed_channels: list[dict] = Field(default_factory=list)
    expected_rows_p99: Optional[int] = None
    mandatory_filters: list[str] = Field(default_factory=list)
    closing_obligation: str = ""
    support_sessions: int = 0
    support_calls: int = 0
    example_sessions: list[str] = Field(default_factory=list)
    contested: list[dict] = Field(default_factory=list)
    # ── inferred (the LLM's two jobs) ──
    intent: str = ""
    description: str = ""
    inferred_policy: Optional[InferredPolicy] = None
    # ── review ──
    review_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


def _share(counts: list[dict], total: int) -> list[dict]:
    return [{**c, "share": round(c.get("sessions", 0) / total, 3) if total else 0.0} for c in counts]


def structural_candidate(profile: dict) -> CandidateIntent:
    """The counted half — everything enforceable, straight from the aggregate.

    No LLM and no network, so a deployment that declines the model still gets a
    usable (if unnamed) draft, and the deterministic half is unit-testable with
    nothing mocked.
    """
    sessions = int(profile.get("sessions") or 0)
    roles = _share(profile.get("roles") or [], sessions)
    channels = _share(profile.get("channels") or [], sessions)

    # The dominant side effect, not the union: a tool is a read or a write, and
    # a minority disagreement is worth flagging rather than averaging away.
    ses = profile.get("side_effects") or []
    side_effect = str(ses[0]["value"]) if ses else "read"

    # A filter counts as mandatory only in the exact shape Family 3 enforces.
    filters = [f"{inv['param']} = {inv['equals']}" for inv in (profile.get("caller_invariants") or [])]

    # An obligation must be near-universal to be one at all. 0.9 is a judgement
    # call, stated as such: below it this is a common sequence, not a duty.
    follow = profile.get("followed_by") or []
    obligation = next((f["tool"] for f in follow if float(f.get("rate") or 0) >= 0.9), "")

    warnings: list[str] = []
    if sessions < MIN_SESSIONS:
        warnings.append(f"thin evidence: {sessions} session(s) — below the {MIN_SESSIONS} floor")
    if len(ses) > 1:
        warnings.append("side effect is not consistent across calls: "
                        + ", ".join(f"{s['value']} ({s['calls']} calls)" for s in ses))
    for r in roles:
        if r["share"] < MIN_ROLE_SHARE:
            warnings.append(f"rare caller {r['value']!r} ({r['sessions']} of {sessions} sessions) — "
                            f"an outlier to narrow, not a role to bless")
    if profile.get("contested"):
        warnings.append("supporting sessions carry Family 2 integrity violations: "
                        + ", ".join(f"{c['check_id']} ({c['sessions']} sessions)" for c in profile["contested"]))
    if int(profile.get("error_calls") or 0):
        warnings.append(f"{profile['error_calls']} call(s) errored — the observed field list may be partial")

    return CandidateIntent(
        tool_name=str(profile.get("tool_name") or ""),
        params=[p["value"] for p in (profile.get("params") or [])],
        side_effect=side_effect,
        fields=[f["value"] for f in (profile.get("fields") or [])],
        observed_roles=roles,
        observed_channels=channels,
        expected_rows_p99=profile.get("row_count_p99"),
        mandatory_filters=filters,
        closing_obligation=obligation,
        support_sessions=sessions,
        support_calls=int(profile.get("calls") or 0),
        example_sessions=list(profile.get("example_sessions") or [])[:5],
        contested=list(profile.get("contested") or []),
        warnings=warnings,
    )


SYSTEM = """You are reverse-engineering the governance rules of a system whose \
policy document you cannot see. You are given a statistical profile of ONE \
tool, built by counting what actually happened in production traces.

Infer the business policy that appears to be in force. Two hard rules:

1. NEVER state a prohibition as fact. You are looking at what happened, not at \
what is permitted. A role absent from the data is not forbidden - it may simply \
not have used the tool yet. Phrase a restriction as "appears to be restricted \
to X" and put the alternative in caveats.
2. FREQUENCY IS NOT LEGITIMACY. If supporting sessions carry integrity \
violations, or a caller appears rarely, treat that as evidence the observed \
behaviour may itself be the problem. Say so. A pattern that looks like a leak \
is a suspected leak, not a rule to adopt.

Judge confidence on evidence: "high" needs a large, consistent, uncontested \
sample; "low" is right for a thin or contested one. Prefer low.

Return STRICT JSON only:
{"intent": "verb_noun snake_case operation name",
 "description": "one sentence on what the operation does",
 "policy": {"statement": "...", "rationale": "...",
            "confidence": "high|medium|low", "caveats": ["..."]}}"""


def render_prompt(candidate: CandidateIntent) -> str:
    """The digest the model reasons over.

    Counts and shares, never raw rows: argument VALUES and result rows are the
    customer's data and have no business in a prompt. The model needs the SHAPE
    of the behaviour, which names and frequencies convey entirely.
    """
    def lines(label: str, items: list[dict]) -> str:
        if not items:
            return f"{label}: none observed"
        return f"{label}: " + ", ".join(
            f"{i['value']} ({i['sessions']} sessions"
            + (f", {int(i['share'] * 100)}%" if "share" in i else "") + ")"
            for i in items)

    parts = [
        f"tool: {candidate.tool_name}",
        f"observed in {candidate.support_sessions} sessions / {candidate.support_calls} calls",
        f"side effect: {candidate.side_effect}",
        f"arguments: {', '.join(candidate.params) or 'none'}",
        f"fields returned: {', '.join(candidate.fields) or 'none declared'}",
        lines("callers", candidate.observed_roles),
        lines("channels", candidate.observed_channels),
        "rows returned (p99): " + (str(candidate.expected_rows_p99)
                                   if candidate.expected_rows_p99 is not None else "unknown"),
    ]
    if candidate.mandatory_filters:
        parts.append("argument always equalled a caller attribute: " + "; ".join(candidate.mandatory_filters))
    if candidate.closing_obligation:
        parts.append(f"almost always followed by: {candidate.closing_obligation}")
    if candidate.contested:
        parts.append("INTEGRITY VIOLATIONS on supporting sessions: " + ", ".join(
            f"{c['check_id']} in {c['sessions']} sessions" for c in candidate.contested))
    if candidate.warnings:
        parts.append("counted warnings: " + " | ".join(candidate.warnings))
    return "\n".join(parts)


def infer_policy(candidate: CandidateIntent, llm: LLMClient) -> tuple[CandidateIntent, Optional[str]]:
    """Ask the model to name the operation and state the rule it implies.

    A failure degrades to the STRUCTURAL candidate rather than to nothing: the
    counted fields are still correct and still worth reviewing, and discarding
    them because a naming call failed would be the wrong trade.
    """
    raw = llm.complete(SYSTEM, render_prompt(candidate))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return candidate, f"{candidate.tool_name}: LLM output was not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return candidate, f"{candidate.tool_name}: LLM returned {type(parsed).__name__}, expected an object"

    pol = parsed.get("policy") or {}
    try:
        inferred = InferredPolicy.model_validate(pol) if pol else None
    except Exception as e:  # noqa: BLE001 - malformed model output, not a bug here
        return candidate, f"{candidate.tool_name}: policy block invalid: {e}"

    # The model names the OPERATION; it does not get to rename the TOOL, which
    # is the join key to everything else.
    name = str(parsed.get("intent") or "").strip()
    return candidate.model_copy(update={
        "intent": name or candidate.tool_name,
        "description": str(parsed.get("description") or "").strip(),
        "inferred_policy": inferred,
    }), None


def mine_intents(profiles: list[dict], llm: Optional[LLMClient] = None,
                 min_sessions: int = 1) -> tuple[list[CandidateIntent], list[str]]:
    """Profiles in, candidate intents out. `llm=None` gives the counted half only.

    One model call PER TOOL, not one for the catalogue: a single call would let
    the model's reading of one tool contaminate another's, and one malformed
    answer would take the whole run down instead of one candidate.
    """
    candidates: list[CandidateIntent] = []
    rejected: list[str] = []
    for p in profiles:
        c = structural_candidate(p)
        if c.support_sessions < min_sessions:
            rejected.append(f"{c.tool_name}: {c.support_sessions} session(s), below min_sessions={min_sessions}")
            continue
        if llm is not None:
            c, err = infer_policy(c, llm)
            if err:
                rejected.append(err)
        candidates.append(c)
    return candidates, rejected
