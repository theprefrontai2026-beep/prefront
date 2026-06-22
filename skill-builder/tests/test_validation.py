"""Validation engine — especially the executability binding pre-check."""

from __future__ import annotations

from skillbuilder.domain_packs import load_pack
from skillbuilder.schema import CandidateRule, Clause
from skillbuilder.validation import run_all

PACK = load_pack("credit_collections")


def _clause(cid="c1", text="on hold"):
    return Clause(clause_id=cid, document_id="d1", section_path="4.1",
                  clause_type="restriction", source_text=text)


def _rule(**kw):
    base = dict(
        rule_key="r1", rule_type="restriction",
        conditions=[{"field": "credit_status", "operator": "==", "value": "hold"}],
        effect={"decision": "block", "message": "x"},
        applies_to_intents=["create_order"],
        source_clause_id="c1", source_evidence="on hold",
    )
    base.update(kw)
    return CandidateRule.model_validate(base)


def _result(report, rule_key):
    return next(r for r in report.rule_results if r.rule_key == rule_key)


def test_clean_rule_is_executable():
    rep = run_all([_rule()], [_clause()], pack=PACK)
    rv = _result(rep, "r1")
    assert rv.executable and rv.source_grounded and rv.semantic_valid


def test_unmappable_symbol_flagged():
    rule = _rule(conditions=[{"field": "customer_tier", "operator": "==", "value": "gold"}])
    rep = run_all([rule], [_clause()], pack=PACK)
    assert not _result(rep, "r1").executable
    assert any(u.type == "unmappable_symbol" for u in rep.unresolved_items)


def test_request_param_and_metric_resolve():
    rule = _rule(conditions=[
        {"field": "order_value", "operator": ">", "value": 50000},
        {"field": "available_credit", "operator": "<", "value": "order_value"},
    ])
    assert _result(run_all([rule], [_clause()], pack=PACK), "r1").executable


def test_no_intent_is_not_executable():
    rep = run_all([_rule(applies_to_intents=[])], [_clause()], pack=PACK)
    assert not _result(rep, "r1").executable


def test_left_side_arithmetic_rejected():
    rule = _rule(conditions=[
        {"field": "credit_limit - current_balance", "operator": ">", "value": 0},
    ])
    rep = run_all([rule], [_clause()], pack=PACK)
    assert not _result(rep, "r1").executable
    assert any(u.type == "non_executable_language" for u in rep.unresolved_items)


def test_unknown_approver_role_is_semantic_failure():
    rule = _rule(rule_type="approval_threshold",
                 effect={"decision": "approval_required", "approver_role": "Wizard"})
    rep = run_all([rule], [_clause()], pack=PACK)
    assert not _result(rep, "r1").semantic_valid
    assert any(u.type == "unknown_role" for u in rep.unresolved_items)


def test_publishable_requires_approval():
    rep = run_all([_rule(review_status="approved")], [_clause()], pack=PACK)
    assert _result(rep, "r1").publishable
    rep2 = run_all([_rule(review_status="pending")], [_clause()], pack=PACK)
    assert not _result(rep2, "r1").publishable
    assert "REVIEW_NOT_APPROVED" in _result(rep2, "r1").publish_blockers
