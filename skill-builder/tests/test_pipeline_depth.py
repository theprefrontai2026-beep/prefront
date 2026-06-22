"""Ledger, classifier, profiler (deterministic paths) + full-extraction wiring."""

from __future__ import annotations

import yaml

from skillbuilder.classifier import classify_clauses, heuristic_disposition
from skillbuilder.ledger import build_ledger
from skillbuilder.profiler import profile_document
from skillbuilder.schema import CandidateRule, Clause, PolicyAtom, UnresolvedItem, UnresolvedSource


def _clause(cid, ctype="restriction", text="No order on hold", disp=None):
    return Clause(clause_id=cid, document_id="d1", section_path="4.1",
                  clause_type=ctype, disposition=disp, source_text=text)


# --- classifier ---------------------------------------------------------------


def test_heuristic_disposition_mapping():
    assert heuristic_disposition("definition") == "definition_only"
    assert heuristic_disposition("explanatory") == "non_enforceable_context"
    assert heuristic_disposition("restriction") == "rule_candidate_required"


def test_classify_assigns_disposition_to_every_clause():
    clauses = [_clause("c1", "restriction"), _clause("c2", "definition"),
               _clause("c3", "explanatory")]
    out = classify_clauses(clauses, client=None)
    assert all(c.disposition is not None for c in out)
    assert {c.clause_id: c.disposition for c in out}["c2"] == "definition_only"


# --- profiler -----------------------------------------------------------------


def test_heuristic_profile_detects_features():
    md = "# 1. Purpose\nThis policy means X.\n## 2. Approval\nDiscounts above 15% require approval."
    prof = profile_document(md, domain="credit_collections", client=None)
    assert prof.detected_domain == "credit_collections"
    assert prof.structural_features["has_thresholds"]
    assert prof.structural_features["has_approval_matrix"]


# --- ledger -------------------------------------------------------------------


def test_ledger_covers_every_clause_with_a_disposition():
    clauses = [_clause("c1", disp="rule_candidate_required"),
               _clause("c2", "definition", disp="definition_only"),
               _clause("c3")]  # no disposition -> must be inferred
    rules = [CandidateRule.model_validate({
        "rule_key": "r1", "rule_type": "restriction",
        "conditions": [{"field": "credit_status", "operator": "==", "value": "hold"}],
        "effect": {"decision": "block"}, "applies_to_intents": ["create_order"],
        "source_clause_id": "c1", "source_evidence": "x",
    })]
    atoms = [PolicyAtom(atom_id="a_0001", clause_id="c2", atom_type="definition")]
    unresolved = [UnresolvedItem(unresolved_id="u_001", type="unmappable_symbol",
                                 severity="high",
                                 source=UnresolvedSource(clause_id="c3"))]
    led = build_ledger(clauses, rules, atoms, unresolved)
    by = {e.clause_id: e for e in led}
    assert len(led) == 3 and all(e.disposition for e in led)
    assert by["c1"].generated_rules == ["r1"] and by["c1"].disposition == "rule_candidate_required"
    assert by["c2"].generated_atoms == ["a_0001"]
    assert by["c3"].disposition == "unresolved"  # inferred from unresolved link


# --- full extraction (no LLM: provider forced invalid -> deterministic) --------


def test_run_full_extraction_writes_run_artifacts(tmp_path):
    from fastapi.testclient import TestClient
    from skillbuilder import api
    from skillbuilder.store import Store

    api._store = Store(str(tmp_path / "x.db"))
    api._REGISTRY = str(tmp_path / "skills")
    client = TestClient(api.app)

    up = client.post("/design/skills/documents/upload", json={
        "text": "## 4.1 Hold\nNo order accepted for accounts on hold.\n"
                "## 4.2 Limits\nOrders may not exceed available credit.",
        "file_name": "p.md", "domain": "credit_collections", "version": "1.0",
    }).json()
    out = client.post(
        f"/design/skills/documents/{up['document_id']}/run-full-extraction",
        json={"provider": "__no_llm__", "skill_id": "cr_test"},
    ).json()

    assert out["run_id"]
    arts = out["artifacts"]
    assert "clause_ledger.yaml" in arts and "document_profile.yaml" in arts
    ledger = yaml.safe_load(open(arts["clause_ledger.yaml"]))
    assert ledger["clauses"] and all(c["disposition"] for c in ledger["clauses"])
