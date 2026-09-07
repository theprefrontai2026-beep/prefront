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


# ── Multi-call intents ────────────────────────────────────────────────────
# An intent is not always one call. "Underwrite an application" is fetch the
# record, pull the report, score it, decide — and mining tool-by-tool reports
# that as four unrelated operations with nothing saying they belong together.
# These consume eval-engine's frequent tool RUNS and propose the process.


class WorkflowCandidate(BaseModel):
    """A candidate intent spanning several tool calls, in order."""
    # ── counted ──
    steps: list[str]
    sessions: int = 0
    occurrences: int = 0
    # Of the sessions that used the FIRST step at all, the share that went on
    # to complete the whole run. The number that separates "this is how that
    # tool is used" from "this is one of several things people do next".
    coverage: float = 0.0
    observed_roles: list[dict] = Field(default_factory=list)
    contested: list[dict] = Field(default_factory=list)
    example_sessions: list[str] = Field(default_factory=list)
    # ── inferred ──
    intent: str = ""
    description: str = ""
    inferred_policy: Optional[InferredPolicy] = None
    # ── review ──
    review_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


WORKFLOW_SYSTEM = """You are reverse-engineering a business PROCESS from \
production traces. You are given an ordered run of tool calls that recurs \
across many sessions, with how often it happens and who ran it.

Name the process and state the policy it appears to encode. The order is the \
evidence: a step that consistently precedes another is a candidate \
PRECONDITION, and a step that consistently follows one is a candidate \
OBLIGATION. Say which you think each is.

Three hard rules:

1. NEVER state a prohibition as fact. You see what happened, not what is \
permitted. Phrase restrictions as "appears to" and put alternatives in caveats.
2. FREQUENCY IS NOT LEGITIMACY. If the run carries integrity violations, or \
its coverage is low, the sequence may be a workaround or an exfiltration \
pattern rather than an approved process. Say so plainly.
3. LOW COVERAGE MEANS IT IS NOT THE NORM. If only a small share of sessions \
that started this way finished it, this is one path among many, not "the" \
process, and confidence should be low.

Return STRICT JSON only:
{"intent": "verb_noun snake_case process name",
 "description": "one sentence on what the process accomplishes",
 "policy": {"statement": "...", "rationale": "...",
            "confidence": "high|medium|low", "caveats": ["..."]}}"""


def structural_workflow(w: dict) -> WorkflowCandidate:
    """The counted half of a multi-call candidate."""
    sessions = int(w.get("sessions") or 0)
    roles = _share(w.get("roles") or [], sessions)
    coverage = float(w.get("coverage") or 0.0)

    warnings: list[str] = []
    if sessions < MIN_SESSIONS:
        warnings.append(f"thin evidence: {sessions} session(s) — below the {MIN_SESSIONS} floor")
    if coverage < 0.25:
        warnings.append(
            f"low coverage ({int(coverage * 100)}%): most sessions that used "
            f"{(w.get('steps') or ['the first step'])[0]!r} did NOT go on to complete this run, "
            f"so this is one path among several rather than the normal one")
    if w.get("contested"):
        warnings.append("steps in this run carry Family 2 integrity violations: "
                        + ", ".join(f"{c['check_id']} ({c['sessions']} sessions)" for c in w["contested"]))
    for r in roles:
        if r["share"] < MIN_ROLE_SHARE:
            warnings.append(f"rare runner {r['value']!r} ({r['sessions']} of {sessions} sessions) — "
                            f"an outlier to narrow, not a role to bless")

    return WorkflowCandidate(
        steps=list(w.get("steps") or []),
        sessions=sessions, occurrences=int(w.get("occurrences") or 0), coverage=coverage,
        observed_roles=roles, contested=list(w.get("contested") or []),
        example_sessions=list(w.get("example_sessions") or [])[:5],
        warnings=warnings,
    )


