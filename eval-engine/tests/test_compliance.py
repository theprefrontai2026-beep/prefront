"""Compliance reporting (compliance_design.md): packs, overlay, and the fold
from verdict rows to per-control states. Pure - no ClickHouse."""

from __future__ import annotations

import json
import textwrap

import pytest
import yaml

from evalengine.compliance import (
    CONTROL_CLASS_CHECKS, CONTROL_CLASSES, EMPTY_OVERLAY, FIELD_AWARE_CHECKS,
    build_report, load_overlay, load_packs,
)
from evalengine.compliance import overlay as overlay_mod
from evalengine.compliance import packs as packs_mod
from evalengine.compliance.classes import FAMILY_OF_CHECK, STORE_BASED_CLASSES
from evalengine.family1 import compilepack
from evalengine.family2 import CHECKS as F2_CHECKS
from evalengine.family3 import call, population, scope, session

# --- the engine's real check ids, gathered from the modules that emit them ----

F1_IDS = set(compilepack._DEFAULT_CHECK.values()) | {"substitution"} | {
    r for r in ("precondition", "sequencing", "prohibition", "field_restriction", "approval_gate")
}
F2_IDS = {m.CHECK_ID for m in F2_CHECKS}
F3_IDS = {
    call.CHECK_MEMBERSHIP, call.CHECK_ENTITLEMENT, call.CHECK_VERSION, call.CHECK_SIDE_EFFECT,
    scope.CHECK_FIELD, scope.CHECK_FILTER, scope.CHECK_VOLUME,
    session.CHECK_TOXIC, session.CHECK_GOAL, session.CHECK_WORKFLOW, session.CHECK_REDUNDANCY,
    population.CHECK_OUTCOME, population.CHECK_DRIFT, population.CHECK_TREND,
}
ALL_IDS = F1_IDS | F2_IDS | F3_IDS


def test_every_mapped_check_id_is_a_real_check():
    mapped = {c for ids in CONTROL_CLASS_CHECKS.values() for c in ids}
    assert mapped <= ALL_IDS, mapped - ALL_IDS


def test_every_real_check_evidences_at_least_one_control_class():
    mapped = {c for ids in CONTROL_CLASS_CHECKS.values() for c in ids}
    assert ALL_IDS <= mapped, ALL_IDS - mapped


def test_family_of_check_agrees_with_the_families():
    assert {c for c, f in FAMILY_OF_CHECK.items() if f == "family1"} == F1_IDS
    assert {c for c, f in FAMILY_OF_CHECK.items() if f == "family2"} == F2_IDS
    assert {c for c, f in FAMILY_OF_CHECK.items() if f == "family3"} == F3_IDS


def test_control_classes_table_is_complete_and_store_classes_have_no_checks():
    assert set(CONTROL_CLASS_CHECKS) == set(CONTROL_CLASSES)
    for cls in STORE_BASED_CLASSES:
        assert CONTROL_CLASS_CHECKS[cls] == ()
    assert FIELD_AWARE_CHECKS <= ALL_IDS


# --- packs --------------------------------------------------------------------

def test_bundled_packs_load_and_are_well_formed():
    packs = load_packs()
    assert {"gdpr", "soc2", "pci_dss", "hipaa"} <= set(packs)
    for p in packs.values():
        assert p.controls, p.framework
        for c in p.controls:
            assert c.control_class in CONTROL_CLASSES


def test_pack_rejects_unknown_control_class(tmp_path):
    bad = {"schema_version": packs_mod.SCHEMA_VERSION, "framework": "x",
           "controls": [{"id": "a", "control_class": "not_a_class"}]}
    with pytest.raises(ValueError, match="unknown control_class"):
        packs_mod.parse(bad)


def test_extra_pack_dir_replaces_bundled_pack_by_framework_id(tmp_path):
    (tmp_path / "gdpr.yaml").write_text(yaml.safe_dump({
        "schema_version": packs_mod.SCHEMA_VERSION, "framework": "gdpr", "title": "GDPR (tightened)",
        "version": "2", "controls": [{"id": "only", "control_class": "minimization"}],
    }), encoding="utf-8")
    packs = load_packs(str(tmp_path))
    assert packs["gdpr"].version == "2" and len(packs["gdpr"].controls) == 1
    assert "hipaa" in packs  # the others are untouched


