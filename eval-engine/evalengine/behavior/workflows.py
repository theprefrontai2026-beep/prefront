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


@dataclass(frozen=True)
class WorkflowGroup:
    """Several observed runs that look like variants of ONE intent.

    Mining returns runs, but a business intent rarely has exactly one shape:
    "assess an applicant" appears as find->profile, profile->report,
    profile->report->score and find->profile->report->score, depending on what
    the agent already had. Reported separately those are four candidates with
    four near-identical policies, and a reviewer reads the same thing four
    times without ever seeing that it is one operation.

    Grouping is DETERMINISTIC and lives here rather than in synthesis, because
    "these runs share most of their steps" is counting. What the group MEANS is
    the model's job, and it gets the whole group in one call instead of one
    call per variant.
    """
    variants: tuple[Workflow, ...]
    sessions: int                     # union across variants, not a sum
    # Present in EVERY variant vs only some. Counted, never inferred: this is
    # the group's backbone, and it is what a reviewer would turn into a
    # precondition, so it must not depend on the model's reading.
    core_steps: tuple[str, ...]
    optional_steps: tuple[str, ...]
    roles: tuple[dict[str, Any], ...]
    contested: tuple[dict[str, Any], ...]
    example_sessions: tuple[str, ...]

    @property
    def longest(self) -> tuple[str, ...]:
        """The fullest observed shape — the best single label for the group."""
        return max((v.steps for v in self.variants), key=len)


def _similar(a: tuple[str, ...], b: tuple[str, ...], min_overlap: float) -> bool:
    """Two runs are variants of one intent if they overlap enough in steps.

    Jaccard on the step SETS, plus an explicit containment rule: a run wholly
    contained in a longer one is always a variant of it, however much the
    lengths differ. Without that, a two-step run and the six-step run that
    contains it score low on Jaccard and split into separate groups — which is
    exactly the case this exists to merge.
    """
    sa, sb = set(a), set(b)
    if sa <= sb or sb <= sa:
        return True
    return len(sa & sb) / len(sa | sb) >= min_overlap


def group_workflows(since: int = 7 * 86400, app: str = "", min_sessions: int = 3,
                    max_len: int = 6, min_overlap: float = 0.6) -> list[WorkflowGroup]:
    """Mine runs, then merge the ones that are variants of the same intent.

    COMPLETE linkage: a run joins a group only if it is similar to EVERY
    member. Single linkage (similar to ANY member) was tried first and failed
    outright on real data — A resembles B, B resembles C, C resembles D, and
    the whole corpus chains into one group. It produced a single 31-variant
    "intent" spanning applicant lookup, quoting AND loan decisions, with NO
    step common to all of it. A group with no backbone is not a summary of
    anything; it is the corpus with a label on it.

    The cost is over-splitting, which is the better failure: two candidates a
    reviewer merges by eye beats one they must take apart.
    """
    ws = frequent_workflows(since, app, min_sessions, 2, max_len)
    if not ws:
        return []

    # Greedy complete-linkage. `ws` arrives sorted by support, so each new
    # group is seeded by the best-evidenced run still unplaced, and weaker
    # variants attach to it rather than the reverse.
    groups: list[list[Workflow]] = []
    for w in ws:
        for g in groups:
            if all(_similar(w.steps, m.steps, min_overlap) for m in g):
                g.append(w)
                break
        else:
            groups.append([w])
    buckets = {i: g for i, g in enumerate(groups)}

    out: list[WorkflowGroup] = []
    for members in buckets.values():
        members.sort(key=lambda w: (-w.sessions, -len(w.steps)))
        step_sets = [set(m.steps) for m in members]
        core = set.intersection(*step_sets) if step_sets else set()
        every = set.union(*step_sets) if step_sets else set()
        # Order the backbone by where the steps appear in the fullest variant,
        # so a reviewer reads a sequence rather than an alphabetised set.
        longest = max((m.steps for m in members), key=len)
        order = {t: i for i, t in enumerate(longest)}
        role_count: dict[str, int] = {}
        contested: dict[str, dict[str, Any]] = {}
        examples: list[str] = []
        for m in members:
            for r in m.roles:
                role_count[r["value"]] = role_count.get(r["value"], 0) + int(r["sessions"])
            for c in m.contested:
                slot = contested.setdefault(c["check_id"], {"check_id": c["check_id"], "sessions": 0, "findings": 0})
                slot["sessions"] = max(slot["sessions"], c["sessions"])
                slot["findings"] = max(slot["findings"], c["findings"])
            examples.extend(m.example_sessions)
        out.append(WorkflowGroup(
            variants=tuple(members),
            # The union of DISTINCT example sessions understates the true union
            # (examples are capped upstream), so the group's session count is
            # the largest variant's — a floor, never an inflated sum, since
            # summing would double-count every session running two variants.
            sessions=max(m.sessions for m in members),
            core_steps=tuple(sorted(core, key=lambda t: order.get(t, 99))),
            optional_steps=tuple(sorted(every - core, key=lambda t: order.get(t, 99))),
            roles=tuple(sorted(({"value": r, "sessions": n} for r, n in role_count.items()),
                               key=lambda d: (-d["sessions"], d["value"]))),
            contested=tuple(sorted(contested.values(), key=lambda d: (-d["sessions"], d["check_id"]))),
            example_sessions=tuple(dict.fromkeys(examples))[:5],
        ))
    out.sort(key=lambda g: (-g.sessions, -len(g.variants)))
    return out