def render_workflow_prompt(c: WorkflowCandidate) -> str:
    parts = [
        "ordered tool run: " + " -> ".join(c.steps),
        f"seen in {c.sessions} sessions ({c.occurrences} occurrences)",
        f"coverage: {int(c.coverage * 100)}% of sessions that used {c.steps[0]!r} completed this run"
        if c.steps else "coverage: unknown",
        "runners: " + (", ".join(
            f"{r['value']} ({r['sessions']} sessions, {int(r['share'] * 100)}%)" for r in c.observed_roles)
            or "none observed"),
    ]
    if c.contested:
        parts.append("INTEGRITY VIOLATIONS on steps of this run: " + ", ".join(
            f"{x['check_id']} in {x['sessions']} sessions" for x in c.contested))
    if c.warnings:
        parts.append("counted warnings: " + " | ".join(c.warnings))
    return "\n".join(parts)


def infer_workflow_policy(c: WorkflowCandidate, llm: LLMClient) -> tuple[WorkflowCandidate, Optional[str]]:
    """Same contract as infer_policy: degrade to the counted half on failure."""
    label = " -> ".join(c.steps)
    raw = llm.complete(WORKFLOW_SYSTEM, render_workflow_prompt(c))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return c, f"{label}: LLM output was not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return c, f"{label}: LLM returned {type(parsed).__name__}, expected an object"
    pol = parsed.get("policy") or {}
    try:
        inferred = InferredPolicy.model_validate(pol) if pol else None
    except Exception as e:  # noqa: BLE001
        return c, f"{label}: policy block invalid: {e}"
    return c.model_copy(update={
        "intent": str(parsed.get("intent") or "").strip() or "_".join(c.steps[:2]),
        "description": str(parsed.get("description") or "").strip(),
        "inferred_policy": inferred,
    }), None


def mine_workflows(workflows: list[dict], llm: Optional[LLMClient] = None,
                   min_sessions: int = 3, limit: int = 12) -> tuple[list[WorkflowCandidate], list[str]]:
    """Runs in, process candidates out.

    `limit` bounds the LLM spend and, more importantly, the reading: a mined
    corpus yields dozens of overlapping runs, and a reviewer handed all of them
    reviews none. The aggregate arrives ranked by support, so the cap keeps the
    best-evidenced ones.
    """
    candidates: list[WorkflowCandidate] = []
    rejected: list[str] = []
    for w in workflows[:limit]:
        c = structural_workflow(w)
        if c.sessions < min_sessions:
            rejected.append(f"{' -> '.join(c.steps)}: {c.sessions} session(s), below min_sessions={min_sessions}")
            continue
        if llm is not None:
            c, err = infer_workflow_policy(c, llm)
            if err:
                rejected.append(err)
        candidates.append(c)
    return candidates, rejected


# ── One intent, several observed shapes ───────────────────────────────────
# A business intent rarely has a single shape: "assess an applicant" appears as
# find->profile, profile->report, and the full four-step run, depending on what
# the agent already had. Summarising each variant separately produces several
# candidates with near-identical policies, and a reviewer reads the same
# operation repeatedly without ever seeing that it is one. These take the whole
# GROUP in a single model call.


class GroupedIntent(BaseModel):
    """One candidate intent, summarised from several observed run shapes."""
    # ── counted ──
    core_steps: list[str] = Field(default_factory=list)      # in EVERY variant
    optional_steps: list[str] = Field(default_factory=list)  # in some
    variants: list[dict] = Field(default_factory=list)
    sessions: int = 0
    observed_roles: list[dict] = Field(default_factory=list)
    contested: list[dict] = Field(default_factory=list)
    example_sessions: list[str] = Field(default_factory=list)
    # ── inferred ──
    intent: str = ""
    description: str = ""
    inferred_policy: Optional[InferredPolicy] = None
    # ── review ──
    review_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