# --- overlay ------------------------------------------------------------------

OVERLAY_YAML = textwrap.dedent("""
    schema_version: prefront.compliance_overlay.v1
    deployment: acme
    policy_document: handbook.md
    data_classes:
      personal_data: [people.national_id, people.home_phone]
      phi: []
    frameworks: [gdpr, hipaa]
    domain_regime:
      - regime: Sector rule
        bindings:
          - {policy_section: "4.2", control_class: field_protection, data_class: personal_data}
          - {policy_section: "7.1", control_class: retention}
""")


def test_overlay_parses_and_resolves_field_names(tmp_path):
    p = tmp_path / "overlay.yaml"
    p.write_text(OVERLAY_YAML, encoding="utf-8")
    o = load_overlay(str(p))
    assert o.configured and o.deployment == "acme"
    assert o.field_names_for("personal_data") == ("home_phone", "national_id")
    assert o.field_names_for("phi") == () and o.field_names_for("nope") == ()
    assert o.frameworks == ("gdpr", "hipaa")
    assert o.domain_regime[0].bindings[0].policy_section == "4.2"


def test_empty_path_is_not_configured():
    assert load_overlay("") is EMPTY_OVERLAY and not EMPTY_OVERLAY.configured


def test_overlay_rejects_unknown_control_class():
    raw = yaml.safe_load(OVERLAY_YAML)
    raw["domain_regime"][0]["bindings"][0]["control_class"] = "bogus"
    with pytest.raises(ValueError, match="unknown control_class"):
        overlay_mod.parse(raw)


# --- report -------------------------------------------------------------------

def _row(check_id, status, detail="", session_id="s1", section="", **kw):
    r = {
        "session_id": session_id, "check_id": check_id, "family": FAMILY_OF_CHECK[check_id],
        "rule_id": kw.get("rule_id", ""), "status": status, "effect": "flag", "detail": detail,
        "evidence_span_ids": ["sp1"], "evidence_excerpt": "x", "event_id": "1",
        "evaluated_at": kw.get("at", "2026-01-01T00:00:00+00:00"),
        "rule_pack_version": kw.get("rpv", "3"), "catalog_version": kw.get("cv", "4"),
        "source": json.dumps({"document": "handbook.md", "section": section}) if section else "",
    }
    return r


FACTS = {"rule_pack_configured": True, "catalog_configured": True,
         "retention": {"eval_verdicts": "", "spans": ""}, "truncated": False}


def _overlay():
    return overlay_mod.parse(yaml.safe_load(OVERLAY_YAML), "overlay.yaml")


def _control(report, framework, cid):
    fw = next(f for f in report["frameworks"] if f["framework"] == framework)
    return next(c for c in fw["controls"] if c["id"] == cid)


def test_no_overlay_reports_every_pack_with_data_classes_unbound():
    rep = build_report(packs=load_packs(), overlay=EMPTY_OVERLAY, verdict_rows=[], since=0, facts=FACTS)
    assert not rep["configured"]
    assert {f["framework"] for f in rep["frameworks"]} == set(load_packs())
    assert _control(rep, "hipaa", "s164_502_b")["state"] == "unbound"
    # a class-less control over configured checks with no rows is no_evidence, never "clean"
    assert _control(rep, "soc2", "cc6_1")["state"] == "no_evidence"


def test_overlay_selects_frameworks_and_scopes_field_aware_checks_by_data_class():
    rows = [
        _row("field_restriction", "violated", "rule R: restricted field(s) ['national_id (result)'] surfaced on tool_a"),
        _row("field_restriction", "satisfied", "rule R: no restricted field surfaced on tool_b", session_id="s2"),
        _row("field_restriction", "violated", "rule Q: restricted field(s) ['other_col (result)'] surfaced on tool_c", session_id="s3"),
    ]
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=rows, since=3600, facts=FACTS)
    assert [f["framework"] for f in rep["frameworks"]] == ["gdpr", "hipaa"]
    c = _control(rep, "gdpr", "art5_1_f")          # field_protection on personal_data
    assert c["scoping"] == "field" and c["state"] == "violated"
    # only the national_id row matched the class; the other_col violation and
    # the unscoped satisfied row (names no field) are NOT counted against it
    assert c["counts"] == {"satisfied": 0, "violated": 1, "indeterminate": 0, "sessions": 1}
    assert c["samples"][0]["session_id"] == "s1" and c["samples"][0]["evidence_span_ids"] == ["sp1"]
    # hipaa keys the same class on phi, which the overlay binds to nothing
    assert _control(rep, "hipaa", "s164_312_c_1")["state"] == "unbound"


