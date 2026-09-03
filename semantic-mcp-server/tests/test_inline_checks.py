"""governance/inline_checks.py - the safe, single-call subset of eval-engine
reused inline (autonomous_build.md step 18). Pure-Python, no DB/Docker: an
intent_catalog.yaml / rule_pack.yaml fixture on disk, no network.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from semanticmcp.governance import inline_checks


CATALOG_YAML = textwrap.dedent("""
    intent_catalog:
      version: 1
      intents:
        - intent: view_account
          side_effect: read
          params: [account_id]
          allowed_callers: {roles: [Teller], channels: []}
          fields: [account_id, balance]
""")

RULE_PACK_YAML = textwrap.dedent("""
    rule_pack:
      version: 1
      source_skill: test
      source_skill_version: "1"
      rules:
        - rule_id: R-SSN
          engine: content
          check: field_restriction
          effect: block
          detectors:
            - field_names: [ssn]
              scopes: [result]
          source: {document: d, section: s, page: 1, text: t}
      rejected: []
""")


@pytest.fixture(autouse=True)
def _reset():
    inline_checks.reload()
    yield
    inline_checks.reload()


@pytest.fixture
def catalog_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "intent_catalog.yaml"
    p.write_text(CATALOG_YAML)
    monkeypatch.setattr(inline_checks, "INTENT_CATALOG_PATH", str(p))
    return p


@pytest.fixture
def rule_pack_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "rule_pack.yaml"
    p.write_text(RULE_PACK_YAML)
    monkeypatch.setattr(inline_checks, "RULE_PACK_PATH", str(p))
    return p


def test_unconfigured_is_always_allow():
    effect, verdicts = inline_checks.evaluate_pre_execution("x", "x", {}, "Role", "chan", "read")
    assert effect == "allow" and verdicts == []
    effect2, verdicts2 = inline_checks.evaluate_post_execution("x", "x", {}, {}, "Role", "chan")
    assert effect2 == "allow" and verdicts2 == []


def test_pre_execution_entitled_caller_allows(catalog_path):
    effect, verdicts = inline_checks.evaluate_pre_execution(
        "view_account", "get_account", {"account_id": 1}, "Teller", "branch", "read",
    )
    assert effect == "allow"
    assert any(v.check_id == "entitlement" and v.status == "satisfied" for v in verdicts)


def test_pre_execution_unentitled_caller_blocks(catalog_path):
    effect, verdicts = inline_checks.evaluate_pre_execution(
        "view_account", "get_account", {"account_id": 1}, "Applicant", "portal", "read",
    )
    assert effect == "block"
    assert any(v.check_id == "entitlement" and v.status == "violated" for v in verdicts)


def test_pre_execution_off_catalog_blocks(catalog_path):
    effect, verdicts = inline_checks.evaluate_pre_execution(
        "", "delete_everything", {}, "Teller", "branch", "write",
    )
    assert effect == "block"
    assert any(v.check_id == "catalog_membership" and v.status == "violated" for v in verdicts)


def test_pre_execution_side_effect_escalation_blocks(catalog_path):
    effect, verdicts = inline_checks.evaluate_pre_execution(
        "view_account", "get_account", {"account_id": 1}, "Teller", "branch", "write",
    )
    assert effect == "block"
    assert any(v.check_id == "side_effect_class" and v.status == "violated" for v in verdicts)


def test_pre_execution_undeclared_param_flags_but_never_blocks(catalog_path):
    # version_conformance's effect is "flag" (schema drift, not a gate) - must
    # never change the decision even though it violated.
    effect, verdicts = inline_checks.evaluate_pre_execution(
        "view_account", "get_account", {"account_id": 1, "include_ssn": True}, "Teller", "branch", "read",
    )
    assert effect == "allow"
    assert any(v.check_id == "version_conformance" and v.status == "violated" for v in verdicts)


def test_post_execution_restricted_field_reports_block(rule_pack_path):
    effect, verdicts = inline_checks.evaluate_post_execution(
        "view_account", "get_account", {"account_id": 1}, {"account_id": 1, "ssn": "123-45-6789"},
        "Teller", "branch",
    )
    assert effect == "block"
    assert any(v.check_id == "field_restriction" and v.status == "violated" for v in verdicts)


def test_restricted_field_names_extracts_hits(rule_pack_path):
    hits = inline_checks.restricted_field_names({"account_id": 1, "ssn": "123-45-6789"})
    assert hits == {"ssn"}


def test_restricted_field_names_empty_when_clean(rule_pack_path):
    assert inline_checks.restricted_field_names({"account_id": 1, "balance": 500}) == set()


def test_post_execution_clean_result_allows(rule_pack_path):
    effect, verdicts = inline_checks.evaluate_post_execution(
        "view_account", "get_account", {"account_id": 1}, {"account_id": 1, "balance": 500},
        "Teller", "branch",
    )
    assert effect == "allow"


# --- preload(): a configured-but-unreadable artifact must fail at STARTUP ----
# Hard Rule 9 says an UNSET path means "this deployment ships without the
# artifact" and degrades to Family 2 only. A SET path that does not resolve is
# a different thing entirely - a misconfiguration - and must never be silently
# read as "unconfigured", or a typo would quietly disable Family 1's content
# rules and Family 3's catalog checks with no signal at all.

def _preload_with(monkeypatch, *, rule_pack="", catalog=""):
    monkeypatch.setattr(inline_checks, "RULE_PACK_PATH", rule_pack)
    monkeypatch.setattr(inline_checks, "INTENT_CATALOG_PATH", catalog)
    inline_checks.reload()
    try:
        inline_checks.preload()
    finally:
        inline_checks.reload()


def test_preload_unconfigured_is_a_no_op(monkeypatch):
    _preload_with(monkeypatch)  # both empty: the documented degrade-to-nothing path


def test_preload_accepts_real_artifacts(monkeypatch, rule_pack_path, catalog_path):
    _preload_with(monkeypatch, rule_pack=str(rule_pack_path), catalog=str(catalog_path))


def test_preload_raises_on_missing_rule_pack(monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "rule_pack.yaml"
    with pytest.raises(RuntimeError) as e:
        _preload_with(monkeypatch, rule_pack=str(missing))
    assert "PREFRONT_RULE_PACK_PATH" in str(e.value)
    assert "FileNotFoundError" in str(e.value)


def test_preload_raises_on_missing_intent_catalog(monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "intent_catalog.yaml"
    with pytest.raises(RuntimeError) as e:
        _preload_with(monkeypatch, catalog=str(missing))
    assert "PREFRONT_INTENT_CATALOG_PATH" in str(e.value)


def test_preload_raises_on_malformed_yaml(monkeypatch, tmp_path):
    bad = tmp_path / "rule_pack.yaml"
    bad.write_text("rule_pack: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        _preload_with(monkeypatch, rule_pack=str(bad))
    assert "PREFRONT_RULE_PACK_PATH" in str(e.value)


# --- restricted_field_names respects restricted_from_roles -------------------
# content.py:_rule_binds_role gates a rule on `restricted_from_roles`; the
# inline mask set must gate identically, or a "not X, but Y" substitution rule
# (a Loan Officer sees the tier band, an Underwriter sees the raw score) denies
# X to every role and leaves Y unreachable.

ROLE_SCOPED_RULE_PACK_YAML = textwrap.dedent("""
    rule_pack:
      version: 1
      source_skill: test
      source_skill_version: "1"
      rules:
        - rule_id: R-SCORE-OFFICER-ONLY
          engine: content
          check: field_restriction
          effect: block
          restricted_from_roles: [Loan Officer]
          detectors:
            - field_names: [credit_score]
              scopes: [final_answer]
          source: {document: d, section: s, page: 1, text: t}
        - rule_id: R-SSN-ALL-ROLES
          engine: content
          check: field_restriction
          effect: block
          detectors:
            - field_names: [ssn]
              scopes: [final_answer]
          source: {document: d, section: s, page: 1, text: t}
      rejected: []
