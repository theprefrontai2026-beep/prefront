"""Per-application configuration (application_isolation_design.md Phase 3).

The engine was single-tenant: one rule pack, one catalog, one disabled-check
set for the whole deployment. These cover the registry that makes each of them
per-application, and — as much as anything — that an UNCONFIGURED registry
changes nothing at all.
"""

from __future__ import annotations

import textwrap

import pytest

from evalengine import binding, compliance, visibility
from evalengine.applications import AppConfig, Registry
from evalengine.checks import CheckSettings
from evalengine.family1.compilepack import load as load_rule_pack
from evalengine.family3.catalog import load as load_catalog


def _defaults() -> AppConfig:
    return AppConfig(
        app_id="", binding=binding.load(""), visibility=visibility.load(""),
        rule_pack=load_rule_pack(""), catalog=load_catalog(""),
        overlay=compliance.load_overlay(""),
    )


RULE_PACK = textwrap.dedent("""
    rule_pack:
      version: 1
      source_skill: alpha_policy
      source_skill_version: "3.1"
      rules:
        - rule_id: R-A
          engine: content
          check: field_restriction
          effect: block
          detectors: [{field_names: [secret], scopes: [result]}]
          source: {document: d, section: s, page: 1, text: t}
      rejected: []
""")

CATALOG = textwrap.dedent("""
    intent_catalog:
      version: 9
      intents:
        - intent: do_thing
          side_effect: read
          params: []
          allowed_callers: {roles: [Operator], channels: []}
          fields: [a]
""")


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "rp.yaml").write_text(RULE_PACK)
    (tmp_path / "cat.yaml").write_text(CATALOG)
    (tmp_path / "apps.yaml").write_text(textwrap.dedent(f"""
        applications:
          version: 7
          apps:
            - id: alpha
              artifacts:
                rule_pack: {tmp_path / 'rp.yaml'}
                intent_catalog: {tmp_path / 'cat.yaml'}
              checks:
                disabled: [field_scope]
            - id: beta
              artifacts: {{}}
    """))
    return Registry(_defaults(), str(tmp_path / "apps.yaml"))


# --- the unconfigured case is the one that must not change ------------------

def test_unconfigured_registry_returns_the_deployment_defaults():
    r = Registry(_defaults())
    assert not r.configured
    assert r.for_app("anything").app_id == ""
    assert r.app_ids == []


def test_an_unregistered_application_falls_back_rather_than_raising(registry):
    # A session can be ingested for an application nobody registered yet;
    # refusing to evaluate it would lose the evidence.
    assert registry.for_app("never-heard-of-it").rule_pack.rules == ()
    assert not registry.registered("never-heard-of-it")


# --- per-application artifacts ----------------------------------------------

def test_an_application_uses_its_own_artifacts(registry):
    cfg = registry.for_app("alpha")
    assert cfg.rule_pack.source_skill == "alpha_policy"
    assert cfg.rule_pack.source_skill_version == "3.1"
    assert cfg.catalog.version == "9"


def test_an_application_declaring_none_inherits_the_defaults(registry):
    cfg = registry.for_app("beta")
    assert cfg.rule_pack.rules == ()
    assert cfg.catalog.intents == {}


def test_registered_applications_are_listed(registry):
    assert registry.app_ids == ["alpha", "beta"]


def test_a_registry_naming_a_missing_artifact_fails_at_load(tmp_path):
    # Set-but-unreadable is a misconfiguration, not "unconfigured" — the same
    # rule api.py's _load_artifact enforces for the deployment-wide paths.
    (tmp_path / "apps.yaml").write_text(textwrap.dedent(f"""
        applications:
          apps:
            - id: alpha
              artifacts: {{rule_pack: {tmp_path / 'nope.yaml'}}}
    """))
    with pytest.raises(FileNotFoundError):
        Registry(_defaults(), str(tmp_path / "apps.yaml"))


# --- per-application check settings -----------------------------------------

def test_a_declared_check_set_replaces_the_deployment_one(registry):
    deployment = CheckSettings(disabled=frozenset({"param_taint"}))
    assert registry.for_app("alpha").settings(deployment).disabled == frozenset({"field_scope"})


def test_no_declaration_inherits_the_deployment_set(registry):
    deployment = CheckSettings(disabled=frozenset({"param_taint"}))
    assert registry.for_app("beta").settings(deployment) is deployment


def test_an_empty_declared_set_is_not_the_same_as_no_declaration(tmp_path):
    # "runs every check" must be expressible, and must override a deployment
    # default — otherwise an app can never opt OUT of a deployment-wide off.
    (tmp_path / "apps.yaml").write_text(textwrap.dedent("""
        applications:
          apps:
            - id: alpha
              checks: {disabled: []}
    """))
    r = Registry(_defaults(), str(tmp_path / "apps.yaml"))
    deployment = CheckSettings(disabled=frozenset({"param_taint"}))
    assert r.for_app("alpha").settings(deployment).disabled == frozenset()


def test_the_version_key_differs_between_applications(registry):
    from evalengine.evaluate import version_key
    a, b = registry.for_app("alpha"), registry.for_app("beta")
    ka = version_key(a.binding, a.visibility, a.rule_pack, a.catalog, a.settings(CheckSettings()))
    kb = version_key(b.binding, b.visibility, b.rule_pack, b.catalog, b.settings(CheckSettings()))
    assert ka != kb, "each application must be evaluated and re-evaluated independently"
