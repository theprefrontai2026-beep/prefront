"""Intent mining synthesis (intent_learning_design.md L2).

The deterministic half, and the guards that stop a mined candidate from
laundering bad observed behaviour into policy. These are the tests that matter:
the LLM's prose is advisory and a reviewer reads it, but if the counted fields
or the warnings are wrong, a reviewer approves something they were never shown.
"""

from __future__ import annotations

import json

import pytest

from semanticlayer.intent_mining import (
    CandidateIntent,
    infer_policy,
    mine_intents,
    render_prompt,
    structural_candidate,
)


def profile(**over):
    base = {
        "tool_name": "t", "calls": 40, "sessions": 20, "error_calls": 0,
        "side_effects": [{"value": "read", "sessions": 20, "calls": 40}],
        "roles": [{"value": "Agent", "sessions": 20, "calls": 40}],
        "channels": [{"value": "ui", "sessions": 20, "calls": 40}],
        "params": [{"value": "id", "sessions": 20, "calls": 40}],
        "fields": [{"value": "a", "sessions": 20, "calls": 40}],
        "row_count_p99": 3, "caller_invariants": [], "followed_by": [],
        "contested": [], "example_sessions": ["s1"],
    }
    base.update(over)
    return base


class _LLM:
    """Stands in for the model. Returns whatever the test hands it."""
    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


def test_structural_half_needs_no_model():
    c = structural_candidate(profile())
    assert (c.tool_name, c.side_effect, c.params, c.fields) == ("t", "read", ["id"], ["a"])
    assert c.expected_rows_p99 == 3
    assert c.review_status == "pending"
    assert c.inferred_policy is None


def test_contested_sessions_raise_a_warning():
    """Defence #1. A profile supported by sessions with integrity violations is
    contested observed practice, and must never reach a reviewer looking clean."""
    c = structural_candidate(profile(contested=[{"check_id": "param_taint", "sessions": 4, "findings": 9}]))
    assert any("param_taint" in w for w in c.warnings)
    assert c.contested


def test_a_rare_caller_is_flagged_not_blessed():
    """The normalization-of-deviance hotspot: observed != allowed. One session
    out of a hundred is an outlier to narrow, and presenting it as a permitted
    role is exactly the failure this design exists to avoid."""
    c = structural_candidate(profile(
        sessions=100,
        roles=[{"value": "Agent", "sessions": 99, "calls": 99},
               {"value": "Stranger", "sessions": 1, "calls": 1}]))
    assert any("Stranger" in w and "outlier" in w for w in c.warnings)
    # ...but it is still REPORTED, with its share, because hiding it would hide
    # the leak rather than the noise.
    assert [r["value"] for r in c.observed_roles] == ["Agent", "Stranger"]
    assert c.observed_roles[1]["share"] == 0.01


def test_thin_evidence_is_flagged():
    assert any("thin evidence" in w for w in structural_candidate(profile(sessions=1)).warnings)


def test_inconsistent_side_effect_is_flagged_not_averaged():
    c = structural_candidate(profile(side_effects=[
        {"value": "read", "sessions": 18, "calls": 30},
        {"value": "write", "sessions": 2, "calls": 3}]))
    assert c.side_effect == "read"          # the dominant one wins
    assert any("not consistent" in w for w in c.warnings)


def test_closing_obligation_requires_near_universality():
    """Below the threshold this is a common sequence, not a duty — and a duty
    is enforceable, so the distinction has teeth."""
    assert structural_candidate(profile(followed_by=[{"tool": "b", "rate": 0.6}])).closing_obligation == ""
    assert structural_candidate(profile(followed_by=[{"tool": "b", "rate": 0.95}])).closing_obligation == "b"


def test_mandatory_filter_uses_the_shape_family3_enforces():
    c = structural_candidate(profile(caller_invariants=[
        {"param": "owner", "equals": "caller.user_id", "holds": 20, "observed": 20}]))
    assert c.mandatory_filters == ["owner = caller.user_id"]


def test_prompt_carries_counts_but_never_values():
    """Argument VALUES and result rows are the customer's data. The model needs
    the shape of the behaviour, which names and frequencies convey entirely."""
    c = structural_candidate(profile(contested=[{"check_id": "param_taint", "sessions": 4, "findings": 9}]))
    p = render_prompt(c)
    assert "INTEGRITY VIOLATIONS" in p and "param_taint" in p
    assert "20 sessions" in p and "arguments: id" in p
    assert "s1" not in p.split("example")[0].replace("sessions", "")


def test_llm_may_name_the_operation_but_not_rename_the_tool():
    """tool_name is the join key to the profile, the traces and the runtime. A
    model that could change it could silently detach a candidate from its
    evidence."""
    llm = _LLM({"intent": "read_thing", "description": "d",
                "tool_name": "SOMETHING_ELSE",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    out, err = infer_policy(structural_candidate(profile()), llm)
    assert err is None
    assert out.intent == "read_thing" and out.tool_name == "t"


def test_malformed_model_output_degrades_to_the_counted_half():
    """A naming failure must not cost the structural candidate: those fields
    are correct, reviewable, and were produced without the model."""
    out, err = infer_policy(structural_candidate(profile()), _LLM("not json at all"))
    assert err and "not valid JSON" in err
    assert out.tool_name == "t" and out.fields == ["a"]      # intact
    assert out.inferred_policy is None


def test_a_rejected_candidate_is_still_returned_with_its_reason():
    cands, rejected = mine_intents([profile()], llm=_LLM("]["), min_sessions=1)
    assert len(cands) == 1 and rejected and "not valid JSON" in rejected[0]


def test_min_sessions_drops_with_a_reason_never_silently():
    cands, rejected = mine_intents([profile(sessions=1)], llm=None, min_sessions=5)
    assert cands == [] and "below min_sessions=5" in rejected[0]


def test_nothing_is_ever_auto_approved():
    for c, _ in [(structural_candidate(profile()), None),
                 infer_policy(structural_candidate(profile()),
                              _LLM({"intent": "x", "policy": {"statement": "s", "rationale": "r"}}))]:
        assert c.review_status == "pending"
