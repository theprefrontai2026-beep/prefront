"""Segment a session into EPISODES — one operation on one subject.

Everything else in this package aggregates across sessions: n-grams, cohort
contrasts. None of it asks what a single session reveals, and a session is not
one intent — it is a sequence of them. To hand a reviewer "this is a governed
intent, approve it", something has to say where one operation ends and the next
begins, and an n-gram cannot: it is a window slid over a stream with no notion
of when the stream changed subject.

TWO BOUNDARY SIGNALS, both structural rather than statistical, which is what
makes an episode more trustworthy than a frequent substring:

1. THE SUBJECT CHANGES — meaning the SAME identifier takes a DIFFERENT value.
   A run carrying id 5001 followed by calls carrying 5007 is two operations,
   however often that pair of tools co-occurs. Identifiers are read from
   argument NAMES ending in `_id` (or exactly `id`) — a naming convention, not
   a domain noun, so the engine still names no customer's vocabulary. Same
   premise the Data Graph builds on: two tools taking the same parameter are
   operating on the same thing.

   A DIFFERENT identifier appearing is NOT a boundary, and getting this wrong
   was the first version's bug. One operation legitimately walks between
   related entities — fetch the record by its own id, then pull a report by the
   id of the party it names — and treating that hop as a new subject severed
   every decision from the evidence gathered for it. A real session read
   `get_application(loan_id=7012)`, `get_credit_report(applicant_id=4)`,
   `decide_loan(loan_id=7012)`, and the old rule cut it into three episodes,
   reporting 228 decisions as having no preconditions at all. So each episode
   carries a MAP of identifier -> value, and only a contradiction on a
   previously-seen identifier ends it.
2. A SIDE EFFECT CLOSES ONE. A write is the terminal act of whatever led to
   it — everything before it in the episode is the evidence gathered to justify
   it, which is exactly the shape a precondition takes. Calls after it belong
   to the next operation.

WHY THIS IS THE RIGHT INPUT FOR POLICY INFERENCE. An n-gram says "these tools
co-occur". An episode says "to do X for subject S, this caller first did A, B
and C" — the claim a business policy actually makes. The steps preceding a
write in an episode are candidate PRECONDITIONS on that write, and unlike a
sequence rate they are bounded by a real event rather than a window size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from .. import ch
from .profiles import _TOOL_PRED, _window

# An argument that names the thing being operated on. A convention (`*_id`),
# never a specific field: the engine names no deployment's vocabulary.
_ID_ARG = re.compile(r"(^|_)id$", re.IGNORECASE)


@dataclass(frozen=True)
class Episode:
    session_id: str
    role: str
    steps: tuple[str, ...]
    subject_arg: str                  # which argument identified the subject
    subject: str                      # its value, as text
    closed_by: str                    # the side-effecting tool that ended it, or ""
    started_at: str = ""              # when the episode's first call happened
    # The steps that preceded the closing write — candidate preconditions on it.
    before_effect: tuple[str, ...] = ()


def _ids(args: dict[str, Any]) -> dict[str, str]:
    """Every identifier this call carries, not just the first.

    All of them, because an operation is pinned by whichever ids it mentions
    and picking one arbitrarily makes the boundary depend on argument order.
    """
    return {str(k): str(v) for k, v in args.items()
            if _ID_ARG.search(str(k)) and v not in (None, "", [])}


def session_episodes(since: int = 7 * 86400, app: str = "") -> list[Episode]:
    """Every session, cut into episodes."""
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT session_id, tool_name, user_role, input_value AS raw,
               toString(start_time) AS ts,
               attributes['app.side_effect'] AS effect
        FROM {ch.SPANS_T} {where}
        ORDER BY session_id, start_time, span_id
    """, params)

    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows_:
        by_session.setdefault(str(r["session_id"]), []).append(r)

    out: list[Episode] = []
    for sid, calls in by_session.items():
        steps: list[str] = []
        subjects: dict[str, str] = {}
        role = ""
        started = ""

        def flush(closed_by: str = "") -> None:
            if not steps:
                return
            # Report the FIRST identifier seen as the episode's subject: it is
            # the one the operation opened on, and later ones are things it
            # reached through that.
            arg, val = next(iter(subjects.items()), ("", ""))
            out.append(Episode(
                session_id=sid, role=role, steps=tuple(steps),
                subject_arg=arg, subject=val, closed_by=closed_by,
                started_at=started,
                before_effect=tuple(steps[:-1]) if closed_by else (),
            ))
            steps.clear()
            subjects.clear()

        for c in calls:
            tool = str(c["tool_name"])
            role = str(c.get("user_role") or role)
            try:
                args = json.loads(str(c["raw"] or "{}"))
            except Exception:  # noqa: BLE001
                args = {}
            ids = _ids(args if isinstance(args, dict) else {})

            # A CONTRADICTION on an identifier already in play ends the
            # episode. A NEW identifier does not — that is the operation
            # reaching a related entity, not changing subject. A call carrying
            # no identifier at all (an unfiltered list, say) has nothing to
            # disagree with; treating "absent" as "different" would cut every
            # session into single calls.
            if any(k in subjects and subjects[k] != v for k, v in ids.items()):
                flush()
            subjects.update(ids)

            if not steps:
                started = str(c.get("ts") or "")
            if not steps or steps[-1] != tool:      # collapse retries
                steps.append(tool)

            if str(c.get("effect") or "").lower() not in ("", "read"):
                flush(closed_by=tool)
                subj_arg = subj = ""
        flush()
    return out


