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


# ── multi-call intents ────────────────────────────────────────────────────

def flow(**over):
    base = {"steps": ["a", "b", "c"], "sessions": 20, "occurrences": 25, "coverage": 0.8,
            "roles": [{"value": "Agent", "sessions": 20, "calls": 25}],
            "contested": [], "example_sessions": ["s1"]}
    base.update(over)
    return base


def test_workflow_structural_half_needs_no_model():
    from semanticlayer.intent_mining import structural_workflow
    c = structural_workflow(flow())
    assert c.steps == ["a", "b", "c"] and c.sessions == 20
    assert c.review_status == "pending" and c.inferred_policy is None
    assert c.warnings == []


def test_low_coverage_is_flagged_as_not_the_norm():
    """The honesty number for a sequence: if most sessions that started this way
    did not finish it, this is one path among several, not 'the' process."""
    from semanticlayer.intent_mining import structural_workflow
    c = structural_workflow(flow(coverage=0.1))
    assert any("low coverage" in w and "one path among several" in w for w in c.warnings)


def test_a_contested_step_contests_the_whole_run():
    """Which step was at fault is exactly what is unknown without a policy."""
    from semanticlayer.intent_mining import structural_workflow
    c = structural_workflow(flow(contested=[{"check_id": "param_taint", "sessions": 5, "findings": 7}]))
    assert any("param_taint" in w for w in c.warnings) and c.contested


def test_workflow_prompt_carries_order_and_coverage():
    """The ORDER is the evidence — what precedes is a candidate precondition,
    what follows a candidate obligation — so it must survive into the prompt."""
    from semanticlayer.intent_mining import render_workflow_prompt, structural_workflow
    p = render_workflow_prompt(structural_workflow(flow()))
    assert "a -> b -> c" in p and "80%" in p


def test_a_run_is_read_as_its_goal_and_what_was_read_first():
    """'a, then b, then c' narrates the order; 'an intent to c, which requires
    a and b' is the reading a reviewer can adopt. The split is counted."""
    from semanticlayer.intent_mining import render_workflow_prompt, structural_workflow
    c = structural_workflow(flow())
    assert (c.goal, c.prerequisites) == ("c", ["a", "b"])
    p = render_workflow_prompt(c)
    assert "goal (the call this run is for): c" in p and "read first in this run: a, b" in p


