from evalengine.family1 import content, predicate, temporal
from evalengine.family1.compilepack import Rule, RulePack

from .helpers import make_ctx, make_session, make_step, make_turn


def _pack(*rules):
    return RulePack(version="1", source_skill="s", source_skill_version="1", rules=tuple(rules))


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
