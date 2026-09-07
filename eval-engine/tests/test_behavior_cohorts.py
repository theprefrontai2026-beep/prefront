"""Cohort contrasts — the access-policy signal.

A policy is what makes one group of callers differ from another, so these test
the differences. The arithmetic that matters is the absence scoring: silence is
the weakest evidence and the easiest to over-read, and getting its strength
wrong invents policies nobody wrote.
"""

from __future__ import annotations

from evalengine.behavior import cohorts as co


def _rows(spec):
    """spec: {role: [(session, tool, [cols]), ...]}"""
    out = []
    for role, calls in spec.items():
        for sid, tool, cols in calls:
            out.append({"role": role, "tool_name": tool, "session_id": sid,
                        "cols": "[" + ", ".join(f'"{c}"' for c in cols) + "]"})
    return out


def _run(monkeypatch, spec):
    monkeypatch.setattr(co.ch, "rows", lambda *a, **k: _rows(spec))
    return {c.role: c for c in co.cohort_contrasts()}


def test_field_gap_is_detected(monkeypatch):
    """The strongest signal: same tool, fewer fields back. It cannot be
    explained by what a cohort happened to need."""
    got = _run(monkeypatch, {
        "boss":  [(f"b{i}", "read", ["id", "secret"]) for i in range(5)],
        "staff": [(f"s{i}", "read", ["id"]) for i in range(5)],
    })
    gap = got["staff"].field_gaps
    assert gap and gap[0]["tool"] == "read"
    assert gap[0]["withheld"] == ["secret"] and gap[0]["seen_by"] == ["boss"]
    assert not got["boss"].field_gaps


def test_exclusive_tools_are_reported(monkeypatch):
    got = _run(monkeypatch, {
        "boss":  [("b1", "approve", []), ("b1", "read", [])],
        "staff": [("s1", "read", [])],
    })
    assert got["boss"].exclusive_tools == ("approve",)
    assert got["staff"].exclusive_tools == ()


def test_silence_about_a_common_tool_is_a_boundary(monkeypatch):
    """Others reach it constantly; this cohort had ample sessions and never
    did. That is unlikely by chance."""
    spec = {"boss": [(f"b{i}", "approve", []) for i in range(40)],
            "staff": [(f"s{i}", "read", []) for i in range(40)]}
    got = _run(monkeypatch, spec)
    never = {u["tool"]: u for u in got["staff"].never_used}
    assert never["approve"]["likely_boundary"] is True
    assert never["approve"]["silence_by_chance"] < 0.05


def test_silence_about_a_rare_tool_is_inconclusive(monkeypatch):
    """The flat-threshold version got this wrong and told the model a
    31-session cohort had ample traffic; it returned HIGH confidence on a
    boundary spanning 13 unused tools. Whether n sessions is ample depends on
    how often the tool is used at all."""
    spec = {"boss": [(f"b{i}", "read", []) for i in range(40)],
            "staff": [(f"s{i}", "read", []) for i in range(8)]}
    spec["boss"].append(("b0", "rare", []))          # 1 of 40 sessions
    got = _run(monkeypatch, spec)
    never = {u["tool"]: u for u in got["staff"].never_used}
    assert never["rare"]["likely_boundary"] is False
    assert never["rare"]["silence_by_chance"] > 0.05


def test_a_tiny_cohort_concludes_nothing_whatever_the_arithmetic(monkeypatch):
    """Below a floor the sample is not a sample, however lopsided the rate."""
    spec = {"boss": [(f"b{i}", "approve", []) for i in range(50)],
            "staff": [("s1", "read", [])]}
    got = _run(monkeypatch, spec)
    assert got["staff"].has_exposure is False
    assert all(not u["likely_boundary"] for u in got["staff"].never_used)


def test_never_used_carries_the_evidence_not_just_the_verdict(monkeypatch):
    """A reader must be able to re-derive the call rather than trust it."""
    spec = {"boss": [(f"b{i}", "approve", []) for i in range(30)],
            "staff": [(f"s{i}", "read", []) for i in range(30)]}
    u = _run(monkeypatch, spec)["staff"].never_used[0]
    assert set(u) >= {"tool", "used_by", "this_cohort_sessions",
                      "others_use_rate", "silence_by_chance", "likely_boundary"}