GROUP_SYSTEM = """You are reverse-engineering one business intent from several \
observed variants of it. Each variant is an ordered run of tool calls that \
recurred across production sessions; they differ because an agent sometimes \
already had part of the data, or went further.

Summarise them as ONE operation. You are told which steps appear in EVERY \
variant (the core) and which appear in only some (optional) - that split is \
counted, not your judgement, so do not contradict it.

What to produce:
- a name for the operation the variants share;
- one sentence on what it accomplishes;
- the policy it appears to encode. Core steps that always precede others are \
candidate PRECONDITIONS. Optional steps are candidate EXTENSIONS, not \
requirements. If a variant reaches something the others do not - an export, a \
write, a decision - say whether it looks like part of the same operation or a \
DIFFERENT one that happens to share a prefix.

Three hard rules:
1. NEVER state a prohibition as fact - you see what happened, not what is \
permitted. Phrase restrictions as "appears to" and put alternatives in caveats.
2. FREQUENCY IS NOT LEGITIMACY. Integrity violations on the run, or a rare \
runner, may mean the pattern itself is the problem. Say so.
3. If the variants do not look like one operation, SAY THAT in the caveats \
rather than inventing a name that covers them all.

Return STRICT JSON only:
{"intent": "verb_noun snake_case name",
 "description": "one sentence",
 "policy": {"statement": "...", "rationale": "...",
            "confidence": "high|medium|low", "caveats": ["..."]}}"""


def structural_group(g: dict) -> GroupedIntent:
    """The counted half of a grouped candidate."""
    sessions = int(g.get("sessions") or 0)
    roles = _share(g.get("roles") or [], sessions)
    variants = list(g.get("variants") or [])

    warnings: list[str] = []
    if sessions < MIN_SESSIONS:
        warnings.append(f"thin evidence: {sessions} session(s) — below the {MIN_SESSIONS} floor")
    if not g.get("core_steps") and len(variants) > 1:
        # Should not happen with complete linkage, and is worth shouting about
        # if it ever does: a group with no shared step is not one operation.
        warnings.append("no step is common to every variant — these may not be one operation")
    best = max((float(v.get("coverage") or 0.0) for v in variants), default=0.0)
    if best < 0.25:
        warnings.append(f"low coverage ({int(best * 100)}%): even the strongest variant is one "
                        f"path among several, not the normal one")
    if g.get("contested"):
        warnings.append("steps in these runs carry Family 2 integrity violations: "
                        + ", ".join(f"{c['check_id']} ({c['sessions']} sessions)" for c in g["contested"]))
    for r in roles:
        if r["share"] < MIN_ROLE_SHARE:
            warnings.append(f"rare runner {r['value']!r} — an outlier to narrow, not a role to bless")

    return GroupedIntent(
        core_steps=list(g.get("core_steps") or []),
        optional_steps=list(g.get("optional_steps") or []),
        variants=variants, sessions=sessions, observed_roles=roles,
        contested=list(g.get("contested") or []),
        example_sessions=list(g.get("example_sessions") or [])[:5],
        warnings=warnings,
    )


def render_group_prompt(c: GroupedIntent) -> str:
    lines = [
        "core steps (in EVERY variant): " + (" -> ".join(c.core_steps) or "none"),
        "optional steps (in some): " + (", ".join(c.optional_steps) or "none"),
        f"seen in up to {c.sessions} sessions",
        "runners: " + (", ".join(
            f"{r['value']} ({int(r['share'] * 100)}%)" for r in c.observed_roles) or "none observed"),
        f"{len(c.variants)} observed variant(s):",
    ]
    for v in c.variants[:8]:
        lines.append(f"  - {' -> '.join(v.get('steps') or [])}"
                     f"  ({v.get('sessions')} sessions, coverage {int(float(v.get('coverage') or 0) * 100)}%)")
    if len(c.variants) > 8:
        lines.append(f"  - ...and {len(c.variants) - 8} more")
    if c.contested:
        lines.append("INTEGRITY VIOLATIONS on these runs: " + ", ".join(
            f"{x['check_id']} in {x['sessions']} sessions" for x in c.contested))
    if c.warnings:
        lines.append("counted warnings: " + " | ".join(c.warnings))
    return "\n".join(lines)


