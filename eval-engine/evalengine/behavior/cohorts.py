"""What differs BETWEEN groups of callers — the access-policy signal.

Per-tool profiles say who used an operation. Sequence groups say what a process
looks like. Neither answers the question an access policy actually encodes:
*what can this cohort do that another cannot?* A policy is precisely what makes
one group's behaviour differ from another's, so the differences are where it is
visible — and they are invisible in any single cohort's profile.

Three contrasts are computed, in descending order of how much they are worth:

1. FIELD GAPS. The same tool returning fewer fields to one cohort than to
   another is the strongest signal in the corpus, because it cannot be
   explained by what a cohort happened to need: they called the same operation
   and got less back. That is a field restriction, observed.
2. TOOL ABSENCE, ADJUSTED FOR OPPORTUNITY. A cohort that never called an
   operation may be forbidden, or may simply never have needed it, and those
   are indistinguishable without knowing how often it would have. So absence is
   scored PER TOOL, not per cohort: if other roles use a tool in a fraction p
   of their sessions, then n sessions with no use happens by chance with
   probability (1-p)^n. Small probability means the silence is unlikely to be
   luck; large means it says nothing.

   A flat session threshold was tried first and was wrong in a way worth
   recording: it told the model that a 31-session cohort had "ample traffic",
   and the model duly returned HIGH confidence on a boundary spanning
   13 unused tools. Whether 31 sessions is ample depends entirely on how often
   the tool is used at all — for a tool others reach in 3% of sessions, 31
   silent sessions is nothing; for one they reach in half, it is decisive.
3. EXCLUSIVE TOOLS. Operations only one cohort ever performs.

THE CEILING, stated because it cannot be engineered away: absence of evidence
is not evidence of prohibition. Everything here is a candidate boundary a
human must confirm, and a cohort's observed reach is never its permitted reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ch
from .profiles import _TOOL_PRED, _window, _split_list

# A cohort's silence about a tool counts as evidence only when it would have
# been unlikely by chance. 0.05 is the conventional line and is stated as a
# convention, not a discovery — a reviewer confirms boundaries, this only
# decides which are worth putting in front of them.
CHANCE_THRESHOLD = 0.05

# Below this, a cohort is too small to say anything about ANYTHING, whatever
# the per-tool arithmetic says — the sample is not a sample.
MIN_EXPOSURE = 5


@dataclass(frozen=True)
class Cohort:
    role: str
    sessions: int
    calls: int
    tools: tuple[dict[str, Any], ...]          # tool -> sessions/calls for this cohort
    # Contrasts against every OTHER cohort.
    exclusive_tools: tuple[str, ...]
    never_used: tuple[dict[str, Any], ...]     # used by others, never here
    field_gaps: tuple[dict[str, Any], ...]     # same tool, fewer fields returned
    has_exposure: bool                         # enough traffic for absence to mean anything


def cohort_contrasts(since: int = 7 * 86400, app: str = "") -> list[Cohort]:
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT user_role AS role, tool_name, session_id, attributes['app.columns'] AS cols
        FROM {ch.SPANS_T} {where} AND user_role != ''
    """, params)
    if not rows_:
        return []

    sessions: dict[str, set[str]] = {}
    calls: dict[str, int] = {}
    tool_sessions: dict[str, dict[str, set[str]]] = {}
    tool_calls: dict[str, dict[str, int]] = {}
    fields: dict[str, dict[str, set[str]]] = {}          # role -> tool -> field names
    for r in rows_:
        role, tool, sid = str(r["role"]), str(r["tool_name"]), str(r["session_id"])
        sessions.setdefault(role, set()).add(sid)
        calls[role] = calls.get(role, 0) + 1
        tool_sessions.setdefault(role, {}).setdefault(tool, set()).add(sid)
        tool_calls.setdefault(role, {})[tool] = tool_calls.setdefault(role, {}).get(tool, 0) + 1
        for f in _split_list(str(r["cols"] or "")):
            fields.setdefault(role, {}).setdefault(tool, set()).add(f)

    roles = sorted(sessions, key=lambda r: -len(sessions[r]))
    all_tools = {t for m in tool_sessions.values() for t in m}

    out: list[Cohort] = []
    for role in roles:
        mine = tool_sessions.get(role, {})
        others = {t for r2 in roles if r2 != role for t in tool_sessions.get(r2, {})}
        exposure = len(sessions[role])

        # Fields another cohort saw from the same tool that this one never did.
        gaps: list[dict[str, Any]] = []
        for tool in mine:
            seen = fields.get(role, {}).get(tool, set())
            elsewhere: set[str] = set()
            for r2 in roles:
                if r2 != role:
                    elsewhere |= fields.get(r2, {}).get(tool, set())
            missing = elsewhere - seen
            if missing and seen:
                gaps.append({"tool": tool, "withheld": sorted(missing),
                             "seen": sorted(seen),
                             "seen_by": sorted(r2 for r2 in roles if r2 != role
                                               and (fields.get(r2, {}).get(tool, set()) & missing))})

        # Per-tool opportunity. `p` is how often the OTHER cohorts reach this
        # tool in a session; (1-p)^n is the chance this cohort's silence is
        # luck. Everything is carried through so a reader can re-derive the
        # verdict rather than trust it.
        never = []
        for t in sorted(others - set(mine)):
            users = sorted(r2 for r2 in roles if t in tool_sessions.get(r2, {}))
            other_sessions = sum(len(sessions[r2]) for r2 in roles if r2 != role)
            with_tool = sum(len(tool_sessions.get(r2, {}).get(t, ())) for r2 in roles if r2 != role)
            p = (with_tool / other_sessions) if other_sessions else 0.0
            by_chance = (1.0 - p) ** exposure if p < 1 else 0.0
            never.append({
                "tool": t, "used_by": users, "this_cohort_sessions": exposure,
                "others_use_rate": round(p, 3),
                "silence_by_chance": round(by_chance, 4),
                # The only field a reader needs if they trust the arithmetic.
                "likely_boundary": bool(exposure >= MIN_EXPOSURE and by_chance < CHANCE_THRESHOLD),
            })

        out.append(Cohort(
            role=role, sessions=exposure, calls=calls.get(role, 0),
            tools=tuple(sorted(({"tool": t, "sessions": len(s), "calls": tool_calls[role].get(t, 0)}
                                for t, s in mine.items()),
                               key=lambda d: (-d["sessions"], d["tool"]))),
            exclusive_tools=tuple(sorted(set(mine) - others)),
            never_used=tuple(sorted(never, key=lambda d: (d["silence_by_chance"], d["tool"]))),
            field_gaps=tuple(sorted(gaps, key=lambda d: (-len(d["withheld"]), d["tool"]))),
            has_exposure=exposure >= MIN_EXPOSURE,
        ))
    return out
