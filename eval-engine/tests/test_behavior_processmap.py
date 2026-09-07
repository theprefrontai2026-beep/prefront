"""The observed process map.

A diagram is believed more readily than a list, so the things worth testing are
the ones that would make it flatter than the truth: counting transitions across
operations that have nothing to do with each other, and dropping the tail
without saying so.
"""

from __future__ import annotations

import json

from evalengine.behavior import episodes as ep
from evalengine.behavior import processmap as pm


def _calls(seq):
    return [{"session_id": s, "tool_name": t, "user_role": "Agent",
             "raw": json.dumps({"a_id": sub}), "effect": eff,
             "ts": f"2026-09-01 00:{i % 60:02d}:00.000000"}
            for i, (s, t, sub, eff) in enumerate(seq)]


def _map(monkeypatch, seq, **kw):
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: _calls(seq))
    return pm.process_map(min_edge=kw.pop("min_edge", 1), **kw)


def test_transitions_are_counted_within_an_operation(monkeypatch):
    got = _map(monkeypatch, [("s", "a", 1, "read"), ("s", "b", 1, "read")])
    assert got["edges"] == [{"source": "a", "target": "b", "count": 1}]


def test_no_edge_is_drawn_across_an_operation_boundary(monkeypatch):
    """The load-bearing one. A hop inside one operation is part of the same
    piece of work; the gap between two is just what the caller did next.
    Counting across draws edges between unrelated operations and makes the map
    denser and LESS true — which, on a diagram, reads as more insight."""
    got = _map(monkeypatch, [("s", "a", 1, "read"), ("s", "b", 2, "read")])
    assert got["edges"] == [], "an edge was drawn across a subject change"


def test_a_write_ends_an_operation_so_no_edge_follows_it(monkeypatch):
    got = _map(monkeypatch, [("s", "a", 1, "write"), ("s", "b", 1, "read")])
    assert got["edges"] == []
    assert next(n for n in got["nodes"] if n["id"] == "a")["writes"] is True


def test_entry_and_exit_are_counted(monkeypatch):
    """Where work begins and ends is most of what a reader wants, and is
    invisible in a plain adjacency count."""
    got = _map(monkeypatch, [("s", "a", 1, "read"), ("s", "b", 1, "read")])
    n = {x["id"]: x for x in got["nodes"]}
    assert n["a"]["starts"] == 1 and n["a"]["ends"] == 0
    assert n["b"]["ends"] == 1 and n["b"]["starts"] == 0


def test_pruned_edges_are_reported_never_silently_dropped(monkeypatch):
    """A map that quietly drops its tail looks cleaner than the system is, and
    the reader cannot tell that it did."""
    seq = [("s1", "a", 1, "read"), ("s1", "b", 1, "read"),
           ("s2", "a", 2, "read"), ("s2", "b", 2, "read"),
           ("s3", "a", 3, "read"), ("s3", "z", 3, "read")]     # a->z seen once
    got = _map(monkeypatch, seq, min_edge=2)
    assert [e["source"] + "->" + e["target"] for e in got["edges"]] == ["a->b"]
    assert got["pruned_edges"] == 1 and got["pruned_weight"] == 1


def test_top_n_keeps_the_busiest_tools(monkeypatch):
    """A map of everything is a map of nothing."""
    seq = [(f"s{i}", "hot", i, "read") for i in range(10)]
    seq += [(f"c{i}", f"cold{i}", i, "read") for i in range(10)]
    got = _map(monkeypatch, seq, top_n=1)
    assert [n["id"] for n in got["nodes"]] == ["hot"]


def test_empty_corpus_is_an_empty_map_not_an_error(monkeypatch):
    monkeypatch.setattr(ep.ch, "rows", lambda *a, **k: [])
    got = pm.process_map()
    assert got["nodes"] == [] and got["edges"] == [] and got["episodes"] == 0