def infer_group_policy(c: GroupedIntent, llm: LLMClient) -> tuple[GroupedIntent, Optional[str]]:
    label = " -> ".join(c.core_steps) or "group"
    raw = llm.complete(GROUP_SYSTEM, render_group_prompt(c))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return c, f"{label}: LLM output was not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return c, f"{label}: LLM returned {type(parsed).__name__}, expected an object"
    pol = parsed.get("policy") or {}
    try:
        inferred = InferredPolicy.model_validate(pol) if pol else None
    except Exception as e:  # noqa: BLE001
        return c, f"{label}: policy block invalid: {e}"
    return c.model_copy(update={
        "intent": str(parsed.get("intent") or "").strip() or label,
        "description": str(parsed.get("description") or "").strip(),
        "inferred_policy": inferred,
    }), None


def mine_intent_groups(groups: list[dict], llm: Optional[LLMClient] = None,
                       min_sessions: int = 3, limit: int = 12) -> tuple[list[GroupedIntent], list[str]]:
    """Grouped runs in, summarised intents out — ONE model call per group.

    This is the whole point of grouping: N variants of one operation cost one
    call and yield one candidate, instead of N calls yielding N candidates with
    near-identical policies that a reviewer has to recognise as duplicates.
    """
    out: list[GroupedIntent] = []
    rejected: list[str] = []
    for g in groups[:limit]:
        c = structural_group(g)
        if c.sessions < min_sessions:
            rejected.append(f"{' -> '.join(c.core_steps)}: {c.sessions} session(s), below min_sessions={min_sessions}")
            continue
        if llm is not None:
            c, err = infer_group_policy(c, llm)
            if err:
                rejected.append(err)
        out.append(c)
    return out, rejected


# ── Access policy, from what differs between cohorts ──────────────────────
# The strongest policy signal in a corpus is not what any one group does, it is
# what one group does that another never does. A policy is exactly what makes
# those differ, and it is invisible in a single cohort's profile.


class CohortPolicy(BaseModel):
    """The access boundary a cohort appears to sit behind."""
    # ── counted ──
    role: str
    sessions: int = 0
    calls: int = 0
    tools: list[dict] = Field(default_factory=list)
    exclusive_tools: list[str] = Field(default_factory=list)
    never_used: list[dict] = Field(default_factory=list)
    field_gaps: list[dict] = Field(default_factory=list)
    has_exposure: bool = False
    # ── inferred ──
    inferred_policy: Optional[InferredPolicy] = None
    # ── review ──
    review_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


COHORT_SYSTEM = """You are inferring an ACCESS POLICY from behaviour, for a \
system whose policy document you cannot see. You are given one group of callers \
(a role), the operations it performs, the operations only it performs, the \
operations other roles perform that it never does, and any fields the same tool \
returned to others but never to it.

State the access boundary this role appears to sit behind.

Weigh the evidence in this order, and say which you are using:
- A FIELD GAP is the strongest signal: the role called the same operation and \
got less back. That cannot be explained by what it happened to need.
- An operation only this role performs suggests a capability reserved to it.
- NEVER HAVING USED something is the weakest signal, and its strength has \
already been computed for you PER TOOL. For each unused operation you are told \
how often other roles reach it and how likely this role's silence is by chance. \
Ones marked a likely boundary are worth reporting. Ones marked inconclusive are \
NOT evidence - this role may simply not have had the occasion, and you must not \
build a restriction on them. Do not treat a long list of inconclusive \
operations as though its length were evidence; it is not.

Three hard rules:
1. ABSENCE OF EVIDENCE IS NOT EVIDENCE OF PROHIBITION. Never write that a role \
"cannot" or "is not permitted to" do something. Write "appears not to" or \
"was never observed to", and put the alternative - that it simply never needed \
to - in caveats.
2. OBSERVED REACH IS NOT PERMITTED REACH. The operations this role DID perform \
are not thereby approved; some may be exactly what a reviewer needs to remove.
3. If the evidence is too thin to say anything, say that, with low confidence. \
An honest "not enough traffic to tell" is more useful than a confident guess.

Return STRICT JSON only:
{"policy": {"statement": "...", "rationale": "...",
            "confidence": "high|medium|low", "caveats": ["..."]}}"""


