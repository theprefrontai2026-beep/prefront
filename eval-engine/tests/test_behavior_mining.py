"""Behavioural mining aggregates (intent_learning_design.md L1).

These test the parts where being wrong is dangerous rather than merely untidy:
the answer key staying out of the profile, the contested overlay actually
attaching, and a hypothesis never being reported as stronger than its evidence.
"""

from __future__ import annotations

import json

import pytest

from evalengine.behavior import profiles as bp


def _span(tool, sid, role="Agent", channel="ui", args=None, cols=None,
          rows_=1, side="read", intent="", status="OK"):
    return {
        "tool_name": tool, "session_id": sid, "user_id": "u1", "user_role": role,
        "status": status,
        "input_value": json.dumps(args or {}),
        "raw": json.dumps(args or {}),
        "attributes": {
            "app.user.role": role, "app.channel": channel, "app.side_effect": side,
            "app.row_count": str(rows_), "app.columns": json.dumps(cols or []),
            "app.intent": intent,
        },
    }


def test_split_list_tolerates_the_three_shapes_exporters_emit():
    """A column list arrives as JSON, a Python repr or a bare CSV depending on
    the exporter. One tolerant split beats three SQL expressions that each work
    for one of them."""
    assert bp._split_list('["a", "b"]') == ["a", "b"]
    assert bp._split_list("['a', 'b']") == ["a", "b"]
    assert bp._split_list("a, b") == ["a", "b"]
    assert bp._split_list("") == []


def test_caller_invariant_needs_min_calls(monkeypatch):
    """One coincidence is not a rule. `holds == observed` is 100% whether the
    denominator is 3 or 300, so the floor is the only thing separating a
    mandatory filter from an accident."""
    rows = [_span("t", f"s{i}", args={"owner": "u1"}) for i in range(2)]
    monkeypatch.setattr(bp.ch, "rows", lambda *a, **k: rows)
    assert bp.caller_invariants(min_calls=3) == {}

    rows.append(_span("t", "s2", args={"owner": "u1"}))
    got = bp.caller_invariants(min_calls=3)
    assert got["t"] == [{"param": "owner", "equals": "caller.user_id", "holds": 3, "observed": 3}]


def test_caller_invariant_reports_the_denominator_not_a_boolean(monkeypatch):
    """A single counter-example must break it outright — an invariant that
    "usually" holds is not an invariant, and reporting it as one would propose
    a filter the runtime would then enforce against real traffic."""
    rows = [_span("t", f"s{i}", args={"owner": "u1"}) for i in range(4)]
    rows.append(_span("t", "s9", args={"owner": "SOMEONE-ELSE"}))
    monkeypatch.setattr(bp.ch, "rows", lambda *a, **k: rows)
    assert "t" not in bp.caller_invariants(min_calls=3)


def test_following_tools_rate_is_over_sessions_not_calls(monkeypatch):
    """A tool called five times in one session and followed once is not 20% of
    a population — it is one session out of one."""
    seq = [_span("a", "s1"), _span("b", "s1"), _span("a", "s2"), _span("a", "s3")]
    monkeypatch.setattr(bp.ch, "rows", lambda *a, **k: seq)
    got = bp.following_tools(min_support=1)
    assert got["a"] == [{"tool": "b", "sessions": 1, "of_sessions": 3, "rate": round(1 / 3, 3)}]


def test_following_tools_ignores_self_repeats(monkeypatch):
    """A tool calling itself again is a retry or a loop, not an obligation."""
    monkeypatch.setattr(bp.ch, "rows", lambda *a, **k: [_span("a", "s1"), _span("a", "s1")])
    assert bp.following_tools(min_support=1) == {}


def test_observed_intent_labels_are_not_in_the_profile(monkeypatch):
    """THE load-bearing test of this package. `app.intent` is the answer key: a
    miner that reads the label it is meant to predict measures nothing. It must
    be reachable only through the explicitly-named scoring function."""
    calls: list[str] = []

    def fake_rows(sql, params=None):
        calls.append(sql)
        return []

    monkeypatch.setattr(bp.ch, "rows", fake_rows)
    bp.tool_profiles()
    assert calls, "expected the profiler to query something"
    assert not any("app.intent" in sql for sql in calls), (
        "tool_profiles must never read app.intent — that is the answer key")

    calls.clear()
    bp.observed_intent_labels()
    assert any("app.intent" in sql for sql in calls), (
        "the scoring accessor is the one place that may read it")


def test_contested_overlay_is_family2_only(monkeypatch):
    """Family 1 and Family 3 verdicts depend on the very artifacts a mining
    deployment does not have. Counting them would leave the overlay silently
    empty exactly where it is needed most."""
    seen: list[str] = []

    def fake_rows(sql, params=None):
        seen.append(sql)
        return [{"tool_name": "t", "session_id": "s1"}] if "DISTINCT" in sql else []

    monkeypatch.setattr(bp.ch, "rows", fake_rows)
    bp._contested_by_tool()
    verdict_sql = [s for s in seen if "eval_verdicts" in s]
    assert verdict_sql, "expected a verdict query"
    assert all("family2" in s and "violated" in s for s in verdict_sql)


def test_contested_attaches_violations_to_every_tool_in_the_session(monkeypatch):
    """A session's integrity violation contests every tool that session used:
    the checks are about the session's behaviour, and which call within it was
    at fault is exactly what is unknown without a policy."""
    def fake_rows(sql, params=None):
        if "DISTINCT" in sql:
            return [{"tool_name": "a", "session_id": "s1"}, {"tool_name": "b", "session_id": "s1"}]
        return [{"session_id": "s1", "check_id": "param_taint", "n": 2}]

    monkeypatch.setattr(bp.ch, "rows", fake_rows)
    got = bp._contested_by_tool()
    assert got["a"] == [{"check_id": "param_taint", "sessions": 1, "findings": 2}]
    assert got["b"] == got["a"]
