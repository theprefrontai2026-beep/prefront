"""FastAPI wiring for the new design-time endpoints (no LLM required)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skillbuilder import api
from skillbuilder.schema import CandidateRule
from skillbuilder.store import Store


@pytest.fixture
def client(tmp_path):
    api._store = Store(str(tmp_path / "api.db"))
    api._REGISTRY = str(tmp_path / "skills")
    return TestClient(api.app)


def _seed_doc_with_rule(client, field="credit_status"):
    up = client.post("/design/skills/documents/upload", json={
        "text": "## 4.1 hold\nNo order accepted for accounts on hold.",
        "file_name": "p.md", "domain": "credit_collections", "version": "1.0",
    }).json()
    doc_id = up["document_id"]
    client.post(f"/design/skills/documents/{doc_id}/segment")
    rule = CandidateRule.model_validate({
        "rule_key": "hold_block", "rule_type": "restriction",
        "conditions": [{"field": field, "operator": "==", "value": "hold"}],
        "effect": {"decision": "block", "message": "x"},
        "applies_to_intents": ["create_order"],
        "source_clause_id": "clause_0001", "source_evidence": "on hold",
    })
    api._store.replace_candidate_rules(doc_id, [rule])
    return doc_id


def test_healthz_and_domain_packs(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert "credit_collections" in client.get("/design/skills/domain-packs").json()["domain_packs"]


def test_validate_persists_unresolved(client):
    doc_id = _seed_doc_with_rule(client, field="customer_tier")  # unmappable
    rep = client.post(f"/design/skills/documents/{doc_id}/validate").json()
    assert rep["summary"]["candidate_rules_total"] == 1
    items = client.get(f"/design/skills/documents/{doc_id}/unresolved-items").json()
    assert any(i["unresolved_type"] == "unmappable_symbol" for i in items["unresolved_items"])


def test_resolve_unresolved(client):
    doc_id = _seed_doc_with_rule(client, field="customer_tier")
    client.post(f"/design/skills/documents/{doc_id}/validate")
    items = client.get(f"/design/skills/documents/{doc_id}/unresolved-items").json()["unresolved_items"]
    uid = items[0]["unresolved_id"]
    out = client.post(f"/design/skills/unresolved-items/{uid}/resolve",
                      json={"status": "waived", "resolved_by": "me"}).json()
    assert out["status"] == "waived"


def test_generate_tests(client):
    doc_id = _seed_doc_with_rule(client)
    out = client.post(f"/design/skills/documents/{doc_id}/generate-tests").json()
    assert any(t["rule_key"] == "hold_block" for t in out["test_cases"])


def test_clause_ledger_endpoint(client):
    doc_id = _seed_doc_with_rule(client)
    client.post(f"/design/skills/documents/{doc_id}/classify-clauses")
    led = client.get(f"/design/skills/documents/{doc_id}/clause-ledger").json()
    assert led["clauses"]
    assert all(c["disposition"] for c in led["clauses"])


def test_edit_rule(client):
    doc_id = _seed_doc_with_rule(client)
    crid = api._store.list_candidate_rules(doc_id)[0]["candidate_rule_id"]
    edited = {
        "rule_key": "hold_block", "rule_type": "restriction",
        "conditions": [{"field": "credit_status", "operator": "==", "value": "watch"}],
        "effect": {"decision": "approval_required", "message": "y"},
        "applies_to_intents": ["create_order"],
        "source_clause_id": "clause_0001", "source_evidence": "on hold",
    }
    out = client.patch(f"/design/skills/candidate-rules/{crid}", json={"rule": edited}).json()
    assert out["rule"]["conditions"][0]["value"] == "watch"