""")


@pytest.fixture
def role_scoped_rule_pack(tmp_path: Path, monkeypatch):
    p = tmp_path / "rule_pack.yaml"
    p.write_text(ROLE_SCOPED_RULE_PACK_YAML)
    monkeypatch.setattr(inline_checks, "RULE_PACK_PATH", str(p))
    return p


ROW = {"applicant_id": 1, "credit_score": 780, "ssn": "343-43-4343"}


def test_role_scoped_field_is_masked_for_the_restricted_role(role_scoped_rule_pack):
    assert inline_checks.restricted_field_names(ROW, "Loan Officer") == {"credit_score", "ssn"}


def test_role_scoped_field_is_NOT_masked_for_an_entitled_role(role_scoped_rule_pack):
    # The Underwriter is entitled to the raw score (§12.2) — masking it here
    # would enforce more than the policy states, and make the tier-band
    # substitution unreachable for the role that should see the number.
    assert inline_checks.restricted_field_names(ROW, "Underwriter") == {"ssn"}


def test_unrestricted_rule_still_binds_every_role(role_scoped_rule_pack):
    assert "ssn" in inline_checks.restricted_field_names(ROW, "Branch Manager")


def test_no_resolved_role_masks_defensively(role_scoped_rule_pack):
    # No identity resolved => no basis to narrow a restriction; mask more.
    assert inline_checks.restricted_field_names(ROW, "") == {"credit_score", "ssn"}


def test_final_answer_scope_still_masks_the_result_inline(role_scoped_rule_pack):
    # Both rules are final_answer-scoped. Inline, masking the RESULT is how a
    # field is kept out of the final answer, so scope must not gate the mask.
    assert inline_checks.restricted_field_names(ROW, "Loan Officer")
