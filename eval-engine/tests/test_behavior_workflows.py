"""Frequent tool-run mining — the multi-call half of intent mining.

An intent is often a sequence, and the difference between a readable output and
an unusable one is entirely in the filtering: without it, every prefix, suffix
and rotation of every real pattern is its own row and a reviewer cannot tell
which of thirty overlapping lines are the same finding.
"""

from __future__ import annotations

from evalengine.behavior import workflows as wf


def _rows(seqs: dict[str, list[str]], role="Agent"):
    out = []
    for sid, tools in seqs.items():
        for t in tools:
            out.append({"session_id": sid, "tool_name": t, "user_role": role})
    return out


def _mine(monkeypatch, seqs, **kw):
    monkeypatch.setattr(wf.ch, "rows", lambda *a, **k: _rows(seqs))
    monkeypatch.setattr(wf, "_contested_by_tool", lambda *a, **k: {})
    return wf.frequent_workflows(min_sessions=kw.pop("min_sessions", 2), **kw)


def test_consecutive_repeats_collapse(monkeypatch):
    """A tool called twice running is a retry, not two steps. Left in, (A, A)
    becomes the most 'frequent' pattern in any corpus that retries."""
    got = _mine(monkeypatch, {"s1": ["a", "a", "a", "b"], "s2": ["a", "b"]})
    assert [w.steps for w in got] == [("a", "b")]


def test_support_counts_sessions_not_occurrences(monkeypatch):
    """A pattern five times in ONE session is one piece of evidence."""
    got = _mine(monkeypatch, {"s1": ["a", "b", "a", "b", "a", "b"], "s2": ["a", "b"]})
    w = next(x for x in got if x.steps == ("a", "b"))
    assert w.sessions == 2 and w.occurrences == 4


def test_a_fragment_of_a_longer_run_is_dropped(monkeypatch):
    """If a->b->c holds wherever a->b does, a->b is the start of a process,
    not a process. Both rows would be the same finding twice."""
    got = _mine(monkeypatch, {"s1": ["a", "b", "c"], "s2": ["a", "b", "c"]})
    steps = [w.steps for w in got]
    assert ("a", "b", "c") in steps
    assert ("a", "b") not in steps and ("b", "c") not in steps


def test_a_fragment_with_strictly_more_support_survives(monkeypatch):
    """a->b in three sessions and a->b->c in two means a->b usually does NOT
    continue — it is its own pattern, and collapsing it would hide that."""
    got = _mine(monkeypatch, {"s1": ["a", "b", "c"], "s2": ["a", "b", "c"], "s3": ["a", "b"]})
    steps = [w.steps for w in got]
    assert ("a", "b") in steps and ("a", "b", "c") in steps


def test_rotations_of_one_loop_collapse_to_the_best_supported(monkeypatch):
    """A looping session yields a distinct n-gram at every offset. They are one
    finding — the same tools in the same order, one window slid along."""
    got = _mine(monkeypatch, {f"s{i}": ["a", "b", "a", "b", "a"] for i in range(3)})
    keys = [frozenset(w.steps) for w in got]
    assert len(keys) == len(set(keys)), "the same step set appeared twice"


def test_ranked_by_support_not_length(monkeypatch):
    """Ranking by length puts the longest and RAREST run on top, which is
    backwards for a reviewer deciding what is a real process."""
    seqs = {f"s{i}": ["x", "y"] for i in range(9)}
    seqs["s9"] = ["p", "q", "r", "s"]
    seqs["s10"] = ["p", "q", "r", "s"]
    got = _mine(monkeypatch, seqs)
    assert got[0].steps == ("x", "y"), [w.steps for w in got]


def test_coverage_is_the_share_of_first_step_sessions_that_completed(monkeypatch):
    """The number that separates 'this is how that tool is used' from 'this is
    one of several things people do next'."""
    seqs = {"s1": ["a", "b"], "s2": ["a", "b"], "s3": ["a", "z"], "s4": ["a", "z"]}
    got = _mine(monkeypatch, seqs)
    ab = next(w for w in got if w.steps == ("a", "b"))
    assert ab.coverage == 0.5      # a appeared in 4 sessions, a->b completed in 2


def test_below_min_sessions_is_not_reported(monkeypatch):
    assert _mine(monkeypatch, {"s1": ["a", "b"]}, min_sessions=2) == []


def test_max_len_bounds_the_run(monkeypatch):
    """A very long run is one session's transcript, not a repeatable process,
    and reporting it invites blessing a single trace."""
    seqs = {f"s{i}": list("abcdefgh") for i in range(3)}
    got = _mine(monkeypatch, seqs, max_len=3)
    assert got and max(len(w.steps) for w in got) == 3


# ── grouping variants of one intent ───────────────────────────────────────

def _group(monkeypatch, seqs, **kw):
    monkeypatch.setattr(wf.ch, "rows", lambda *a, **k: _rows(seqs))
    monkeypatch.setattr(wf, "_contested_by_tool", lambda *a, **k: {})
    return wf.group_workflows(min_sessions=kw.pop("min_sessions", 2), **kw)


def test_variants_of_one_operation_group_together(monkeypatch):
    """The same intent appears in several shapes because the agent sometimes
    already held part of the data. Reported separately they are several
    candidates with near-identical policies."""
    seqs = {f"a{i}": ["find", "profile"] for i in range(4)}
    seqs.update({f"b{i}": ["find", "profile", "report"] for i in range(4)})
    got = _group(monkeypatch, seqs)
    assert len(got) == 1
    assert got[0].core_steps == ("find", "profile")
    assert got[0].optional_steps == ("report",)


def test_containment_merges_however_different_the_lengths(monkeypatch):
    """A short run wholly inside a long one is a variant of it. On Jaccard
    alone the length gap sinks the score and they split — the exact case
    grouping exists to merge."""
    seqs = {f"a{i}": ["p", "q"] for i in range(3)}
    seqs.update({f"b{i}": ["p", "q", "r", "s", "t", "u"] for i in range(3)})
    got = _group(monkeypatch, seqs)
    assert len(got) == 1 and got[0].core_steps == ("p", "q")


def test_unrelated_operations_do_not_chain_into_one_group(monkeypatch):
    """Single linkage failed here on real data: A resembles B, B resembles C,
    and the whole corpus chained into one 31-variant 'intent' with no step
    common to it. A group with no backbone is the corpus with a label on it."""
    seqs = {}
    seqs.update({f"a{i}": ["a", "b"] for i in range(3)})
    seqs.update({f"b{i}": ["b", "c"] for i in range(3)})
    seqs.update({f"c{i}": ["c", "d"] for i in range(3)})
    got = _group(monkeypatch, seqs, min_overlap=0.6)
    assert len(got) > 1, "unrelated runs chained into one group"
    for g in got:
        if len(g.variants) > 1:
            assert g.core_steps, "a group with more than one variant must share a step"


def test_group_sessions_is_a_floor_not_a_sum(monkeypatch):
    """Summing variants double-counts every session that ran two of them."""
    seqs = {f"s{i}": ["find", "profile", "report"] for i in range(5)}
    got = _group(monkeypatch, seqs)
    assert got[0].sessions == 5


def test_core_steps_are_ordered_by_the_fullest_variant(monkeypatch):
    """A reviewer should read a sequence, not an alphabetised set — the order
    is what makes it a precondition."""
    seqs = {f"s{i}": ["zeta", "alpha", "mid"] for i in range(3)}
    got = _group(monkeypatch, seqs)
    assert got[0].core_steps == ("zeta", "alpha", "mid")