@dataclass(frozen=True)
class EpisodeShape:
    steps: tuple[str, ...]
    closed_by: str
    episodes: int
    sessions: int
    roles: tuple[dict[str, Any], ...]
    subject_args: tuple[str, ...]
    before_effect: tuple[str, ...]     # the modal precondition run, when closed
    example_sessions: tuple[str, ...]


def episode_shapes(since: int = 7 * 86400, app: str = "",
                   min_episodes: int = 3) -> list[EpisodeShape]:
    """Distinct episode shapes, with support.

    These are the candidate governed intents: each is a bounded operation on
    one subject, not a window over a stream, so "approve this as an intent" is
    a question a reviewer can actually answer about it.
    """
    eps = session_episodes(since, app)
    agg: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
    for e in eps:
        key = (e.steps, e.closed_by)
        slot = agg.setdefault(key, {"episodes": 0, "sessions": set(), "roles": {},
                                    "subject_args": set(), "examples": []})
        slot["episodes"] += 1
        slot["sessions"].add(e.session_id)
        if e.role:
            slot["roles"][e.role] = slot["roles"].get(e.role, 0) + 1
        if e.subject_arg:
            slot["subject_args"].add(e.subject_arg)
        if len(slot["examples"]) < 5:
            slot["examples"].append(e.session_id)

    out = [
        EpisodeShape(
            steps=steps, closed_by=closed, episodes=v["episodes"], sessions=len(v["sessions"]),
            roles=tuple(sorted(({"value": r, "episodes": n} for r, n in v["roles"].items()),
                               key=lambda d: (-d["episodes"], d["value"]))),
            subject_args=tuple(sorted(v["subject_args"])),
            before_effect=steps[:-1] if closed else (),
            example_sessions=tuple(v["examples"]),
        )
        for (steps, closed), v in agg.items() if v["episodes"] >= min_episodes
    ]
    out.sort(key=lambda s: (-s.episodes, -len(s.steps)))
    return out


def explained_fraction(since: int = 7 * 86400, app: str = "",
                       min_episodes: int = 3) -> dict[str, Any]:
    """How much of the deployment's traffic the candidate intents account for.

    The measurement that says whether a mined catalog is worth approving.
    A catalog covering 30% of episodes leaves most of what the agent does
    ungoverned, and a reviewer should be told that BEFORE approving it rather
    than discovering it from a thin findings feed afterwards.
    """
    eps = session_episodes(since, app)
    if not eps:
        return {"episodes": 0, "explained": 0, "fraction": 0.0, "shapes": 0}
    known = {(s.steps, s.closed_by) for s in episode_shapes(since, app, min_episodes)}
    hit = sum(1 for e in eps if (e.steps, e.closed_by) in known)
    return {"episodes": len(eps), "explained": hit,
            "fraction": round(hit / len(eps), 3), "shapes": len(known)}
