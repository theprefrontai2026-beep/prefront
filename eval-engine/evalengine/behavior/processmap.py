"""The observed process map: tools as nodes, transitions as weighted edges.

Every other surface in this package is a list. A list is the right shape for
reviewing one candidate and the wrong one for the question people actually ask
first — *what does this system DO?* That question is about structure, and a
directed graph of observed transitions answers it in one glance where thirty
rows of sequences do not.

Nothing here is inferred or laid out for effect: an edge exists because that
transition was observed, and its weight is how often. This is the corpus, drawn.

TRANSITIONS ARE COUNTED WITHIN EPISODES, NOT ACROSS THEM. An episode is one
operation on one subject (see episodes.py), so a hop inside one is part of the
same piece of work, while the gap between two is just what the caller happened
to do next. Counting across episode boundaries would draw edges between
unrelated operations and make the map denser and less true — which, on a
diagram, reads as MORE insight rather than less.

Entry and exit are first-class. Which tool an operation tends to START at, and
which one ENDS it, is most of what a reader wants and is invisible in a plain
adjacency count.
"""

from __future__ import annotations

from typing import Any

from .episodes import session_episodes


def process_map(since: int = 7 * 86400, app: str = "",
                min_edge: int = 2, top_n: int = 24) -> dict[str, Any]:
    """Nodes, edges, entries and exits over the observed episodes.

    `min_edge` prunes transitions seen once or twice. A process map's failure
    mode is the hairball — every rare hop drawn at the same visual weight as the
    spine, so the reader sees complexity rather than structure — and the pruned
    weight is reported so nothing is silently hidden.
    """
    eps = session_episodes(since, app)
    if not eps:
        return {"nodes": [], "edges": [], "episodes": 0, "pruned_edges": 0, "pruned_weight": 0}

    node_eps: dict[str, int] = {}
    writes: set[str] = set()
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    edges: dict[tuple[str, str], int] = {}
    roles: dict[str, dict[str, int]] = {}

    for e in eps:
        if not e.steps:
            continue
        for t in e.steps:
            node_eps[t] = node_eps.get(t, 0) + 1
            if e.role:
                roles.setdefault(t, {})[e.role] = roles.setdefault(t, {}).get(e.role, 0) + 1
        starts[e.steps[0]] = starts.get(e.steps[0], 0) + 1
        ends[e.steps[-1]] = ends.get(e.steps[-1], 0) + 1
        if e.closed_by:
            writes.add(e.closed_by)
        for a, b in zip(e.steps, e.steps[1:]):
            edges[(a, b)] = edges.get((a, b), 0) + 1

    # Keep the busiest nodes; a map of everything is a map of nothing.
    keep = {t for t, _ in sorted(node_eps.items(), key=lambda kv: -kv[1])[:top_n]}

    kept, pruned, pruned_w = [], 0, 0
    for (a, b), n in edges.items():
        if a in keep and b in keep and n >= min_edge:
            kept.append({"source": a, "target": b, "count": n})
        else:
            pruned += 1
            pruned_w += n
    kept.sort(key=lambda e: -e["count"])

    nodes = [
        {"id": t, "episodes": n,
         # A side-effecting tool is the point of the operation that reaches it,
         # so it is drawn differently rather than left for the reader to spot.
         "writes": t in writes,
         "starts": starts.get(t, 0), "ends": ends.get(t, 0),
         "roles": sorted(({"value": r, "episodes": c} for r, c in (roles.get(t) or {}).items()),
                         key=lambda d: (-d["episodes"], d["value"]))[:4]}
        for t, n in sorted(node_eps.items(), key=lambda kv: -kv[1]) if t in keep
    ]
    return {"nodes": nodes, "edges": kept, "episodes": len(eps),
            "pruned_edges": pruned, "pruned_weight": pruned_w,
            "max_edge": max((e["count"] for e in kept), default=0),
            "max_node": max((n["episodes"] for n in nodes), default=0)}
