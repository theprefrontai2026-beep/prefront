"""Rule-pack compiler: approved rules -> eval-engine's rule_pack.yaml."""

from __future__ import annotations

import pytest

from skillbuilder.rulepack import CompileError, compile_rule_pack
from skillbuilder.schema import CandidateRule, Clause


def _clause(cid="c1", text="Amounts above 50000 require Branch Manager sign-off.", section="10.2"):
    return Clause(clause_id=cid, document_id="loan_policy", section_path=section,
                  clause_type="approval_threshold", source_text=text)


def _rule(**kw):
    base = dict(
        rule_key="r_approval", rule_type="approval_threshold",
        conditions=[{"field": "amount", "operator": ">", "value": 50000}],
        effect={"decision": "approval_required", "approver_role": "Branch Manager"},
        applies_to_intents=["quote_loan"],
        source_clause_id="c1", source_evidence="Amounts above 50000",
        review_status="approved",
    )
    base.update(kw)
    return CandidateRule.model_validate(base)


def _compiled(pack: dict, rule_key: str) -> dict:
    return next(r for r in pack["rule_pack"]["rules"] if r["rule_id"] == rule_key)


def test_approval_threshold_lowers_to_predicate():
    pack = compile_rule_pack("skill1", "1.0", [_rule()], {"c1": _clause()})
    r = _compiled(pack, "r_approval")
    assert r["engine"] == "predicate"
    assert r["check"] == "approval_gate"
    assert r["effect"] == "approval_required"
    assert r["approver_roles"] == ["Branch Manager"]
    assert r["conditions"] == [{"field": "amount", "operator": ">", "value": 50000}]
    assert r["source"]["text"].startswith("Amounts above")


def test_data_access_lowers_to_content():
    rule = _rule(
        rule_key="r_mask", rule_type="data_access",
        conditions=[{"field": "channel", "operator": "==", "value": "portal"}],
        effect={"decision": "mask", "restricted_fields": ["risk_score", "tax_id"]},
    )
    pack = compile_rule_pack("skill1", "1.0", [rule], {"c1": _clause()})
    r = _compiled(pack, "r_mask")
    assert r["engine"] == "content"
    assert r["check"] == "field_restriction"
    assert r["effect"] == "block"  # mask -> block (no runtime masking in OOB shadow eval)
    assert r["detectors"] == [{"field_names": ["risk_score", "tax_id"], "scopes": ["result", "final_answer"]}]


def test_restriction_lowers_to_predicate_prohibition():
    rule = _rule(rule_key="r_restrict", rule_type="restriction",
                conditions=[{"field": "region", "operator": "!=", "value": "US"}],
                effect={"decision": "block"})
    pack = compile_rule_pack("skill1", "1.0", [rule], {"c1": _clause()})
    r = _compiled(pack, "r_restrict")
    assert r["engine"] == "predicate"
    assert r["check"] == "prohibition"
    assert r["effect"] == "block"
    assert "approver_roles" not in r


def test_unlowerable_rule_types_are_rejected_not_dropped():
    exc = _rule(rule_key="r_exc", rule_type="exception", effect={"decision": "allow"})
    audit = _rule(rule_key="r_audit", rule_type="audit_requirement", effect={"decision": "allow"})
    pack = compile_rule_pack("skill1", "1.0", [exc, audit], {"c1": _clause()})
    rejected = {r["rule_key"]: r for r in pack["rule_pack"]["rejected"]}
    assert set(rejected) == {"r_exc", "r_audit"}
    assert rejected["r_exc"]["reason"]
    assert not pack["rule_pack"]["rules"]


def test_missing_source_citation_is_a_compile_error():
    rule = _rule(source_clause_id="missing")
    with pytest.raises(CompileError):
        compile_rule_pack("skill1", "1.0", [rule], {"c1": _clause()})


def test_only_approved_rules_are_compiled():
    approved = _rule(rule_key="r_ok", review_status="approved")
    draft = _rule(rule_key="r_draft", review_status="pending")
    pack = compile_rule_pack("skill1", "1.0", [approved, draft], {"c1": _clause()})
    ids = {r["rule_id"] for r in pack["rule_pack"]["rules"]}
    assert ids == {"r_ok"}
