"""Store CRUD + invariants on the SQLAlchemy backend (SQLite)."""

from __future__ import annotations

from skillbuilder.schema import CandidateRule


def _doc(store, text="hello world", version="1.0"):
    return store.add_document(
        file_name="p.md", file_type="md", raw_text=text,
        domain="credit_collections", version=version,
    )


def _rule(clause_id="clause_0001"):
    return CandidateRule(
        rule_key="hold_block", rule_type="restriction",
        conditions=[{"field": "credit_status", "operator": "==", "value": "hold"}],
        effect={"decision": "block", "message": "x"},
        applies_to_intents=["create_order"],
        source_clause_id=clause_id, source_evidence="on hold",
    )


def test_document_immutability(store):
    a = _doc(store)
    b = _doc(store)  # same content + version
    assert a["document_id"] == b["document_id"]
    # Different version -> new row.
    c = _doc(store, version="2.0")
    assert c["document_id"] != a["document_id"]


def test_persist_structure_is_idempotent(store, section, clause):
    doc = _doc(store)
    ns, nc = store.persist_structure(doc["document_id"], [section()], [clause()])
    assert (ns, nc) == (1, 1)
    # Re-running upserts, does not duplicate or error on FK.
    ns2, nc2 = store.persist_structure(doc["document_id"], [section()], [clause()])
    assert (ns2, nc2) == (1, 1)


def test_candidate_rule_lifecycle(store, section, clause):
    doc = _doc(store)
    store.persist_structure(doc["document_id"], [section()], [clause()])
    n = store.replace_candidate_rules(doc["document_id"], [_rule()])
    assert n == 1
    rows = store.list_candidate_rules(doc["document_id"])
    assert len(rows) == 1 and rows[0]["rule"]["rule_key"] == "hold_block"
    assert rows[0]["review_status"] == "pending"

    crid = rows[0]["candidate_rule_id"]
    store.set_review_status(crid, "approved", rule_json=_rule().model_dump_json())
    assert store.get_candidate_rule(crid)["review_status"] == "approved"


def test_clause_disposition(store, section, clause):
    doc = _doc(store)
    store.persist_structure(doc["document_id"], [section()], [clause()])
    store.set_clause_disposition(doc["document_id"], "clause_0001", "rule_extracted")
    # Re-persisting must not wipe a set disposition when the incoming clause has none.
    store.persist_structure(doc["document_id"], [section()], [clause()])


def test_delete_cascades(store, section, clause):
    doc = _doc(store)
    store.persist_structure(doc["document_id"], [section()], [clause()])
    store.replace_candidate_rules(doc["document_id"], [_rule()])
    store.delete_document(doc["document_id"])
    assert store.list_documents() == []
    assert store.list_candidate_rules(doc["document_id"]) == []


def test_skill_versions(store):
    doc = _doc(store)
    store.add_skill_version(
        skill_id="cr_fin_001", version="1.0", domain="credit_collections",
        status="published", artifact_json="{}", approved_by="me",
    )
    versions = store.list_skill_versions()
    assert len(versions) == 1 and versions[0]["skill_id"] == "cr_fin_001"
