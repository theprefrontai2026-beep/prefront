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
