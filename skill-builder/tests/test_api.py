"""FastAPI wiring for the new design-time endpoints (no LLM required)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from skillbuilder import api
from skillbuilder.llm import ClauseExtraction
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


class _FakeExtractor:
    """Stands in for RuleExtractor - no network, but exercises the real
    extract_clauses(..., on_progress=...) contract via a tiny sleep per
    clause, so a poll between /start and completion can observe partial
    progress instead of the job finishing before the test can look."""

    model = "fake-model"
    provider = "fake"

    def __init__(self, **_kwargs):
        pass

    def extract_clauses(self, clauses, ctx, on_progress=None):
        clauses = list(clauses)
        out = []
        for i, c in enumerate(clauses):
            time.sleep(0.05)
            out.append(ClauseExtraction(clause=c))
            if on_progress:
                on_progress(i + 1, len(clauses))
        return out


def _seed_multi_clause_doc(client):
    up = client.post("/design/skills/documents/upload", json={
        "text": "## 4.1 hold\nNo order accepted for accounts on hold.\n\n"
               "## 4.2 limit\nOrders above $10,000 need approval.\n\n"
               "## 4.3 region\nOnly ship within region.",
        "file_name": "p.md", "domain": "credit_collections", "version": "1.0",
    }).json()
    return up["document_id"]


def test_extract_rules_start_and_progress(client, monkeypatch):
    monkeypatch.setattr(api, "RuleExtractor", _FakeExtractor)
    doc_id = _seed_multi_clause_doc(client)

    started = client.post(f"/design/skills/documents/{doc_id}/extract-rules/start").json()
    assert started["total"] == 3
    assert started["status"] == "running"

    # Poll until done (the fake extractor's sleeps guarantee this isn't
    # instant, so at least one poll should observe a real in-progress state).
    seen_running = False
    deadline = time.time() + 5
    progress = None
    while time.time() < deadline:
        progress = client.get(f"/design/skills/documents/{doc_id}/extract-rules/progress").json()
        assert 0 <= progress["completed"] <= progress["total"] == 3
        if progress["status"] == "running":
            seen_running = True
        if progress["status"] == "done":
            break
        time.sleep(0.02)

    assert progress["status"] == "done"
    assert progress["completed"] == 3
    assert progress["result"]["document_id"] == doc_id
    assert progress["result"]["requires_review"] is True
    assert seen_running, "expected at least one poll to catch the job mid-flight"


def test_extract_rules_progress_404_before_start(client):
    resp = client.get("/design/skills/documents/never-started/extract-rules/progress")
    assert resp.status_code == 404


def test_extract_rules_sync_endpoint_still_works_unchanged(client, monkeypatch):
    """The original blocking endpoint (CLI/scripts still use it) must be
    untouched by the background-job refactor."""
    monkeypatch.setattr(api, "RuleExtractor", _FakeExtractor)
    doc_id = _seed_multi_clause_doc(client)
    out = client.post(f"/design/skills/documents/{doc_id}/extract-rules").json()
    assert out["document_id"] == doc_id
    assert out["requires_review"] is True


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
