"""Frequent tool SEQUENCES — the multi-call half of intent mining.

Per-tool profiles answer "what is this operation"; they cannot answer "what is
this PROCESS". A business intent is often several calls in order — fetch the
record, pull the report, score it, decide — and mining one tool at a time
reports that as five unrelated operations with no hint they belong together.
`profiles.followed_by` only ever said "B tends to follow A", which is a pairwise
shadow of the same thing.

This mines contiguous ordered runs of tool calls within a session, keeps the
ones frequent enough to be a pattern rather than a coincidence, and discards the
ones that are merely fragments of a longer pattern.

THREE DECISIONS THAT DETERMINE WHETHER THE OUTPUT IS READABLE:

1. CONSECUTIVE REPEATS COLLAPSE. A tool called twice in a row is a retry or a
   loop, not two steps of a process, and leaving them in makes (A, A) the most
   "frequent" pattern in any corpus with retries.
2. SUPPORT IS COUNTED OVER SESSIONS. A pattern occurring five times in one
   session is one piece of evidence, not five.
3. FRAGMENTS ARE DROPPED (closure). If A->B->C holds in every session where
   A->B does, then A->B is not a separate workflow — it is the start of one.
   Without this the output is dominated by every prefix and suffix of every real
   pattern, and a reviewer has to reconstruct which of thirty overlapping rows
   are the same finding. This is the single filter that turns a list into
   something a human can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ch
from .profiles import _TOOL_PRED, _window, _contested_by_tool


@dataclass(frozen=True)
class Workflow:
    steps: tuple[str, ...]
    sessions: int                 # sessions containing this run at least once
    occurrences: int              # total runs, across all sessions
    # Of the sessions that called steps[0] at all, the share that went on to
    # complete the whole run. This is the number that says whether a pattern is
    # THE way the first step is used, or one of several.
    coverage: float
    roles: tuple[dict[str, Any], ...]
    contested: tuple[dict[str, Any], ...]
    example_sessions: tuple[str, ...]


def _session_sequences(since: int, app: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(session -> collapsed tool sequence, session -> roles seen)."""
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT session_id, tool_name, user_role
        FROM {ch.SPANS_T} {where}
        ORDER BY session_id, start_time, span_id
    """, params)
    seqs: dict[str, list[str]] = {}
    roles: dict[str, list[str]] = {}
    for r in rows_:
        sid, tool = str(r["session_id"]), str(r["tool_name"])
        seq = seqs.setdefault(sid, [])
        if not seq or seq[-1] != tool:      # decision 1: collapse retries
            seq.append(tool)
        role = str(r.get("user_role") or "")
        if role and role not in roles.setdefault(sid, []):
            roles[sid].append(role)
    return seqs, roles


def frequent_workflows(since: int = 7 * 86400, app: str = "", min_sessions: int = 3,
                       min_len: int = 2, max_len: int = 6) -> list[Workflow]:
    """Contiguous tool runs seen in at least `min_sessions` sessions.

    `max_len` is a readability bound, not a correctness one: a 12-step run is
    almost always one session's whole transcript rather than a repeatable
    process, and reporting it invites a reviewer to bless a single trace.
    """
    seqs, roles = _session_sequences(since, app)
    if not seqs:
        return []

    # (pattern) -> sessions containing it, and how many times in total
    support: dict[tuple[str, ...], set[str]] = {}
    occurrences: dict[tuple[str, ...], int] = {}
    for sid, seq in seqs.items():
        for n in range(min_len, max_len + 1):
            for i in range(len(seq) - n + 1):
                pat = tuple(seq[i:i + n])
                support.setdefault(pat, set()).add(sid)      # decision 2
                occurrences[pat] = occurrences.get(pat, 0) + 1

    frequent = {p: s for p, s in support.items() if len(s) >= min_sessions}
    kept = _closed(frequent)

    # How often the first step happened at all, for coverage.
    starts: dict[str, set[str]] = {}
    for sid, seq in seqs.items():
        for t in set(seq):
            starts.setdefault(t, set()).add(sid)

    contested = _contested_by_tool(since, app)
    out: list[Workflow] = []
    for pat in kept:
        sids = frequent[pat]
        first_total = len(starts.get(pat[0]) or ())
        # A session's role set counts for this pattern only if the session ran
        # it; a role is attributed to the WORKFLOW, not to a step.
        role_count: dict[str, int] = {}
        for sid in sids:
            for role in roles.get(sid, ()):
                role_count[role] = role_count.get(role, 0) + 1
        # Any integrity violation on any step contests the whole run — which
        # step was at fault is exactly what is unknown without a policy.
        merged: dict[str, dict[str, Any]] = {}
        for step in set(pat):
            for c in contested.get(step, ()):
                m = merged.setdefault(c["check_id"], {"check_id": c["check_id"], "sessions": 0, "findings": 0})
                m["sessions"] = max(m["sessions"], c["sessions"])
                m["findings"] += c["findings"]
        out.append(Workflow(
            steps=pat, sessions=len(sids), occurrences=occurrences[pat],
            coverage=round(len(sids) / first_total, 3) if first_total else 0.0,
            roles=tuple(sorted(({"value": r, "sessions": n} for r, n in role_count.items()),
                               key=lambda d: (-d["sessions"], d["value"]))),
            contested=tuple(sorted(merged.values(), key=lambda d: (-d["sessions"], d["check_id"]))),
            example_sessions=tuple(sorted(sids)[:5]),
        ))
    # Support first, length second. Ranking by length put the longest and
    # RAREST runs on top — a six-step run seen 14 times outranked the four-step
    # core seen 60 — which is exactly backwards for a reviewer deciding what is
    # a real process.
    out.sort(key=lambda w: (-w.sessions, -len(w.steps), w.steps))
    return _dedupe_by_steps(out)


def _dedupe_by_steps(ws: list[Workflow]) -> list[Workflow]:
    """Collapse runs made of the same steps into their best-supported form.

    A session that loops — profile, report, score, profile, report, score —
    yields a distinct n-gram at every offset, so one process appears as a dozen
    rotations of itself. They are the same finding: same tools, same order, one
    window slid along. Keyed on the step SET so rotations and repeats collapse
    together, keeping the highest-support representative (the list arrives
    sorted, so that is the first one seen).

    Deliberately not merged into `_closed`: closure is about a pattern being a
    FRAGMENT of a longer one, this is about two patterns being the same loop
    photographed at different moments. Conflating them would drop genuinely
    different orderings of the same tools, which is a real distinction — one
    order may be a control and the other a bypass.
    """
    seen: set[frozenset[str]] = set()
    out: list[Workflow] = []
    for w in ws:
        key = frozenset(w.steps)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _closed(frequent: dict[tuple[str, ...], set[str]]) -> list[tuple[str, ...]]:
    """Drop a pattern that some longer pattern explains entirely (decision 3).

    "Explains" means: a superpattern that CONTAINS it contiguously and holds in
    just as many sessions. If A->B->C is in every session A->B is, then A->B
    tells the reviewer nothing A->B->C does not, and both rows in the output
    would be the same finding twice.

    Strictly-greater support is kept: if A->B appears in 40 sessions and
    A->B->C in 12, then A->B really is its own pattern — most of the time it
    does not continue — and collapsing them would hide that.
    """
    by_len: dict[int, list[tuple[str, ...]]] = {}
    for p in frequent:
        by_len.setdefault(len(p), []).append(p)

    kept: list[tuple[str, ...]] = []
    for pat, sids in frequent.items():
        n = len(pat)
        redundant = False
        for longer in by_len.get(n + 1, ()):
            # contiguous containment: the longer one extends this by one step
            if (longer[:n] == pat or longer[1:] == pat) and len(frequent[longer]) >= len(sids):
                redundant = True
                break
        if not redundant:
            kept.append(pat)
    return kept
