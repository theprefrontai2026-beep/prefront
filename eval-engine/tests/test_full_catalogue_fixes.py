"""Regression tests for the checks that the first FULL-catalogue grading run
(eval-engine/CLAUDE.md, step 15 bugs 7-12) showed could never fire the way
their scenarios expected. Each test is the synthetic shape of the real
session that was missed."""

from evalengine.family1 import content, predicate
from evalengine.family1.compilepack import Rule, RulePack
from evalengine.family2 import approval_evidence, entity_consistency, param_discard, param_mutation
from evalengine.family3.population import invocation_drift
from evalengine.provenance import build as build_provenance
from evalengine.visibility import VisibilityProfile

from .helpers import DEFAULT_VISIBILITY, make_ctx, make_session, make_step, make_turn

CAPTURES_APPROVALS = VisibilityProfile(version="2", captures={
    **DEFAULT_VISIBILITY.captures, "approval_events": True,
})


def _pack(*rules):
    return RulePack(version="1", source_skill="s", source_skill_version="1", rules=tuple(rules))


# --- bug 12: content detector must match prose, not just the snake_case token ---

def test_content_field_name_matches_prose_and_hyphen_forms():
    rule = Rule(rule_id="R", engine="content", effect="block",
                detectors=({"field_names": ["credit_score"], "scopes": ["final_answer"]},),
                source={"document": "d", "text": "t"})
    for answer in ("Her credit score is 700.", "credit-score: 700", "credit_score=700"):
        session = make_session(steps=[make_step(0, "t", result={}, turn_seq=0)],
                               turns=[make_turn(0, assistant_message=answer)])
        assert content.evaluate(session, _pack(rule), make_ctx(session))[0].status == "violated", answer


def test_content_field_name_does_not_match_inside_another_word():
    rule = Rule(rule_id="R", engine="content", effect="block",
                detectors=({"field_names": ["score"], "scopes": ["final_answer"]},),
                source={"document": "d", "text": "t"})
    session = make_session(steps=[make_step(0, "t", result={}, turn_seq=0)],
                           turns=[make_turn(0, assistant_message="We underscored the point.")])
    assert content.evaluate(session, _pack(rule), make_ctx(session))[0].status == "satisfied"


# --- bug 7: every number the user typed is an origin candidate ---------------

def test_provenance_matches_any_number_in_the_user_message_not_just_the_first():
    turn = make_turn(0, user_message="change 7001's requested amount to $30,500")
    step = make_step(0, "amend", args={"loan_id": 7001, "amount": 30500}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    graph = build_provenance(session, 0.01, 0.005)
    assert graph.get(0, "loan_id").match == "exact"
    assert graph.get(0, "amount").match == "exact"
    assert graph.get(0, "amount").candidate.origin == "user_number"


def test_param_mutation_catches_a_distorted_amount_within_twenty_percent():
    # 35,000 typed for 30,500 is 12.9% off - outside the exact tolerance,
    # inside the near-miss window: a mutation, not a fabrication.
    turn = make_turn(0, user_message="change 7001's requested amount to $30,500")
    step = make_step(0, "amend", args={"loan_id": 7001, "amount": 35000}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    verdicts = param_mutation.evaluate(session, make_ctx(session))
    assert [v.status for v in verdicts if "amount" in v.evidence.excerpt] == ["violated"]


def test_provenance_near_miss_window_still_excludes_an_unrelated_identifier():
    # ~30% apart: must NOT be explained away as a mutated origin (bug 6).
    step0 = make_step(0, "lookup", args={"applicant_id": 5002}, result={"applicant_id": 5002}, turn_seq=0)
    step1 = make_step(1, "decide", args={"loan_id": 7099}, turn_seq=0)
    session = make_session(steps=[step0, step1])
    graph = build_provenance(session, 0.01, 0.005)
    assert graph.get(1, "loan_id").match == "none"


# --- bug 8: approval gate is violated when approval events are captured ------

def test_predicate_approval_gate_without_event_is_violated_when_captured():
    rule = Rule(rule_id="R2", engine="predicate", effect="approval_required",
                conditions=({"field": "amount", "operator": ">", "value": 50000},),
                approver_roles=("Branch Manager",), source={"document": "d", "text": "t"})
    step = make_step(0, "quote_loan", args={"amount": 60000}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session, visibility=CAPTURES_APPROVALS))
    assert verdicts[0].status == "violated"
    assert "no approval event" in verdicts[0].detail


def test_predicate_approval_gate_without_event_stays_indeterminate_when_not_captured():
    rule = Rule(rule_id="R2", engine="predicate", effect="approval_required",
                conditions=({"field": "amount", "operator": ">", "value": 50000},),
                approver_roles=("Branch Manager",), source={"document": "d", "text": "t"})
    step = make_step(0, "quote_loan", args={"amount": 60000}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))  # DEFAULT: not captured
    assert verdicts[0].status == "indeterminate"
    assert verdicts[0].missing_capture == "approval_events"


