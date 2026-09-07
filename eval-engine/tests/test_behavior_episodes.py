"""Session segmentation into episodes.

An episode is one operation on one subject. The boundary rules are structural
rather than statistical, which is what makes an episode a thing a reviewer can
approve where an n-gram is not — so the rules are what these test.
"""

from __future__ import annotations

import json

from evalengine.behavior import episodes as ep


def _rows(calls):
    """calls: [(session, tool, args, effect)]"""
    return [{"session_id": s, "tool_name": t, "user_role": "Agent",
             "raw": json.dumps(a), "effect": e} for s, t, a, e in calls]


def _run(monkeypatch, calls):
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: _rows(calls))
    return ep.session_episodes()


def test_same_identifier_new_value_is_a_boundary(monkeypatch):
    """Calls carrying id 1 then id 2 are two operations, however often that
    pair of tools co-occurs."""
    got = _run(monkeypatch, [
        ("s", "read", {"thing_id": 1}, "read"),
        ("s", "read2", {"thing_id": 2}, "read"),
    ])
    assert [e.steps for e in got] == [("read",), ("read2",)]


def test_a_different_identifier_is_not_a_boundary(monkeypatch):
    """THE bug the first version had. One operation legitimately walks between
    related entities — fetch by its own id, then pull a report by the id of the
    party it names — and cutting there severed every decision from the evidence
    gathered for it, reporting 228 decisions as having no preconditions."""
    got = _run(monkeypatch, [
        ("s", "get_record", {"record_id": 7012}, "read"),
        ("s", "get_report", {"party_id": 4}, "read"),
        ("s", "decide", {"record_id": 7012}, "write"),
    ])
    assert len(got) == 1
    assert got[0].steps == ("get_record", "get_report", "decide")
    assert got[0].closed_by == "decide"
    assert got[0].before_effect == ("get_record", "get_report")


def test_a_side_effect_closes_the_episode(monkeypatch):
    """A write is the terminal act of whatever led to it; what follows belongs
    to the next operation."""
    got = _run(monkeypatch, [
        ("s", "read", {"a_id": 1}, "read"),
        ("s", "write", {"a_id": 1}, "write"),
        ("s", "after", {"a_id": 1}, "read"),
    ])
    assert [e.steps for e in got] == [("read", "write"), ("after",)]
    assert got[0].closed_by == "write" and got[1].closed_by == ""


def test_a_call_with_no_identifier_does_not_cut(monkeypatch):
    """Treating 'absent' as 'different' would cut every session into single
    calls."""
    got = _run(monkeypatch, [
        ("s", "list", {}, "read"),
        ("s", "read", {"a_id": 1}, "read"),
        ("s", "list", {}, "read"),
    ])
    assert len(got) == 1 and got[0].steps == ("list", "read", "list")


def test_retries_collapse_within_an_episode(monkeypatch):
    got = _run(monkeypatch, [("s", "read", {"a_id": 1}, "read")] * 3)
    assert got[0].steps == ("read",)


def test_shapes_carry_the_precondition_run(monkeypatch):
    """What preceded the write, which is the candidate precondition on it."""
    calls = []
    for i in range(4):
        calls += [(f"s{i}", "gather", {"a_id": i}, "read"),
                  (f"s{i}", "act", {"a_id": i}, "write")]
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: _rows(calls))
    shapes = ep.episode_shapes(min_episodes=2)
    s = next(x for x in shapes if x.closed_by == "act")
    assert s.before_effect == ("gather",) and s.episodes == 4


def test_explained_fraction_reports_coverage(monkeypatch):
    """A catalog covering a third of the traffic leaves most of it ungoverned,
    and a reviewer should be told before approving rather than after."""
    calls = [(f"s{i}", "common", {"a_id": i}, "read") for i in range(5)]
    calls.append(("odd", "rare_one", {"a_id": 99}, "read"))
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: _rows(calls))
    got = ep.explained_fraction(min_episodes=2)
    assert got["episodes"] == 6 and got["explained"] == 5
    assert got["fraction"] == round(5 / 6, 3)
