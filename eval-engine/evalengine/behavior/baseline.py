"""Has the deployment been watched long enough to have a baseline?

The learning phase is not a shorter version of monitoring. Monitoring compares
behaviour against a known-good shape; learning is how that shape is obtained,
and until it exists there is nothing to compare against. Judging traffic on day
one presumes the answer: calling an operation "frequently performed with no
preconditions" is only a finding if you already know it ought to have some, and
that is precisely what has not been established yet.

So the question this module answers is not "is anything wrong" but "do we know
what normal looks like yet" — and it is answerable by counting, without any
judgement at all:

  NOVELTY. How many patterns appeared for the first time in the most recent
  bucket? A deployment still producing new shapes on day seven has not been
  watched long enough, whatever the shapes look like.

  PRIOR COVERAGE. What share of the most recent bucket's episodes were already
  explained by patterns learned from EARLIER buckets? This is the honest test,
  because it is a prediction: the catalog learned up to yesterday is scored on
  traffic it had never seen. Coverage measured over the whole window instead
  would be circular — the shapes were derived from that traffic, so of course
  they cover it.

A deployment is ready to review when recent traffic is mostly explained by what
was learned before it, and new shapes have stopped arriving. Neither threshold
is a discovery; both are conventions, and they are stated as such so a reviewer
can move them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .episodes import session_episodes


def _lerp(lo: str, hi: str, f: float) -> str:
    """A timestamp fraction f of the way from lo to hi.

    String timestamps, compared and interpolated as text: they are ISO-ordered,
    which is all this needs, and parsing them would add a dependency on their
    exact format for no gain.
    """
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in lo else "%Y-%m-%d %H:%M:%S"
    try:
        a = datetime.strptime(lo[:26], fmt)
        b = datetime.strptime(hi[:26], "%Y-%m-%d %H:%M:%S.%f" if "." in hi else "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return lo if f < 1 else hi
    return (a + (b - a) * f).strftime("%Y-%m-%d %H:%M:%S.%f")

# Conventions, not findings. A deployment whose recent traffic is 90% explained
# by earlier learning, still turning up under 10% new shapes, has stopped
# telling us things we did not know.
STABLE_COVERAGE = 0.90
STABLE_NOVELTY = 0.10


def _verdict(episodes: int, shapes: int, status: str, recommendation: str) -> dict[str, Any]:
    """An early return carrying the SAME keys as a full one.

    A caller reading `distinct_shapes` off a short-circuit payload got a
    KeyError, and a UI reading it rendered `undefined` — an answer shaped
    differently from the one it usually gives is worse than a wrong number,
    because nothing downstream is written to expect it.
    """
    return {
        "observed_episodes": episodes, "distinct_shapes": shapes, "buckets": [],
        "recent_coverage": 0.0, "recent_novelty": 0.0,
        "thresholds": {"coverage": STABLE_COVERAGE, "novelty": STABLE_NOVELTY},
        "ready": False, "status": status, "recommendation": recommendation,
    }


@dataclass(frozen=True)
class Bucket:
    label: str
    episodes: int
    new_shapes: int              # shapes first seen in this bucket
    explained_by_prior: float    # share of this bucket's episodes seen before


def learning_progress(since: int = 7 * 86400, app: str = "",
                      bucket_seconds: int = 86400) -> dict[str, Any]:
    """Bucket the window and measure whether the pattern set has settled."""
    eps = session_episodes(since, app)
    if not eps:
        return _verdict(0, 0, "no_traffic",
                        "No tool calls in this window — nothing to learn from yet.")

    # BUCKET BY TIME, not by position. Positional bucketing was the first
    # implementation and it could not see convergence at all: every new episode
    # re-partitions the whole history, so the "most recent" chunk keeps
    # re-inheriting whatever fell in the last seventh of ALL traffic. Running
    # steady repeated traffic proved it — the distinct-shape count sat at 49
    # for four rounds, meaning nothing new was being learned, while the measure
    # went on reporting 25% novelty and refusing to settle. A convergence
    # measure that cannot notice convergence is worse than none.
    #
    # Episodes are ordered by time already, so a real clock split is a
    # partition on `started_at`. Empty periods are dropped rather than counted
    # as perfectly-covered: a deployment that was idle overnight has not
    # thereby learned anything.
    stamped = [e for e in eps if e.started_at]
    if len(stamped) < 2:
        return _verdict(len(eps), 0, "no_traffic",
                        "Not enough timestamped traffic in this window to judge.")
    stamped.sort(key=lambda e: e.started_at)
    lo, hi = stamped[0].started_at, stamped[-1].started_at
    n_buckets = max(2, min(7, int(since / bucket_seconds) or 2))
    edges = [_lerp(lo, hi, i / n_buckets) for i in range(n_buckets + 1)]
    chunks = []
    for i in range(n_buckets):
        a, b = edges[i], edges[i + 1]
        last = i == n_buckets - 1
        chunk = [e for e in stamped if a <= e.started_at and (e.started_at <= b if last else e.started_at < b)]
        if chunk:
            chunks.append(chunk)
    if len(chunks) < 2:
        return _verdict(len(eps), len({(e.steps, e.closed_by) for e in stamped}), "no_traffic",
                        "All traffic in this window arrived at once — observe over a "
                        "longer period before judging.")

    seen: set[tuple] = set()
    buckets: list[Bucket] = []
    for i, chunk in enumerate(chunks):
        shapes = {(e.steps, e.closed_by) for e in chunk}
        new = shapes - seen
        hit = sum(1 for e in chunk if (e.steps, e.closed_by) in seen)
        buckets.append(Bucket(
            label=f"period {i + 1}", episodes=len(chunk), new_shapes=len(new),
            # The first bucket has nothing before it; 0.0 is the truth, not a
            # failure, and calling it 1.0 would flatter the measure.
            explained_by_prior=round(hit / len(chunk), 3) if chunk else 0.0,
        ))
        seen |= shapes

    last = buckets[-1]
    novelty = round(last.new_shapes / max(1, len({(e.steps, e.closed_by) for e in chunks[-1]})), 3)
    ready = last.explained_by_prior >= STABLE_COVERAGE and novelty <= STABLE_NOVELTY

    if ready:
        rec = ("The pattern set has settled: recent traffic is mostly explained by what was "
               "already learned, and few new shapes are arriving. A catalog mined now is worth "
               "reviewing.")
    elif last.explained_by_prior < STABLE_COVERAGE:
        rec = (f"Still learning: only {int(last.explained_by_prior * 100)}% of the most recent "
               f"traffic was explained by patterns learned before it. Keep observing — a catalog "
               f"mined now would miss what has not been seen.")
    else:
        rec = (f"Still learning: {last.new_shapes} new shape(s) appeared in the most recent "
               f"period. Coverage is good but the deployment is still doing things it had not "
               f"done before.")

    return {
        "observed_episodes": len(eps),
        "distinct_shapes": len(seen),
        "buckets": [b.__dict__ for b in buckets],
        "recent_coverage": last.explained_by_prior,
        "recent_novelty": novelty,
        "thresholds": {"coverage": STABLE_COVERAGE, "novelty": STABLE_NOVELTY},
        "ready": ready,
        "status": "stable" if ready else "learning",
        "recommendation": rec,
    }