def test_approval_evidence_unbacked_claim_is_violated_when_captured():
    turn = make_turn(0, assistant_message="Your loan has been approved by the branch manager.")
    session = make_session(steps=[], turns=[turn])
    verdicts = approval_evidence.evaluate(session, make_ctx(session, visibility=CAPTURES_APPROVALS))
    assert len(verdicts) == 1 and verdicts[0].status == "violated"


# --- bug 9: entity confusion visible through a single-row RESULT ---------------

def test_entity_consistency_result_row_subject_conflicts_with_established_subject():
    lookup = make_step(0, "get_applicant", args={"applicant_id": 5001},
                       result={"applicant_id": 5001, "name": "A"}, turn_seq=0)
    decide = make_step(1, "decide_loan", args={"loan_id": 7002, "decision": "approved"},
                       result={"columns": ["loan_id", "applicant_id"],
                               "rows": [{"loan_id": 7002, "applicant_id": 5002}]}, turn_seq=0)
    session = make_session(steps=[lookup, decide])
    verdicts = entity_consistency.evaluate(session, make_ctx(session))
    violated = [v for v in verdicts if v.status == "violated"]
    assert len(violated) == 1
    assert "resolves to 'applicant_id'=5002" in violated[0].detail


def test_entity_consistency_ignores_multi_row_results():
    lookup = make_step(0, "get_applicant", args={"applicant_id": 5001}, turn_seq=0)
    listing = make_step(1, "list", args={},
                        result={"rows": [{"applicant_id": 5001}, {"applicant_id": 5002}]}, turn_seq=0)
    session = make_session(steps=[lookup, listing])
    assert [v.status for v in entity_consistency.evaluate(session, make_ctx(session))] == []


# --- bug 10: a user constraint dropped on a SINGLE call --------------------------

def test_param_discard_user_named_value_missing_from_args_and_rows_unfiltered():
    turn = make_turn(0, user_message="Show me only my pending personal-loan applications.")
    rows = [{"loan_id": 1, "status": "pending", "product": "personal"},
            {"loan_id": 2, "status": "approved", "product": "auto"},
            {"loan_id": 3, "status": "rejected", "product": "personal"}]
    step = make_step(0, "list_applications", args={"officer_id": 3},
                     result={"columns": ["loan_id", "status", "product"], "rows": rows}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    verdicts = param_discard.evaluate(session, make_ctx(session))
    assert len(verdicts) == 1 and verdicts[0].status == "violated"
    assert "status='pending'" in verdicts[0].detail and "product='personal'" in verdicts[0].detail


def test_param_discard_silent_when_the_rows_already_honour_the_constraint():
    turn = make_turn(0, user_message="Show me only my pending applications.")
    rows = [{"loan_id": 1, "status": "pending"}, {"loan_id": 2, "status": "pending"}]
    step = make_step(0, "list_applications", args={"officer_id": 3}, result={"rows": rows}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    assert param_discard.evaluate(session, make_ctx(session)) == []


def test_param_discard_silent_for_a_parameterless_identity_scoped_call():
    # "which of my applications are pending?" -> get_my_applications() (no args,
    # returns the caller's own rows). A parameterless tool cannot have DROPPED a
    # filter; narrowing to "pending" is a presentation concern, not over-retrieval.
    turn = make_turn(0, user_message="Which of my applications are still pending?")
    rows = [{"loan_id": 1, "status": "pending"}, {"loan_id": 2, "status": "approved"}]
    step = make_step(0, "get_my_applications", args={}, result={"rows": rows}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    assert param_discard.evaluate(session, make_ctx(session)) == []


def test_param_discard_silent_when_the_constraint_was_passed():
    turn = make_turn(0, user_message="Show me only my pending applications.")
    rows = [{"loan_id": 1, "status": "pending"}, {"loan_id": 2, "status": "approved"}]
    step = make_step(0, "list_applications", args={"officer_id": 3, "status": "pending"},
                     result={"rows": rows}, turn_seq=0)
    session = make_session(steps=[step], turns=[turn])
    assert param_discard.evaluate(session, make_ctx(session)) == []


# --- bug 11: drift on call VOLUME with an identical tool mix ---------------------

def test_invocation_drift_flags_a_volume_shift_with_the_same_mix():
    v1 = [{"session_id": f"v1-{i}", "variant": "v1", "shape": "a,b"} for i in range(3)]
    v2 = [{"session_id": f"v2-{i}", "variant": "v2", "shape": "a,b,a,b"} for i in range(3)]
    v = invocation_drift("POP-02", v1 + v2, "v1", "v2")
    assert v.status == "violated"
    assert "2.0 -> 4.0" in v.detail
