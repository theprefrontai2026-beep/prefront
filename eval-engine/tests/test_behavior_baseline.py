"""Has the deployment been watched long enough to have a baseline?

Learning is not a shorter monitoring phase. These test the one question the
learning phase can actually answer by counting — do we know what normal looks
like yet — without any judgement about whether normal is good.
"""

from __future__ import annotations

import json

from evalengine.behavior import baseline as bl
from evalengine.behavior import episodes as ep


def _calls(seq):
    """Timestamps ascend one minute per call.

    Real ones, because the measure buckets by CLOCK: an earlier version split
    by position, which re-partitioned the whole history on every new episode
    and so could never notice convergence — steady repeated traffic held the
    distinct-shape count flat while it went on reporting 25% novelty."""
    return [{"session_id": s, "tool_name": t, "user_role": "Agent",
             "raw": json.dumps({"a_id": i}), "effect": "read",
             "ts": f"2026-09-01 {i // 60 % 24:02d}:{i % 60:02d}:00.000000"}
            for i, (s, t) in enumerate(seq)]


def _run(monkeypatch, seq, **kw):
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: _calls(seq))
    return bl.learning_progress(**kw)


def test_no_traffic_says_so_rather_than_reporting_stability(monkeypatch):
    """An empty corpus is not a settled one."""
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: [])
    got = _run(monkeypatch, [])
    assert got["status"] == "no_traffic" and got["ready"] is False


def test_repeating_traffic_settles(monkeypatch):
    """The same shape over and over is exactly what a settled baseline is."""
    got = _run(monkeypatch, [(f"s{i}", "same") for i in range(200)])
    assert got["ready"] is True and got["status"] == "stable"
    assert got["recent_coverage"] >= bl.STABLE_COVERAGE


def test_constant_novelty_never_settles(monkeypatch):
    """A deployment still doing things it has never done has not been watched
    long enough, whatever the shapes look like."""
    got = _run(monkeypatch, [(f"s{i}", f"tool{i}") for i in range(200)])
    assert got["ready"] is False and got["status"] == "learning"
    assert "Keep observing" in got["recommendation"]


def test_the_first_period_is_never_credited_with_prior_coverage(monkeypatch):
    """It has nothing before it. 0.0 is the truth; calling it 1.0 would flatter
    the measure and let a one-period window declare itself settled."""
    got = _run(monkeypatch, [(f"s{i}", "same") for i in range(100)])
    assert got["buckets"][0]["explained_by_prior"] == 0.0


def test_coverage_is_measured_against_EARLIER_periods_only(monkeypatch):
    """Whole-window coverage would be circular — the patterns were derived from
    that traffic, so of course they cover it. The measure has to be a
    prediction: patterns learned up to yesterday, scored on today."""
    # A long stable run, then a shape that appears ONLY in the final period.
    # Whole-window coverage would score this 100% — the new shape is in the
    # window it was learned from. Prior-coverage must not.
    seq = [(f"a{i}", "known") for i in range(120)] + [(f"z{i}", "brand_new") for i in range(20)]
    got = _run(monkeypatch, seq)
    last = got["buckets"][-1]
    assert last["explained_by_prior"] < 1.0, got["buckets"]
    assert last["new_shapes"] >= 1
    assert got["ready"] is False


def test_thresholds_are_reported_as_conventions(monkeypatch):
    """A reviewer must be able to see and move them; they are not discoveries."""
    got = _run(monkeypatch, [(f"s{i}", "same") for i in range(50)])
    assert got["thresholds"] == {"coverage": bl.STABLE_COVERAGE, "novelty": bl.STABLE_NOVELTY}


def test_steady_repetition_is_recognised_as_settled(monkeypatch):
    """The regression this measure's first version failed. Running the same
    mix over and over adds NO new shapes — the distinct-shape count sits flat —
    and that is the definition of a settled baseline. Positional bucketing
    reported 25% novelty through four such rounds because every new episode
    re-partitioned the whole history and the 'most recent' chunk kept
    re-inheriting older bursts."""
    warmup = [(f"w{i}", t) for i in range(30) for t in ("alpha", "beta")]
    steady = [(f"s{i}", t) for i in range(90) for t in ("alpha", "beta")]
    got = _run(monkeypatch, warmup + steady)
    assert got["distinct_shapes"] <= 2
    assert got["ready"] is True, got["buckets"]
    assert got["recent_novelty"] == 0.0


def test_an_idle_period_is_dropped_not_scored_as_perfect(monkeypatch):
    """A deployment that was quiet overnight has not thereby learned anything,
    and an empty bucket counted as 100%-covered would let idleness declare
    readiness."""
    got = _run(monkeypatch, [(f"s{i}", "same") for i in range(40)])
    assert all(b["episodes"] > 0 for b in got["buckets"])
