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
    return [{"session_id": s, "tool_name": t, "user_role": "Agent",
             "raw": json.dumps({"a_id": i}), "effect": "read"}
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
    assert got["buckets"][-1]["explained_by_prior"] < 1.0, got["buckets"]
    assert got["buckets"][-1]["new_shapes"] >= 1
    assert got["ready"] is False


def test_thresholds_are_reported_as_conventions(monkeypatch):
    """A reviewer must be able to see and move them; they are not discoveries."""
    got = _run(monkeypatch, [(f"s{i}", "same") for i in range(50)])
    assert got["thresholds"] == {"coverage": bl.STABLE_COVERAGE, "novelty": bl.STABLE_NOVELTY}