def test_not_configured_when_every_check_belongs_to_an_unloaded_family():
    facts = dict(FACTS, rule_pack_configured=False, catalog_configured=False)
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=[], since=0, facts=facts)
    # purpose_limitation is family3 only -> not configured without a catalog
    c = _control(rep, "gdpr", "art5_1_b")
    assert c["state"] == "not_configured" and "family3" in c["note"]
    # integrity is family2 -> still runnable with no artifacts at all
    assert _control(rep, "gdpr", "art5_1_d")["state"] == "no_evidence"
    assert _control(rep, "gdpr", "art5_2_change")["state"] == "not_configured"


def test_evidenced_requires_satisfied_and_no_violated():
    rows = [_row("goal_alignment", "satisfied"), _row("goal_alignment", "satisfied", session_id="s2")]
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=rows, since=0, facts=FACTS)
    c = _control(rep, "gdpr", "art5_1_b")
    assert c["state"] == "evidenced" and c["counts"]["sessions"] == 2
    rows.append(_row("goal_alignment", "violated", session_id="s3"))
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=rows, since=0, facts=FACTS)
    c = _control(rep, "gdpr", "art5_1_b")
    assert c["state"] == "violated" and c["samples"][0]["status"] == "violated"


def test_store_based_classes_read_facts_not_verdict_status():
    rows = [_row("entitlement", "satisfied")]
    facts = dict(FACTS, retention={"eval_verdicts": "toDateTime(evaluated_at) + toIntervalDay(30)", "spans": ""})
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=rows, since=0, facts=facts)
    assert _control(rep, "gdpr", "art5_2")["state"] == "evidenced"          # audit_logging: rows exist
    assert _control(rep, "gdpr", "art5_2")["basis"] == "store"
    r = _control(rep, "gdpr", "art5_1_e")                                   # retention: half the tables
    assert r["state"] == "indeterminate" and "none on: spans" in r["note"]
    assert _control(rep, "gdpr", "art5_2_change")["state"] == "evidenced"   # versions stamped
    empty = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=[], since=0, facts=FACTS)
    assert _control(empty, "gdpr", "art5_2")["state"] == "no_evidence"
    assert _control(empty, "gdpr", "art5_1_e")["state"] == "no_evidence"


def test_domain_regime_bindings_count_verdicts_citing_the_section():
    rows = [
        _row("field_restriction", "violated", "rule R: restricted field(s) ['national_id (result)'] surfaced on t",
             section="4.2 Identifiers"),
        _row("field_restriction", "satisfied", "rule R: no restricted field surfaced on t", session_id="s2",
             section="4.2 Identifiers"),
        _row("entitlement", "satisfied", section="14.2 Something else", session_id="s3"),
    ]
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=rows, since=0, facts=FACTS)
    regime = rep["domain_regime"][0]
    assert regime["regime"] == "Sector rule"
    b = regime["bindings"][0]
    assert b["policy_section"] == "4.2" and b["state"] == "violated"
    assert b["cited"] == {"satisfied": 1, "violated": 1, "indeterminate": 0, "sessions": 2}
    # "14.2" must not match "4.2"
    assert regime["bindings"][1]["policy_section"] == "7.1" and regime["bindings"][1]["cited"]["sessions"] == 0


def test_only_filter_and_truncation_flag():
    facts = dict(FACTS, truncated=True)
    rep = build_report(packs=load_packs(), overlay=_overlay(), verdict_rows=[], since=0, facts=facts, only="hipaa")
    assert [f["framework"] for f in rep["frameworks"]] == ["hipaa"]
    assert rep["window"]["truncated"] is True and "truncated" not in rep["facts"]