def test_prerequisite_support_is_counted_across_every_run_reaching_the_goal():
    """'Always requires' is a claim about every run that reached the goal, not
    the one being summarised — which is 100% by construction. A run where the
    goal is mid-way still reached it, and one beyond the cap is still evidence."""
    from semanticlayer.intent_mining import mine_workflows
    llm = _LLM({"intent": "x", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    runs = [flow(steps=["a", "b", "c"], occurrences=6),
            flow(steps=["b", "c", "d"], occurrences=3),   # reaches c mid-way
            flow(steps=["c"], occurrences=1)]             # past the cap below
    cands, _ = mine_workflows(runs, llm=llm, min_sessions=1, limit=1)
    c = cands[0]
    assert c.goal_runs == 10
    assert c.prerequisite_support == [{"step": "b", "runs": 9, "share": 0.9},
                                      {"step": "a", "runs": 6, "share": 0.6}]
    user = llm.prompts[0][1]
    assert "this pattern: 6 runs, 60% of the 10 runs that reached the goal" in user
    assert "across all 10 observed runs that reached the goal" in user
    assert "b: 9 of 10 runs (90%)" in user


def test_a_run_with_nothing_first_is_not_handed_other_patterns_reads():
    """Given the whole list, the model recited other patterns' reads as a
    caveat on a run that reads nothing first. It gets one figure instead."""
    from semanticlayer.intent_mining import mine_workflows
    llm = _LLM({"intent": "x", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    runs = [flow(steps=["c"], occurrences=1),
            flow(steps=["a", "b", "c"], occurrences=6),
            flow(steps=["b", "c", "d"], occurrences=3)]
    mine_workflows(runs, llm=llm, min_sessions=1, limit=1)
    user = llm.prompts[0][1]
    assert "of all 10 runs that reached the goal, 9 read something first" in user
    assert "9 of 10 runs" not in user and "6 of 10 runs" not in user


def test_cross_run_support_without_the_whole_set_is_not_measured():
    """Summarised alone, a run cannot say what else reaches its goal; saying
    100% because this run is all it can see would be a fabricated 'always'."""
    from semanticlayer.intent_mining import render_workflow_prompt, structural_workflow
    c = structural_workflow(flow())
    assert c.goal_runs == 0 and c.prerequisite_support == []
    assert "outside this one: not measured" in render_workflow_prompt(c)


def test_hidden_single_call_runs_still_count_but_are_not_summarised():
    """Hiding a goal called on its own must not remove it from the evidence:
    without it, 'the credit report was read first in 60%' becomes 'always'.
    And a hidden run must not spend the cap a shown one needed."""
    from semanticlayer.intent_mining import mine_workflows
    llm = _LLM({"intent": "x", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    runs = [flow(steps=["c"], occurrences=4),
            flow(steps=["a", "c"], occurrences=6)]
    cands, _ = mine_workflows(runs, llm=llm, min_sessions=1, limit=1, min_steps=2)
    assert [c.steps for c in cands] == [["a", "c"]] and len(llm.prompts) == 1
    assert cands[0].goal_runs == 10
    assert cands[0].prerequisite_support == [{"step": "a", "runs": 6, "share": 0.6}]


def test_a_single_call_run_has_no_prerequisites():
    from semanticlayer.intent_mining import render_workflow_prompt, structural_workflow
    c = structural_workflow(flow(steps=["c"]))
    assert (c.goal, c.prerequisites) == ("c", [])
    assert "called on its own" in render_workflow_prompt(c)


def test_workflow_title_and_description_are_kept():
    """They lead the row on the page; the tool calls move behind a click."""
    from semanticlayer.intent_mining import infer_workflow_policy, structural_workflow
    llm = _LLM({"intent": "assess_risk", "title": "Assess applicant risk",
                "description": "Scores an applicant before a decision.",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    out, err = infer_workflow_policy(structural_workflow(flow()), llm)
    assert err is None
    assert (out.title, out.description) == ("Assess applicant risk", "Scores an applicant before a decision.")
    assert '"title"' in llm.prompts[0][0]


def _goal_runs():
    return [flow(steps=["a", "b", "c"], occurrences=6), flow(steps=["b", "c"], occurrences=3),
            flow(steps=["c"], occurrences=1), flow(steps=["x"], occurrences=9)]


def test_goals_group_every_way_of_reaching_the_same_call():
    """Keyed by the counted terminal call, never the model's title. A goal only
    ever called on its own is hidden under min_steps=2, but a kept goal keeps
    its direct-call variant — it is one of the ways the goal is reached."""
    from semanticlayer.intent_mining import goals_from_workflows
    gs = goals_from_workflows(_goal_runs(), min_steps=2)
    assert [g.goal for g in gs] == ["c"]
    g = gs[0]
    assert g.variant_runs == 10 and [v["runs"] for v in g.variants] == [6, 3, 1]
    assert g.goal_runs == 10
    assert g.prerequisite_support[0] == {"step": "b", "runs": 9, "share": 0.9}


def test_one_model_call_per_goal_not_per_variant():
    from semanticlayer.intent_mining import mine_goal_intents
    llm = _LLM({"intent": "x", "title": "Do the thing", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    out, _ = mine_goal_intents(_goal_runs(), llm=llm, min_sessions=1, min_steps=2)
    assert len(out) == 1 and len(llm.prompts) == 1 and out[0].title == "Do the thing"
    user = llm.prompts[0][1]
    assert "b: 9 of 10 runs (90%)" in user
    assert "a -> b -> the goal   [6 runs]" in user and "directly, nothing read first   [1 runs]" in user


def test_workflow_llm_failure_degrades_to_the_counted_half():
    from semanticlayer.intent_mining import infer_workflow_policy, structural_workflow
    out, err = infer_workflow_policy(structural_workflow(flow()), _LLM("nonsense"))
    assert err and "not valid JSON" in err
    assert out.steps == ["a", "b", "c"] and out.inferred_policy is None


def test_mine_workflows_caps_what_it_sends_to_the_model():
    """A mined corpus yields dozens of overlapping runs; a reviewer handed all
    of them reviews none, and each one costs a model call."""
    from semanticlayer.intent_mining import mine_workflows
    llm = _LLM({"intent": "x", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    cands, _ = mine_workflows([flow() for _ in range(30)], llm=llm, min_sessions=1, limit=4)
    assert len(cands) == 4 and len(llm.prompts) == 4


# ── grouped intents ───────────────────────────────────────────────────────

def group(**over):
    base = {"core_steps": ["a", "b"], "optional_steps": ["c"],
            "variants": [{"steps": ["a", "b"], "sessions": 20, "coverage": 0.8, "occurrences": 20},
                         {"steps": ["a", "b", "c"], "sessions": 9, "coverage": 0.4, "occurrences": 9}],
            "sessions": 20, "roles": [{"value": "Agent", "sessions": 20, "calls": 20}],
            "contested": [], "example_sessions": ["s1"]}
    base.update(over)
    return base


def test_grouped_candidate_keeps_core_and_optional_counted():
    from semanticlayer.intent_mining import structural_group
    c = structural_group(group())
    assert c.core_steps == ["a", "b"] and c.optional_steps == ["c"]
    assert len(c.variants) == 2 and c.review_status == "pending"


def test_a_group_with_no_shared_step_is_flagged_loudly():
    """Should not happen with complete linkage, and is worth shouting about if
    it ever does: variants with nothing in common are not one operation."""
    from semanticlayer.intent_mining import structural_group
    c = structural_group(group(core_steps=[]))
    assert any("may not be one operation" in w for w in c.warnings)


def test_group_prompt_states_the_core_optional_split():
    """It is counted, so the model is told it rather than asked to infer it —
    and told not to contradict it."""
    from semanticlayer.intent_mining import render_group_prompt, structural_group, GROUP_SYSTEM
    p = render_group_prompt(structural_group(group()))
    assert "core steps (in EVERY variant): a -> b" in p
    assert "optional steps (in some): c" in p
    assert "2 observed variant(s)" in p
    assert "do not contradict it" in GROUP_SYSTEM


def test_one_model_call_per_group_not_per_variant():
    """The whole point of grouping: N shapes of one operation cost one call and
    yield one candidate, not N near-identical ones."""
    from semanticlayer.intent_mining import mine_intent_groups
    llm = _LLM({"intent": "x", "description": "d",
                "policy": {"statement": "s", "rationale": "r", "confidence": "low"}})
    cands, _ = mine_intent_groups([group(), group()], llm=llm, min_sessions=1)
    assert len(cands) == 2 and len(llm.prompts) == 2      # 2 groups, 4 variants


def test_grouped_llm_failure_degrades_to_the_counted_half():
    from semanticlayer.intent_mining import infer_group_policy, structural_group
    out, err = infer_group_policy(structural_group(group()), _LLM("{"))
    assert err and out.core_steps == ["a", "b"] and out.inferred_policy is None


# ── access boundaries from cohort contrasts ───────────────────────────────

def cohort(**over):
    base = {"role": "Agent", "sessions": 100, "calls": 300,
            "tools": [{"tool": "read", "sessions": 100, "calls": 300}],
            "exclusive_tools": [], "field_gaps": [], "has_exposure": True,
            "never_used": [
                {"tool": "approve", "used_by": ["Boss"], "this_cohort_sessions": 100,
                 "others_use_rate": 0.4, "silence_by_chance": 0.0, "likely_boundary": True},
                {"tool": "rare", "used_by": ["Boss"], "this_cohort_sessions": 100,
                 "others_use_rate": 0.01, "silence_by_chance": 0.36, "likely_boundary": False}]}
    base.update(over)
    return base


def test_cohort_prompt_separates_evidence_from_noise():
    """A long list of rarely-used tools is not evidence, and its LENGTH is the
    thing most likely to be mistaken for some — so the split is made for the
    model rather than left to it."""
    from semanticlayer.intent_mining import render_cohort_prompt, structural_cohort
    p = render_cohort_prompt(structural_cohort(cohort()))
    assert "UNLIKELY BY CHANCE" in p and "approve" in p.split("INCONCLUSIVE")[0]
    assert "INCONCLUSIVE" in p and "rare" in p.split("INCONCLUSIVE")[1]
    assert "do not infer a restriction from these" in p


def test_no_field_gaps_is_itself_reported():
    """On an ungoverned deployment nothing is withheld from anyone, and that is
    a finding — not an empty section."""
    from semanticlayer.intent_mining import structural_cohort
    c = structural_cohort(cohort())
    assert any("no field-level restriction is being enforced" in w for w in c.warnings)


def test_a_thin_cohort_is_warned_about_not_dropped():
    """A role with three sessions is part of the picture; hiding it would hide
    that the corpus cannot yet say anything about it."""
    from semanticlayer.intent_mining import mine_cohort_policies
    out, _ = mine_cohort_policies([cohort(sessions=3, has_exposure=False)], llm=None)
    assert len(out) == 1
    assert any("too little traffic" in w for w in out[0].warnings)


def test_cohort_prompt_forbids_stating_prohibition_as_fact():
    from semanticlayer.intent_mining import COHORT_SYSTEM
    assert "ABSENCE OF EVIDENCE IS NOT EVIDENCE OF PROHIBITION" in COHORT_SYSTEM
    assert "OBSERVED REACH IS NOT PERMITTED REACH" in COHORT_SYSTEM


def test_cohort_llm_failure_degrades_to_the_counted_half():
    from semanticlayer.intent_mining import infer_cohort_policy, structural_cohort
    out, err = infer_cohort_policy(structural_cohort(cohort()), _LLM("nope"))
    assert err and out.role == "Agent" and out.inferred_policy is None


# ── the rule governing a side-effecting operation ─────────────────────────

def shape(steps, closed, n, **over):
    d = {"steps": steps, "closed_by": closed, "episodes": n, "sessions": n,
         "before_effect": steps[:-1] if closed else [],
         "roles": [{"value": "Agent", "episodes": n}], "subject_args": ["a_id"],
         "example_sessions": ["s1"]}
    d.update(over)
    return d


def test_only_side_effecting_operations_get_a_rule():
    """A read leaves nothing behind for a rule to be about; 'what must be true
    before this is allowed' only arises for an act that changes something."""
    from semanticlayer.intent_mining import operations_from_shapes
    ops = operations_from_shapes([shape(["look"], "", 10), shape(["gather", "act"], "act", 4)])
    assert [o.operation for o in ops] == ["act"]


def test_paths_to_one_operation_are_gathered_together():
    """The comparison IS the evidence: the times evidence was gathered, beside
    the times it was not."""
    from semanticlayer.intent_mining import operations_from_shapes
    o = operations_from_shapes([
        shape(["act"], "act", 196),
        shape(["gather", "act"], "act", 15),
        shape(["other", "act"], "act", 15),
    ])[0]
    assert o.total_episodes == 226 and o.bare_episodes == 196
    assert o.paths[0]["episodes"] == 196          # ranked by frequency
    assert o.bare_share == round(196 / 226, 3)


def test_learning_states_the_fact_monitoring_states_the_concern():
    """The same counted fact, framed by what is known. While a baseline is
    still forming, "performed with nothing preceding it" is a description of
    how the operation is used here. Once a baseline exists, the same number
    raises the question of a bypass. Judging before there is anything to judge
    against manufactures issues out of the absence of a baseline."""
    from semanticlayer.intent_mining import LEARNING, MONITORING, operations_from_shapes
    shapes = [shape(["act"], "act", 90), shape(["gather", "act"], "act", 10)]

    learn = operations_from_shapes(shapes, LEARNING)[0]
    assert any("nothing preceding it" in w for w in learn.warnings)
    assert not any("bypassed" in w for w in learn.warnings)

    watch = operations_from_shapes(shapes, MONITORING)[0]
    assert any("bypassed" in w for w in watch.warnings)


def test_learning_is_the_default_mode():
    """Assuming a baseline that does not exist is the more damaging mistake."""
    from semanticlayer.intent_mining import LEARNING, mode_preamble, operations_from_shapes
    o = operations_from_shapes([shape(["act"], "act", 90)])[0]
    assert not any("bypassed" in w for w in o.warnings)
    assert mode_preamble("anything-unrecognised") == mode_preamble(LEARNING)


def test_the_learning_preamble_forbids_judgement_language():
    """The model is told plainly, because it will otherwise reach for the
    vocabulary of audit — that is what its training rewards."""
    from semanticlayer.intent_mining import LEARNING, MONITORING, mode_preamble
    learn = mode_preamble(LEARNING)
    for word in ("bypass", "gap", "violation", "risk", "control failure"):
        assert word in learn, f"the preamble should name {word!r} as forbidden"
    assert "not judge" in learn
    assert mode_preamble(MONITORING) != learn


def test_a_fully_guarded_operation_raises_no_bare_warning():
    from semanticlayer.intent_mining import operations_from_shapes
    o = operations_from_shapes([shape(["gather", "act"], "act", 40)])[0]
    assert o.bare_episodes == 0
    assert not any("NOTHING" in w for w in o.warnings)


def test_operation_prompt_shows_every_path_with_its_share():
    from semanticlayer.intent_mining import (operations_from_shapes,
                                             render_operation_prompt, OPERATION_SYSTEM)
    o = operations_from_shapes([shape(["act"], "act", 3), shape(["gather", "act"], "act", 1)])[0]
    p = render_operation_prompt(o)
    assert "(nothing preceded it)" in p and "gather" in p and "75%" in p
    assert "FREQUENCY IS NOT LEGITIMACY" in OPERATION_SYSTEM
    assert "rule" in OPERATION_SYSTEM and "habit" in OPERATION_SYSTEM


# ── the model does not run during learning ────────────────────────────────

def test_mine_functions_run_the_counted_half_with_no_model():
    """The guarantee the gate rests on: refusing the model must still leave a
    usable result, or the refusal would just be an outage."""
    from semanticlayer.intent_mining import (mine_cohort_policies, mine_intent_groups,
                                             mine_intents, mine_operation_policies)
    cands, _ = mine_intents([profile()], llm=None, min_sessions=1)
    groups, _ = mine_intent_groups([group()], llm=None, min_sessions=1)
    cos, _ = mine_cohort_policies([cohort()], llm=None)
    ops, _ = mine_operation_policies([shape(["gather", "act"], "act", 5)], llm=None)
    assert cands and groups and cos and ops
    for c in (cands[0], groups[0], cos[0], ops[0]):
        assert c.inferred_policy is None
        assert c.review_status == "pending"
    # ...and the counted structure is all there.
    assert ops[0].paths and ops[0].total_episodes == 5
    assert cands[0].fields == ["a"]


def test_unmeasured_coverage_is_not_reported_as_low():
    """Absent is not zero. A caller with no coverage figure that sends 0 is not
    being conservative — it asserts almost nobody who started the run finished
    it, and the model duly reports a process that is not the norm. That is a
    fabricated finding, and it reached a screenshot before it was caught."""
    from semanticlayer.intent_mining import render_workflow_prompt, structural_workflow
    w = flow(); w.pop("coverage")
    c = structural_workflow(w)
    assert c.coverage is None
    assert not any("low coverage" in x for x in c.warnings)
    assert "not measured" in render_workflow_prompt(c)


def test_a_real_zero_coverage_is_still_reported():
    """The distinction only helps if a measured zero still warns."""
    from semanticlayer.intent_mining import structural_workflow
    c = structural_workflow(flow(coverage=0.0))
    assert c.coverage == 0.0
    assert any("low coverage" in x for x in c.warnings)