def structural_cohort(c: dict) -> CohortPolicy:
    sessions = int(c.get("sessions") or 0)
    warnings: list[str] = []
    if not c.get("has_exposure"):
        warnings.append(
            f"only {sessions} session(s): too little traffic for 'never used' to mean anything — "
            f"absence here is silence, not a boundary")
    if not c.get("field_gaps"):
        # Worth saying out loud rather than rendering an empty section: on an
        # UNGOVERNED deployment nothing is withheld from anyone, and that is
        # itself the finding.
        warnings.append("no field gaps: every cohort that called a tool saw the same fields, "
                        "so no field-level restriction is being enforced anywhere in this corpus")
    return CohortPolicy(
        role=str(c.get("role") or ""), sessions=sessions, calls=int(c.get("calls") or 0),
        tools=list(c.get("tools") or []), exclusive_tools=list(c.get("exclusive_tools") or []),
        never_used=list(c.get("never_used") or []), field_gaps=list(c.get("field_gaps") or []),
        has_exposure=bool(c.get("has_exposure")), warnings=warnings,
    )


def render_cohort_prompt(c: CohortPolicy) -> str:
    lines = [
        f"role: {c.role}",
        f"traffic: {c.sessions} sessions, {c.calls} tool calls",
        "operations performed: " + (", ".join(
            f"{t['tool']} ({t['sessions']})" for t in c.tools) or "none"),
        "performed ONLY by this role: " + (", ".join(c.exclusive_tools) or "none"),
    ]
    # Split by whether the silence is statistically meaningful, rather than
    # handing over one list and a session count for the model to weigh — it
    # weighed it wrongly, returning HIGH confidence for a 31-session cohort.
    strong = [x for x in c.never_used if x.get("likely_boundary")]
    weak = [x for x in c.never_used if not x.get("likely_boundary")]
    if strong:
        lines.append("never performed by this role, and the silence is UNLIKELY BY CHANCE "
                     "(a likely boundary): " + "; ".join(
                         f"{x['tool']} — others reach it in {int(float(x.get('others_use_rate') or 0) * 100)}% "
                         f"of their sessions, this role had {x['this_cohort_sessions']} sessions "
                         f"(p={x.get('silence_by_chance')})" for x in strong))
    if weak:
        lines.append("never performed, but INCONCLUSIVE — too rare for this role's traffic to "
                     "say anything, do not infer a restriction from these: "
                     + ", ".join(x["tool"] for x in weak))
    if c.field_gaps:
        lines.append("FIELDS WITHHELD from this role by the same tool: " + "; ".join(
            f"{g['tool']}: {', '.join(g['withheld'])} (seen by {', '.join(g['seen_by'])})"
            for g in c.field_gaps))
    else:
        lines.append("no fields were withheld from this role that others saw")
    if c.warnings:
        lines.append("counted warnings: " + " | ".join(c.warnings))
    return "\n".join(lines)


def infer_cohort_policy(c: CohortPolicy, llm: LLMClient) -> tuple[CohortPolicy, Optional[str]]:
    raw = llm.complete(COHORT_SYSTEM, render_cohort_prompt(c))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return c, f"{c.role}: LLM output was not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return c, f"{c.role}: LLM returned {type(parsed).__name__}, expected an object"
    pol = parsed.get("policy") or {}
    try:
        inferred = InferredPolicy.model_validate(pol) if pol else None
    except Exception as e:  # noqa: BLE001
        return c, f"{c.role}: policy block invalid: {e}"
    return c.model_copy(update={"inferred_policy": inferred}), None


