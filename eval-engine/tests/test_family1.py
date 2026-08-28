from evalengine.family1 import content, predicate, temporal
from evalengine.family1.compilepack import Rule, RulePack

from .helpers import make_ctx, make_session, make_step, make_turn


def _pack(*rules):
    return RulePack(version="1", source_skill="s", source_skill_version="1", rules=tuple(rules))


# --- Rule.check_id() defaulting/override ---------------------------------------

def test_check_id_defaults_by_engine():
    assert Rule(rule_id="r", engine="temporal", effect="block").check_id() == "precondition"
    assert Rule(rule_id="r", engine="content", effect="block").check_id() == "field_restriction"
    assert Rule(rule_id="r", engine="predicate", effect="block").check_id() == "prohibition"


def test_check_id_predicate_with_approver_roles_defaults_approval_gate():
    rule = Rule(rule_id="r", engine="predicate", effect="approval_required", approver_roles=("Manager",))
    assert rule.check_id() == "approval_gate"


def test_check_id_explicit_override_wins():
    rule = Rule(rule_id="r", engine="temporal", effect="block", check="sequencing")
    assert rule.check_id() == "sequencing"


# --- predicate: prohibition shape (no approver_roles) -------------------------

def test_predicate_prohibition_fires_is_violated():
    rule = Rule(rule_id="R1", engine="predicate", effect="block",
               conditions=({"field": "region", "operator": "!=", "value": "US"},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_account", args={"region": "EU"}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert len(verdicts) == 1 and verdicts[0].status == "violated" and verdicts[0].rule_id == "R1"


def test_predicate_prohibition_does_not_fire_is_satisfied():
    rule = Rule(rule_id="R1", engine="predicate", effect="block",
               conditions=({"field": "region", "operator": "!=", "value": "US"},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_account", args={"region": "US"}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "satisfied"


def test_predicate_missing_symbol_is_indeterminate():
    rule = Rule(rule_id="R1", engine="predicate", effect="block",
               conditions=({"field": "region", "operator": "!=", "value": "US"},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_account", args={}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "indeterminate"
    assert verdicts[0].missing_capture == ""  # a plain missing fact, not a coverage gap


def test_predicate_correlates_facts_across_a_bridging_id():
    rule = Rule(rule_id="R5", engine="predicate", effect="block",
               conditions=({"field": "score", "operator": "<", "value": 580},),
               source={"document": "d", "text": "t"})
    get_app = make_step(0, "get_application", args={"loan_id": 7002},
                        result={"loan_id": 7002, "applicant_id": 5002}, turn_seq=0)
    get_credit = make_step(1, "get_credit_report", args={"applicant_id": 5002},
                           result={"score": 540}, turn_seq=0)
    decide = make_step(2, "decide_loan", args={"loan_id": 7002, "decision": "approved"}, turn_seq=0)
    session = make_session(steps=[get_app, get_credit, decide])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    decide_verdicts = [v for v in verdicts if "decide_loan" in v.evidence.excerpt]
    assert decide_verdicts[0].status == "violated"


def test_predicate_does_not_correlate_unrelated_ids():
    rule = Rule(rule_id="R5", engine="predicate", effect="block",
               conditions=({"field": "score", "operator": "<", "value": 580},),
               source={"document": "d", "text": "t"})
    get_credit = make_step(0, "get_credit_report", args={"applicant_id": 9999},
                           result={"score": 540}, turn_seq=0)
    decide = make_step(1, "decide_loan", args={"loan_id": 7002, "decision": "approved"}, turn_seq=0)
    session = make_session(steps=[get_credit, decide])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    decide_verdicts = [v for v in verdicts if "decide_loan" in v.evidence.excerpt]
    assert decide_verdicts[0].status == "indeterminate"


def test_predicate_exists_operator_fires_when_field_present():
    rule = Rule(rule_id="R6", engine="predicate", effect="block",
               conditions=({"field": "internal_risk_score", "operator": "exists", "value": True},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_risk_profile", result={"internal_risk_score": 22}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "violated"


def test_predicate_absent_field_is_satisfied_not_indeterminate():
    rule = Rule(rule_id="R6", engine="predicate", effect="block",
               conditions=({"field": "internal_risk_score", "operator": "exists", "value": True},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_risk_profile", result={"tier": "prime"}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "satisfied"


def test_predicate_value_field_metric_comparison():
    rule = Rule(rule_id="R7", engine="predicate", effect="block",
               conditions=({"field": "requested_amount", "operator": ">",
                          "value_field": "verified_income", "value_multiplier": 5},),
               source={"document": "d", "text": "t"})
    income = make_step(0, "get_income_verification", result={"verified_income": 45000}, turn_seq=0)
    decide = make_step(1, "decide_loan", args={"applicant_id": 1, "requested_amount": 300000}, turn_seq=0)
    income_correlated = income.__class__(**{**income.__dict__, "args": {"applicant_id": 1}})
    decide_correlated = decide.__class__(**{**decide.__dict__, "args": {"applicant_id": 1, "requested_amount": 300000}})
    session = make_session(steps=[income_correlated, decide_correlated])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    decide_verdicts = [v for v in verdicts if "decide_loan" in v.evidence.excerpt]
    assert decide_verdicts[0].status == "violated"


def test_predicate_scoped_to_applies_to_intents():
    rule = Rule(rule_id="R1", engine="predicate", effect="block",
               conditions=({"field": "region", "operator": "!=", "value": "US"},),
               applies_to_intents=("quote_loan",), source={"document": "d", "text": "t"})
    step = make_step(0, "unrelated_tool", args={"region": "EU"}, turn_seq=0)
    session = make_session(steps=[step])
    assert predicate.evaluate(session, _pack(rule), make_ctx(session)) == []


# --- predicate: approval-gate shape (approver_roles set) ----------------------

def test_predicate_approval_gate_fires_without_approval_is_indeterminate():
    rule = Rule(rule_id="R2", engine="predicate", effect="approval_required",
               conditions=({"field": "amount", "operator": ">", "value": 50000},),
               approver_roles=("Branch Manager",), source={"document": "d", "text": "t"})
    step = make_step(0, "quote_loan", args={"amount": 60000}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "indeterminate"
    assert verdicts[0].missing_capture == "approval_events"


def test_predicate_approval_gate_fires_with_approval_is_satisfied():
    rule = Rule(rule_id="R2", engine="predicate", effect="approval_required",
               conditions=({"field": "amount", "operator": ">", "value": 50000},),
               approver_roles=("Branch Manager",), source={"document": "d", "text": "t"})
    approve = make_step(0, "approve_loan", turn_seq=0)
    quote = make_step(1, "quote_loan", args={"amount": 60000}, turn_seq=0)
    session = make_session(steps=[approve, quote])
    verdicts = predicate.evaluate(session, _pack(rule), make_ctx(session))
    quote_verdicts = [v for v in verdicts if "quote_loan" in v.evidence.excerpt]
    assert quote_verdicts[0].status == "satisfied"


# --- content --------------------------------------------------------------------

def test_content_restricted_field_in_result_is_violated():
    rule = Rule(rule_id="R3", engine="content", effect="block",
               detectors=({"field_names": ["risk_score"], "scopes": ["result"]},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_profile", result={"risk_score": 700}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = content.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "violated"


def test_content_clean_result_is_satisfied():
    rule = Rule(rule_id="R3", engine="content", effect="block",
               detectors=({"field_names": ["risk_score"], "scopes": ["result"]},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_profile", result={"name": "ok"}, turn_seq=0)
    session = make_session(steps=[step])
    verdicts = content.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "satisfied"


def test_content_restricted_field_in_final_answer_is_violated():
    rule = Rule(rule_id="R3", engine="content", effect="block",
               detectors=({"field_names": ["risk_score"], "scopes": ["final_answer"]},),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_profile", result={}, turn_seq=0)
    turn = make_turn(0, assistant_message="Your risk_score is 700.")
    session = make_session(steps=[step], turns=[turn])
    verdicts = content.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "violated"


def test_content_restricted_from_roles_scopes_the_detector():
    rule = Rule(rule_id="R3b", engine="content", effect="block",
               detectors=({"field_names": ["credit_score"], "scopes": ["result"]},),
               restricted_from_roles=("Loan Officer",),
               source={"document": "d", "text": "t"})
    step = make_step(0, "get_applicant_profile", result={"credit_score": 700}, turn_seq=0)

    officer_session = make_session(steps=[step], caller_role="Loan Officer")
    underwriter_session = make_session(steps=[step], caller_role="Underwriter")
    assert content.evaluate(officer_session, _pack(rule), make_ctx(officer_session))[0].status == "violated"
    assert content.evaluate(underwriter_session, _pack(rule), make_ctx(underwriter_session)) == []


# --- temporal --------------------------------------------------------------------

def test_temporal_precondition_missing_is_violated():
    rule = Rule(rule_id="R4", engine="temporal", effect="block",
               automaton={"before": {"intent": "quote_discount"}, "requires_fact": "check_risk_profile"},
               source={"document": "d", "text": "t"})
    step = make_step(0, "quote_discount", turn_seq=0)
    session = make_session(steps=[step])
    verdicts = temporal.evaluate(session, _pack(rule), make_ctx(session))
    assert verdicts[0].status == "violated"


def test_temporal_precondition_established_is_satisfied():
    rule = Rule(rule_id="R4", engine="temporal", effect="block",
               automaton={"before": {"intent": "quote_discount"}, "requires_fact": "check_risk_profile"},
               source={"document": "d", "text": "t"})
    check = make_step(0, "check_risk_profile", turn_seq=0)
    quote = make_step(1, "quote_discount", turn_seq=0)
    session = make_session(steps=[check, quote])
    verdicts = temporal.evaluate(session, _pack(rule), make_ctx(session))
    quote_verdicts = [v for v in verdicts if "quote_discount" in v.evidence.excerpt]
    assert quote_verdicts[0].status == "satisfied"


def test_temporal_before_intents_list_covers_multiple_targets():
    rule = Rule(rule_id="R4b", engine="temporal", effect="block",
               automaton={"before": {"intents": ["quote_terms", "apply_discount"]}, "requires_fact": "verify_kyc"},
               source={"document": "d", "text": "t"})
    quote = make_step(0, "quote_terms", turn_seq=0)
    discount = make_step(1, "apply_discount", turn_seq=0)
    session = make_session(steps=[quote, discount])
    verdicts = temporal.evaluate(session, _pack(rule), make_ctx(session))
    assert {v.evidence.excerpt.split(":")[0]: v.status for v in verdicts} == {
        "quote_terms": "violated", "apply_discount": "violated",
    }
