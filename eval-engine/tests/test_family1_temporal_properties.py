"""Property-based tests for family1/temporal.py's precondition automaton,
against GENERATED step streams (autonomous_build.md step 10: "property-test
automata against generated step streams" - the one piece of step 10 the
hand-picked example tests in test_family1.py don't cover).

The invariant under test: for a rule `before: {intent: T}, requires_fact: F`,
a step whose intent matches T is "satisfied" if and only if some EARLIER
step in the session (lower seq) has intent == F - for ANY step stream, not
just the couple of hand-picked orderings test_family1.py exercises.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from evalengine.family1 import temporal
from evalengine.family1.compilepack import Rule, RulePack

from .helpers import make_ctx, make_session, make_step

ALPHABET = ("A", "B", "C", "D")


def _pack(rule: Rule) -> RulePack:
    return RulePack(version="1", source_skill="s", source_skill_version="1", rules=(rule,))


def _session_for(intents: list[str]):
    steps = [make_step(i, name, turn_seq=0) for i, name in enumerate(intents)]
    return make_session(steps=steps), steps


@given(
    intents=st.lists(st.sampled_from(ALPHABET), min_size=1, max_size=8),
    target=st.sampled_from(ALPHABET),
    fact=st.sampled_from(ALPHABET),
)
@settings(max_examples=200)
def test_precondition_satisfied_iff_fact_established_earlier(intents, target, fact):
    rule = Rule(rule_id="R", engine="temporal", effect="block",
               automaton={"before": {"intent": target}, "requires_fact": fact},
               source={"document": "d", "text": "t"})
    session, steps = _session_for(intents)
    verdicts = temporal.evaluate(session, _pack(rule), make_ctx(session))
    by_seq = {v.evidence.span_ids[0]: v.status for v in verdicts}

    for step in steps:
        if step.intent != target:
            assert step.span_id not in by_seq, "a non-target step must get no verdict (Hard Rule 16)"
            continue
        expected = "satisfied" if any(
            s.intent == fact for s in steps if s.seq < step.seq
        ) else "violated"
        assert by_seq[step.span_id] == expected, (
            f"intents={intents} target={target} fact={fact} step={step.seq}: "
            f"expected {expected}, got {by_seq[step.span_id]}"
        )


@given(intents=st.lists(st.sampled_from(ALPHABET), min_size=1, max_size=8), fact=st.sampled_from(ALPHABET))
@settings(max_examples=100)
def test_wildcard_target_checks_every_step(intents, fact):
    rule = Rule(rule_id="R", engine="temporal", effect="block",
               automaton={"before": {"intent": "*"}, "requires_fact": fact},
               source={"document": "d", "text": "t"})
    session, steps = _session_for(intents)
    verdicts = temporal.evaluate(session, _pack(rule), make_ctx(session))
    assert len(verdicts) == len(steps), "'*' must produce exactly one verdict per step"


@given(
    intents=st.lists(st.sampled_from(ALPHABET), min_size=2, max_size=8),
    target=st.sampled_from(ALPHABET),
    fact=st.sampled_from(ALPHABET),
)
@settings(max_examples=100)
def test_reordering_a_fact_step_earlier_never_turns_satisfied_into_violated(intents, target, fact):
    """Monotonicity: moving an established-fact step EARLIER in the stream
    can only ever help a later target step, never hurt it."""
    if target == fact:
        return  # prepending a fact step would also add a new target step - not the property under test
    rule = Rule(rule_id="R", engine="temporal", effect="block",
               automaton={"before": {"intent": target}, "requires_fact": fact},
               source={"document": "d", "text": "t"})
    session, steps = _session_for(intents)
    before = {v.evidence.span_ids[0]: v.status for v in temporal.evaluate(session, _pack(rule), make_ctx(session))}

    moved = [fact] + intents  # prepend an unambiguous fact-establishing step
    session2, steps2 = _session_for(moved)
    after = {v.evidence.span_ids[0]: v.status for v in temporal.evaluate(session2, _pack(rule), make_ctx(session2))}

    # Every target step in the original stream must be satisfied in the
    # moved stream too (its span_id shifts by one position, so compare by
    # original intent-order index instead of span_id).
    orig_target_idx = [i for i, name in enumerate(intents) if name == target]
    new_target_idx = [i for i, name in enumerate(moved) if name == target]
    assert len(orig_target_idx) == len(new_target_idx)
    for oi, ni in zip(orig_target_idx, new_target_idx):
        orig_status = before[f"step-{oi}"]
        new_status = after[f"step-{ni}"]
        if orig_status == "satisfied":
            assert new_status == "satisfied"