def mine_cohort_policies(cohorts: list[dict], llm: Optional[LLMClient] = None,
                         ) -> tuple[list[CohortPolicy], list[str]]:
    """One access-boundary candidate per cohort.

    Every cohort is returned, including under-exposed ones — a role with three
    sessions is part of the picture and hiding it would hide that the corpus
    cannot yet say anything about it. Its thinness rides along as a warning
    and is put in front of the model rather than filtered out.
    """
    out: list[CohortPolicy] = []
    rejected: list[str] = []
    for c in cohorts:
        p = structural_cohort(c)
        if llm is not None:
            p, err = infer_cohort_policy(p, llm)
            if err:
                rejected.append(err)
        out.append(p)
    return out, rejected


# ── The policy behind an operation, from how it was actually performed ────
# Episode shapes grouped by their CLOSING action. This is the sharpest input
# available for reverse-engineering a rule, because it puts every way an
# operation was performed side by side: the times it was preceded by evidence
# and the times it was not. A rate over n-grams cannot express that; "196 times
# with nothing first, 30 times after pulling a credit report" can.


class OperationPolicy(BaseModel):
    """One side-effecting operation, and the rule its usage implies."""
    # ── counted ──
    operation: str
    total_episodes: int = 0
    # Every observed way of reaching it, most frequent first.
    paths: list[dict] = Field(default_factory=list)
    bare_episodes: int = 0            # performed with nothing preceding it
    observed_roles: list[dict] = Field(default_factory=list)
    subject_args: list[str] = Field(default_factory=list)
    example_sessions: list[str] = Field(default_factory=list)
    # ── inferred ──
    intent: str = ""
    description: str = ""
    inferred_policy: Optional[InferredPolicy] = None
    # ── review ──
    review_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)

    @property
    def bare_share(self) -> float:
        return round(self.bare_episodes / self.total_episodes, 3) if self.total_episodes else 0.0


OPERATION_SYSTEM = """You are reverse-engineering the business rule governing ONE \
operation, from how it was actually performed in production.

You are given a side-effecting operation and every observed path to it: the \
sequence of calls that preceded it each time, with counts. Some paths gather \
evidence first; some perform the operation with nothing before it at all.

State the rule that appears to govern it. The comparison between paths is your \
evidence:
- Steps that appear before the operation in MANY paths are candidate \
PRECONDITIONS - things the business appears to require before this act.
- If the operation is frequently performed with NOTHING first, say so plainly. \
That is either a rule being bypassed or evidence there is no such rule, and \
you should say which you think it is and why. Do not smooth it over.
- A path taken once is an anomaly, not a rule.

Three hard rules:
1. NEVER state a prohibition as fact. Phrase as "appears to require" and put \
alternatives in caveats.
2. FREQUENCY IS NOT LEGITIMACY. The most common path may be the wrong one. If \
the bare path dominates, the honest reading may be that a control is missing \
entirely - say that rather than concluding no precondition exists.
3. Distinguish "the evidence shows a rule" from "the evidence shows a habit". \
A precondition present in 95% of paths is a candidate rule; one in 40% is a \
common practice at best.

Return STRICT JSON only:
{"intent": "verb_noun snake_case name for the operation",
 "description": "one sentence on what it does",
 "policy": {"statement": "...", "rationale": "...",
            "confidence": "high|medium|low", "caveats": ["..."]}}"""


def operations_from_shapes(shapes: list[dict]) -> list[OperationPolicy]:
    """Group episode shapes by their closing action.

    Only shapes that CLOSE on a side effect: a read leaves nothing behind for a
    rule to be about, and the interesting question — what must be true before
    this is allowed to happen — only arises for an act that changes something.
    """
    by_op: dict[str, dict[str, Any]] = {}
    for s in shapes:
        op = str(s.get("closed_by") or "")
        if not op:
            continue
        slot = by_op.setdefault(op, {"paths": [], "episodes": 0, "bare": 0,
                                     "roles": {}, "subjects": set(), "examples": []})
        before = list(s.get("before_effect") or [])
        n = int(s.get("episodes") or 0)
        slot["paths"].append({"before": before, "episodes": n})
        slot["episodes"] += n
        if not before:
            slot["bare"] += n
        for r in s.get("roles") or []:
            slot["roles"][r["value"]] = slot["roles"].get(r["value"], 0) + int(r.get("episodes") or 0)
        slot["subjects"].update(s.get("subject_args") or [])
        for e in s.get("example_sessions") or []:
            if len(slot["examples"]) < 5:
                slot["examples"].append(e)

    out: list[OperationPolicy] = []
    for op, v in by_op.items():
        paths = sorted(v["paths"], key=lambda p: -p["episodes"])
        total = v["episodes"]
        roles = _share([{"value": r, "sessions": n} for r, n in v["roles"].items()], total)
        warnings: list[str] = []
        bare_share = (v["bare"] / total) if total else 0.0
        if bare_share > 0.5:
            warnings.append(
                f"{int(bare_share * 100)}% of the time this operation was performed with NOTHING "
                f"preceding it — either no precondition is required, or one is being bypassed "
                f"routinely, and the traces alone cannot tell you which")
        if len(paths) > 1 and paths[0]["episodes"] < 0.5 * total:
            warnings.append("no dominant path: the operation is reached many different ways, "
                            "which is weak ground for calling any of them required")
        out.append(OperationPolicy(
            operation=op, total_episodes=total, paths=paths[:8], bare_episodes=v["bare"],
            observed_roles=roles, subject_args=sorted(v["subjects"]),
            example_sessions=v["examples"], warnings=warnings,
        ))
    out.sort(key=lambda o: -o.total_episodes)
    return out


def render_operation_prompt(o: OperationPolicy) -> str:
    lines = [
        f"operation: {o.operation}  (a side-effecting act)",
        f"performed {o.total_episodes} times",
        "subject identified by: " + (", ".join(o.subject_args) or "unknown"),
        "performed by: " + (", ".join(
            f"{r['value']} ({int(r['share'] * 100)}%)" for r in o.observed_roles) or "unknown"),
        "observed paths to it:",
    ]
    for p in o.paths:
        before = " -> ".join(p["before"]) if p["before"] else "(nothing preceded it)"
        share = int(p["episodes"] / o.total_episodes * 100) if o.total_episodes else 0
        lines.append(f"  - {before}   [{p['episodes']} times, {share}%]")
    if o.warnings:
        lines.append("counted warnings: " + " | ".join(o.warnings))
    return "\n".join(lines)


def infer_operation_policy(o: OperationPolicy, llm: LLMClient) -> tuple[OperationPolicy, Optional[str]]:
    raw = llm.complete(OPERATION_SYSTEM, render_operation_prompt(o))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return o, f"{o.operation}: LLM output was not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return o, f"{o.operation}: LLM returned {type(parsed).__name__}, expected an object"
    pol = parsed.get("policy") or {}
    try:
        inferred = InferredPolicy.model_validate(pol) if pol else None
    except Exception as e:  # noqa: BLE001
        return o, f"{o.operation}: policy block invalid: {e}"
    return o.model_copy(update={
        "intent": str(parsed.get("intent") or "").strip() or o.operation,
        "description": str(parsed.get("description") or "").strip(),
        "inferred_policy": inferred,
    }), None


def mine_operation_policies(shapes: list[dict], llm: Optional[LLMClient] = None,
                            limit: int = 10) -> tuple[list[OperationPolicy], list[str]]:
    ops = operations_from_shapes(shapes)
    out: list[OperationPolicy] = []
    rejected: list[str] = []
    for o in ops[:limit]:
        if llm is not None:
            o, err = infer_operation_policy(o, llm)
            if err:
                rejected.append(err)
        out.append(o)
    return out, rejected
