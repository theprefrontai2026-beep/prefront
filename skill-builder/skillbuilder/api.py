"""FastAPI service for the Skill Builder.

Exposes the design.md endpoints over the same pipeline the CLI uses:

    POST /design/skills/documents/upload
    POST /design/skills/documents/{document_id}/extract
    POST /design/skills/documents/{document_id}/segment
    POST /design/skills/documents/{document_id}/extract-rules
    GET  /design/skills/candidate-rules?document_id=...
    POST /design/skills/candidate-rules/{candidate_rule_id}/approve
    POST /design/skills/candidate-rules/{candidate_rule_id}/reject
    POST /design/skills/{skill_id}/publish

State lives in SQLite (path from SKILLBUILDER_DB, default ./skillbuilder.db).
Sections/clauses are re-derived deterministically from the stored raw text, so
the LLM is only ever invoked at the explicit ``extract-rules`` step.

Run:  uvicorn skillbuilder.api:app --reload
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from . import unresolved as unresolved_mod
from .artifacts import SkillMeta, write_run_artifacts, write_skill_artifacts
from .atoms import extract_atoms
from .classifier import classify_clauses
from .domain_packs import list_pack_names, load_pack
from .domain_packs.loader import Pack
from .extract import SUPPORTED_SUFFIXES, ExtractionError, extract_text
from .ledger import build_ledger
from .llm import ExtractionContext, RuleExtractor
from .logconfig import setup_logging
from .normalize import normalize
from .profiler import profile_document
from .schema import (
    ApprovedRule,
    CandidateRule,
    Clause,
    Effect,
    PolicyAtom,
    Source,
    UnresolvedItem,
)
from .schema_pack import merge_packs, pack_from_schema
from .segment import segment_sections
from .store import Store
from .tests_gen import generate_test_cases, untestable_rules
from .validation import run_all

setup_logging()  # honors SKILLBUILDER_LOG_LEVEL so UI-driven runs show the trace
log = logging.getLogger(__name__)

app = FastAPI(title="Prefront Skill Builder", version="0.1.0")

_DB_PATH = os.environ.get("SKILLBUILDER_DB", "skillbuilder.db")
_REGISTRY = os.environ.get("SKILLBUILDER_REGISTRY", "./skills")
# Named (curated) domain packs are DISABLED for now — grounding/validation use
# only the DDL-derived schema pack. The named-pack code path is kept intact and
# re-enabled by setting SKILLBUILDER_NAMED_PACKS to a truthy value (1/true/on).
_NAMED_PACKS_ENABLED = os.environ.get("SKILLBUILDER_NAMED_PACKS", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(_DB_PATH)
    return _store


# -- helpers ------------------------------------------------------------------


def _doc_or_404(document_id: str) -> dict:
    try:
        return store().get_document(document_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"document not found: {document_id}")


def _pack_for(doc: dict, requested_domain: Optional[str] = None) -> Optional[Pack]:
    """Resolve the grounding/validation pack for a document.

    Order: a curated named pack (by domain) is layered *over* a default pack
    derived from the document's datasource DDL (if one was supplied at upload).
    The named pack supplies aliases/roles/intents/metrics; the schema pack
    supplies the column namespace the binder actually resolves. Either may be
    absent — returns None only when neither exists.

    Named packs are currently gated off via SKILLBUILDER_NAMED_PACKS (see module
    constant); while disabled, only the DDL-derived schema pack is used.
    """
    if _NAMED_PACKS_ENABLED:
        named = load_pack(requested_domain or doc.get("domain") or "")
    else:
        named = None
        log.debug(
            "named domain packs disabled (SKILLBUILDER_NAMED_PACKS); "
            "using DDL-derived pack only for doc %s",
            doc.get("document_id"),
        )
    ddl = (doc.get("ddl") or "").strip()
    schema_dp = pack_from_schema(ddl) if ddl else None

    if named and schema_dp:
        log.info(
            "grounding doc %s: schema pack (%d cols) overlaid by named pack '%s'",
            doc.get("document_id"), len(schema_dp.fields), named.model.domain,
        )
        return Pack(merge_packs(schema_dp, named.model))
    if named:
        return named
    if schema_dp:
        log.info(
            "grounding doc %s: schema-derived default pack (%d cols), no named pack",
            doc.get("document_id"), len(schema_dp.fields),
        )
        return Pack(schema_dp)
    return None


def _derive(doc: dict):
    """Re-derive (normalized doc, clauses) deterministically from raw text."""
    normalized = normalize(
        doc["raw_text"],
        document_id=doc["document_id"],
        version=doc.get("version") or "0",
        file_name=doc["file_name"],
    )
    clauses = segment_sections(normalized)
    return normalized, clauses


def _clause_index(clauses: list[Clause]) -> dict[str, Clause]:
    return {c.clause_id: c for c in clauses}


# -- request models -----------------------------------------------------------


class UploadJSON(BaseModel):
    text: str
    file_name: str = "policy.md"
    domain: str = "general"
    owner: Optional[str] = None
    version: Optional[str] = None
    uploaded_by: Optional[str] = None
    document_id: Optional[str] = None
    # Optional datasource schema: when present, a default column-only domain pack
    # is derived from it to ground extraction/validation (see _pack_for).
    ddl: Optional[str] = None
    datasource_id: Optional[str] = None


class ExtractRulesBody(BaseModel):
    domain: Optional[str] = None
    known_roles: list[str] = []
    known_fields: list[str] = []
    known_intents: list[str] = []
    provider: Optional[str] = None
    model: Optional[str] = None


class ApproveBody(BaseModel):
    approved_by: str = "policy_admin"
    effective_from: Optional[str] = None
    version: str = "1.0"
    rule: Optional[dict] = None  # optional edited rule body


class RejectBody(BaseModel):
    rejected_by: str = "policy_admin"
    reason: str


class PublishBody(BaseModel):
    document_id: str
    name: Optional[str] = None
    domain: Optional[str] = None
    owner: Optional[str] = None
    approved_only: bool = True


class ValidateBody(BaseModel):
    pack: Optional[str] = None  # domain pack name; defaults to the doc's domain
    declared_params: list[str] = []
    metrics: list[str] = []


class EditRuleBody(BaseModel):
    rule: dict


class ResolveBody(BaseModel):
    status: str = "resolved"  # resolved | waived | open
    resolved_by: str = "policy_admin"
    notes: Optional[str] = None


def _candidate_rules(document_id: str) -> list[CandidateRule]:
    rows = store().list_candidate_rules(document_id)
    return [CandidateRule.model_validate(r["rule"]) for r in rows]


def _build_report(document_id: str, body: ValidateBody):
    """Run the validation engine over a document's candidate rules."""
    doc = _doc_or_404(document_id)
    _, clauses = _derive(doc)
    rules = _candidate_rules(document_id)
    pack = _pack_for(doc, body.pack)
    report = run_all(
        rules,
        clauses,
        pack=pack,
        declared_params=set(body.declared_params),
        metrics=set(body.metrics),
    )
    return report


# -- routes -------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/design/skills/documents/upload")
async def upload_document(request: Request):
    """Upload a policy document via multipart file OR a JSON {text:...} body.

    Branches on Content-Type so a single endpoint serves both, avoiding
    FastAPI's Form-vs-Body conflict.
    """
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(400, "multipart upload requires a 'file' field")
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(400, f"unsupported file type {suffix!r}")
        raw = await upload.read()
        tmp = Path(f"/tmp/_sb_{Path(upload.filename).name}")
        tmp.write_bytes(raw)
        try:
            text = extract_text(tmp)
        except ExtractionError as e:
            raise HTTPException(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)
        file_type = suffix.lstrip(".")
        file_name = upload.filename or "policy"
        domain = form.get("domain") or "general"
        owner = form.get("owner")
        version = form.get("version")
        uploaded_by = form.get("uploaded_by")
        document_id = form.get("document_id")
        ddl = form.get("ddl")
        datasource_id = form.get("datasource_id")
        ddl_upload = form.get("ddl_file")
        if ddl_upload is not None and hasattr(ddl_upload, "read"):
            ddl = (await ddl_upload.read()).decode("utf-8", "replace")
    else:
        try:
            payload = await request.json()
            body = UploadJSON.model_validate(payload)
        except (ValidationError, ValueError) as e:
            raise HTTPException(
                400, f"provide a multipart 'file' or a JSON body with 'text': {e}"
            )
        text = body.text
        file_type = Path(body.file_name).suffix.lstrip(".") or "md"
        file_name = body.file_name
        domain, owner, version = body.domain, body.owner, body.version
        uploaded_by, document_id = body.uploaded_by, body.document_id
        ddl, datasource_id = body.ddl, body.datasource_id

    doc = store().add_document(
        file_name=file_name,
        file_type=file_type,
        raw_text=text,
        domain=domain,
        owner=owner,
        version=version,
        uploaded_by=uploaded_by,
        document_id=document_id,
        ddl=ddl,
        datasource_id=datasource_id,
    )
    if ddl:
        log.info(
            "document %s uploaded with datasource schema (%d chars, datasource=%s)",
            doc["document_id"], len(ddl), datasource_id,
        )
    return {"document_id": doc["document_id"], "status": doc["status"]}


@app.post("/design/skills/documents/{document_id}/extract")
def extract_markdown(document_id: str):
    doc = _doc_or_404(document_id)
    normalized, _ = _derive(doc)
    # Re-extraction resets downstream clauses; persist_structure is FK-safe.
    n, _ = store().persist_structure(document_id, normalized.sections, [])
    store().set_document_status(document_id, "markdown_generated")
    return {
        "document_id": document_id,
        "status": "markdown_generated",
        "sections_count": n,
    }


@app.post("/design/skills/documents/{document_id}/segment")
def segment_clauses(document_id: str):
    doc = _doc_or_404(document_id)
    normalized, clauses = _derive(doc)
    _, n = store().persist_structure(document_id, normalized.sections, clauses)
    store().set_document_status(document_id, "segmented")
    return {"document_id": document_id, "clauses_created": n}


@app.post("/design/skills/documents/{document_id}/extract-rules")
def extract_rules(document_id: str, body: ExtractRulesBody = Body(default=ExtractRulesBody())):
    doc = _doc_or_404(document_id)
    normalized, clauses = _derive(doc)
    # Fall back to the resolved pack (named overlaid on the schema-derived
    # default) when the caller doesn't pass explicit vocabulary.
    pack = _pack_for(doc, body.domain)
    ctx = ExtractionContext(
        domain=body.domain or doc.get("domain") or "general",
        known_roles=body.known_roles or (pack.known_roles() if pack else []),
        known_fields=body.known_fields or (pack.known_fields() if pack else []),
        known_intents=body.known_intents or (pack.known_intents() if pack else []),
    )
    try:
        extractor = RuleExtractor(provider=body.provider, model=body.model)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(500, str(e))

    candidates: list[CandidateRule] = []
    errors: list[str] = []
    for res in extractor.extract_clauses(clauses, ctx):
        candidates.extend(res.candidates)
        errors.extend(f"{res.clause.clause_id}: {e}" for e in res.errors)

    # Persist structure too so approved_policy_rules' FK target always exists,
    # even if the caller skipped the explicit /segment step.
    store().persist_structure(document_id, normalized.sections, clauses)
    store().replace_candidate_rules(document_id, candidates)
    store().set_document_status(document_id, "rules_extracted")
    return {
        "document_id": document_id,
        "candidate_rules_created": len(candidates),
        "errors": errors,
        "requires_review": True,
    }


def _client(provider: Optional[str] = None, model: Optional[str] = None):
    """An LLM client, or None if the provider/model is unusable. Stages with a
    deterministic fallback (profile/classify) treat None as 'use the heuristic'."""
    try:
        return RuleExtractor(provider=provider, model=model)
    except (RuntimeError, ValueError):
        return None


class StageBody(BaseModel):
    pack: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    skill_id: Optional[str] = None


@app.post("/design/skills/documents/{document_id}/profile")
def profile_document_endpoint(document_id: str, body: StageBody = Body(default=StageBody())):
    doc = _doc_or_404(document_id)
    normalized, _ = _derive(doc)
    profile = profile_document(
        normalized.canonical_markdown,
        domain=body.pack or doc.get("domain"),
        client=_client(body.provider, body.model),
    )
    store().save_profile(document_id, profile.model_dump())
    return {"document_id": document_id, "profile": profile.model_dump()}


@app.post("/design/skills/documents/{document_id}/classify-clauses")
def classify_clauses_endpoint(document_id: str, body: StageBody = Body(default=StageBody())):
    doc = _doc_or_404(document_id)
    normalized, clauses = _derive(doc)
    classified = classify_clauses(clauses, client=_client(body.provider, body.model))
    store().persist_structure(document_id, normalized.sections, classified)
    store().set_document_status(document_id, "segmented")
    by_disposition: dict[str, int] = {}
    for c in classified:
        by_disposition[c.disposition or "none"] = by_disposition.get(c.disposition or "none", 0) + 1
    return {
        "document_id": document_id,
        "clauses_classified": len(classified),
        "by_disposition": by_disposition,
    }


@app.post("/design/skills/documents/{document_id}/extract-policy-atoms")
def extract_policy_atoms_endpoint(document_id: str, body: StageBody = Body(default=StageBody())):
    doc = _doc_or_404(document_id)
    _, clauses = _derive(doc)
    client = _client(body.provider, body.model)
    atoms = extract_atoms(clauses, client=client)
    store().replace_atoms(document_id, atoms)
    return {"document_id": document_id, "atoms_created": len(atoms),
            "atoms": [a.model_dump() for a in atoms]}


@app.post("/design/skills/documents/{document_id}/run-full-extraction")
def run_full_extraction(document_id: str, body: StageBody = Body(default=StageBody())):
    """Drive the whole chain: profile → classify → atoms → rules → validate, and
    write the per-run intermediates under skills/<skill_id>/v<ver>/runs/<run_id>/."""
    doc = _doc_or_404(document_id)
    normalized, clauses = _derive(doc)
    client = _client(body.provider, body.model)
    domain = body.pack or doc.get("domain") or "general"
    # Named pack overlaid on a default pack derived from the document's DDL.
    pack = _pack_for(doc, body.pack)
    skill_id = body.skill_id or domain or document_id
    run_id = store().add_extraction_run(
        document_id, model_name=getattr(client, "model", None), domain_pack=domain
    )
    try:
        profile = profile_document(normalized.canonical_markdown, domain=domain, client=client)
        store().save_profile(document_id, profile.model_dump())

        classified = classify_clauses(clauses, client=client)
        store().persist_structure(document_id, normalized.sections, classified)

        atoms = extract_atoms(classified, client=client)
        store().replace_atoms(document_id, atoms, run_id=run_id)

        candidates: list[CandidateRule] = []
        errors: list[str] = []
        if client is not None:
            ctx = ExtractionContext(
                domain=domain,
                known_roles=pack.known_roles() if pack else [],
                known_fields=pack.known_fields() if pack else [],
                known_intents=pack.known_intents() if pack else [],
            )
            for res in client.extract_clauses(classified, ctx):
                candidates.extend(res.candidates)
                errors.extend(f"{res.clause.clause_id}: {e}" for e in res.errors)
            store().replace_candidate_rules(document_id, candidates)
        else:
            candidates = _candidate_rules(document_id)  # reuse any prior extraction

        report = run_all(candidates, classified, pack=pack)
        unresolved_mod.save(store(), document_id, report.unresolved_items)
        ledger = build_ledger(classified, candidates, atoms, report.unresolved_items)

        meta = SkillMeta(
            skill_id=skill_id, name=skill_id, domain=domain,
            version=doc.get("version") or "1.0", source_document=document_id,
            file_name=doc["file_name"], file_hash=doc["file_hash"], owner=doc.get("owner"),
        )
        written = write_run_artifacts(
            _REGISTRY, meta, run_id,
            profile=profile, clauses=classified, ledger=ledger, atoms=atoms,
            unresolved_items=report.unresolved_items, validation_report=report,
        )
        store().set_document_status(document_id, "rules_extracted")
        store().complete_extraction_run(run_id)
    except HTTPException:
        raise
    except Exception as e:
        store().complete_extraction_run(run_id, status="failed", error=str(e))
        raise HTTPException(500, f"extraction failed: {e}")

    return {
        "document_id": document_id, "run_id": run_id, "skill_id": skill_id,
        "candidate_rules": len(candidates), "atoms": len(atoms),
        "summary": report.summary, "artifacts": written, "errors": errors,
    }


@app.get("/design/skills/candidate-rules")
def list_candidate_rules(document_id: Optional[str] = Query(default=None)):
    rows = store().list_candidate_rules(document_id)
    # Attach the originating clause's verbatim text (provenance) so the UI can show
    # the exact policy-document text a rule was generated from. The candidate already
    # carries source_clause_id + source_evidence; we join the clause for full text.
    clause_cache: dict[str, dict] = {}
    for row in rows:
        did = row.get("document_id")
        if did not in clause_cache:
            try:
                _, clauses = _derive(_doc_or_404(did))
                clause_cache[did] = _clause_index(clauses)
            except Exception:
                clause_cache[did] = {}
        rule = row.get("rule") or {}
        clause = clause_cache[did].get(rule.get("source_clause_id") or "")
        if clause:
            rule["source_text"] = clause.source_text
            rule["source"] = {"document": clause.document_id, "section": clause.section_path}
    return {"candidate_rules": rows}


def _approve_candidate(
    candidate_rule_id: str,
    row: dict,
    *,
    version: str,
    approved_by: str,
    effective_from: Optional[str] = None,
    rule_dict: Optional[dict] = None,
    clause_index: Optional[dict] = None,
) -> dict:
    """Validate a candidate, persist it as an ApprovedRule, and flip its
    review_status to 'approved'. Shared by the single-rule approve endpoint and
    the bulk approve-all endpoint (which passes a precomputed clause_index so it
    doesn't re-derive the document once per rule)."""
    cand = CandidateRule.model_validate(rule_dict or row["rule"])

    doc = _doc_or_404(row["document_id"])
    if clause_index is None:
        _, clauses = _derive(doc)
        clause_index = _clause_index(clauses)
    clause = clause_index.get(cand.source_clause_id or "")

    approved_at = _now()
    source = Source(
        document_id=doc["document_id"],
        file_name=doc["file_name"],
        page=clause.page_number if clause else None,
        section=clause.section_path if clause else "",
        paragraph_ref=clause.paragraph_ref if clause else None,
        evidence=cand.source_evidence,
        text=clause.source_text if clause else "",
    )
    approved = ApprovedRule(
        rule_key=cand.rule_key,
        rule_type=cand.rule_type,
        version=version,
        status="active",
        conditions=cand.conditions,
        effect=cand.effect,
        source=source,
        applies_to_intents=cand.applies_to_intents,
        trace_required=cand.requires_trace,
        approved_by=approved_by,
        approved_at=approved_at,
        effective_from=effective_from,
    )

    cand.review_status = "approved"
    store().set_review_status(
        candidate_rule_id, "approved", rule_json=cand.model_dump_json()
    )
    # policy_clauses stores clause ids prefixed with the document id.
    db_clause_id = (
        f"{doc['document_id']}:{cand.source_clause_id}"
        if cand.source_clause_id
        else None
    )
    store().add_approved_rule(
        rule_key=approved.rule_key,
        domain=doc.get("domain") or "general",
        rule_type=approved.rule_type,
        rule_json=approved.model_dump_json(),
        source_document_id=doc["document_id"],
        source_clause_id=db_clause_id,
        version=version,
        effective_from=effective_from,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    return json.loads(approved.model_dump_json())


@app.post("/design/skills/candidate-rules/{candidate_rule_id}/approve")
def approve_rule(candidate_rule_id: str, body: ApproveBody = Body(default=ApproveBody())):
    try:
        row = store().get_candidate_rule(candidate_rule_id)
    except KeyError:
        raise HTTPException(404, "candidate rule not found")
    try:
        approved = _approve_candidate(
            candidate_rule_id, row,
            version=body.version, approved_by=body.approved_by,
            effective_from=body.effective_from, rule_dict=body.rule,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"edited rule failed validation: {e}")
    return {"candidate_rule_id": candidate_rule_id, "review_status": "approved",
            "approved_rule": approved}


@app.post("/design/skills/documents/{document_id}/approve-all")
def approve_all_rules(
    document_id: str, body: ApproveBody = Body(default=ApproveBody())
):
    """Approve every not-yet-approved candidate rule for a document in one call."""
    doc = _doc_or_404(document_id)
    _, clauses = _derive(doc)
    clause_index = _clause_index(clauses)
    rows = store().list_candidate_rules(document_id)
    approved = 0
    errors: list[str] = []
    for r in rows:
        if r["review_status"] == "approved":
            continue
        cid = r["candidate_rule_id"]
        try:
            _approve_candidate(
                cid, r, version=body.version, approved_by=body.approved_by,
                effective_from=body.effective_from, clause_index=clause_index,
            )
            approved += 1
        except Exception as e:  # one bad candidate shouldn't abort the batch
            errors.append(f"{r.get('rule', {}).get('rule_key', cid)}: {e}")
    log.info("approve-all %s: approved %d/%d (%d errors)",
             document_id, approved, len(rows), len(errors))
    return {"document_id": document_id, "approved": approved,
            "total": len(rows), "errors": errors}


@app.post("/design/skills/documents/{document_id}/reset-approvals")
def reset_approvals(document_id: str):
    """Reset every reviewed candidate rule for a document back to 'pending'.
    Publish reads review_status, so this fully un-approves the document's rules."""
    _doc_or_404(document_id)
    rows = store().list_candidate_rules(document_id)
    reset = 0
    for r in rows:
        if r["review_status"] != "pending":
            store().set_review_status(r["candidate_rule_id"], "pending", notes=None)
            reset += 1
    log.info("reset-approvals %s: reset %d/%d", document_id, reset, len(rows))
    return {"document_id": document_id, "reset": reset, "total": len(rows)}


@app.post("/design/skills/candidate-rules/{candidate_rule_id}/reject")
def reject_rule(candidate_rule_id: str, body: RejectBody):
    try:
        store().get_candidate_rule(candidate_rule_id)
    except KeyError:
        raise HTTPException(404, "candidate rule not found")
    store().set_review_status(
        candidate_rule_id, "rejected", notes=f"{body.rejected_by}: {body.reason}"
    )
    return {"candidate_rule_id": candidate_rule_id, "review_status": "rejected"}


@app.patch("/design/skills/candidate-rules/{candidate_rule_id}")
def edit_rule(candidate_rule_id: str, body: EditRuleBody):
    """Replace a candidate rule's body (reviewer edit); re-validates the shape."""
    try:
        row = store().get_candidate_rule(candidate_rule_id)
    except KeyError:
        raise HTTPException(404, "candidate rule not found")
    try:
        cand = CandidateRule.model_validate(body.rule)
    except Exception as e:
        raise HTTPException(400, f"edited rule failed validation: {e}")
    cand.source_clause_id = cand.source_clause_id or row["rule"].get("source_clause_id")
    updated = store().set_review_status(
        candidate_rule_id, row["review_status"], rule_json=cand.model_dump_json()
    )
    return {"candidate_rule_id": candidate_rule_id, "rule": updated["rule"]}


@app.post("/design/skills/documents/{document_id}/validate")
def validate_document(document_id: str, body: ValidateBody = Body(default=ValidateBody())):
    report = _build_report(document_id, body)
    # Persist unresolved items so the UI / publish guard can read them.
    unresolved_mod.save(store(), document_id, report.unresolved_items)
    return report.model_dump()


@app.get("/design/skills/documents/{document_id}/validation-report")
def get_validation_report(document_id: str, pack: Optional[str] = Query(default=None)):
    return _build_report(document_id, ValidateBody(pack=pack)).model_dump()


@app.post("/design/skills/documents/{document_id}/generate-tests")
def generate_tests(document_id: str):
    _doc_or_404(document_id)
    rules = _candidate_rules(document_id)
    return {
        "document_id": document_id,
        "test_cases": generate_test_cases(rules),
        "untestable_rules": untestable_rules(rules),
    }


@app.get("/design/skills/documents/{document_id}/unresolved-items")
def list_unresolved(document_id: str):
    _doc_or_404(document_id)
    return {"unresolved_items": store().list_unresolved_items(document_id)}


@app.post("/design/skills/unresolved-items/{unresolved_id}/resolve")
def resolve_unresolved(unresolved_id: str, body: ResolveBody = Body(default=ResolveBody())):
    try:
        item = unresolved_mod.resolve(
            store(), unresolved_id, status=body.status,
            resolved_by=body.resolved_by, notes=body.notes,
        )
    except KeyError:
        raise HTTPException(404, "unresolved item not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return item


@app.get("/design/skills/domain-packs")
def list_domain_packs():
    return {"domain_packs": list_pack_names()}


@app.get("/design/skills/documents/{document_id}/clauses")
def list_clauses(document_id: str):
    _doc_or_404(document_id)
    return {"clauses": store().list_clauses(document_id)}


@app.get("/design/skills/documents/{document_id}/policy-atoms")
def list_policy_atoms(document_id: str):
    _doc_or_404(document_id)
    return {"atoms": store().list_atoms(document_id)}


@app.get("/design/skills/documents/{document_id}/profile")
def get_profile(document_id: str):
    _doc_or_404(document_id)
    return {"document_id": document_id, "profile": store().get_profile(document_id)}


@app.get("/design/skills/documents/{document_id}/clause-ledger")
def get_clause_ledger(document_id: str):
    """Build the clause ledger from stored clauses/rules/atoms/unresolved."""
    doc = _doc_or_404(document_id)
    _, derived = _derive(doc)
    # Merge stored dispositions onto the deterministically derived clauses.
    disp = {}
    for row in store().list_clauses(document_id):
        bare = str(row["clause_id"]).split(":", 1)[-1]
        if row.get("disposition"):
            disp[bare] = row["disposition"]
    clauses = [c.model_copy(update={"disposition": disp.get(c.clause_id)}) for c in derived]

    rules = _candidate_rules(document_id)
    atoms = [
        PolicyAtom.model_validate(r["atom"]) for r in store().list_atoms(document_id)
    ]
    unresolved = [
        UnresolvedItem.model_validate(r["item"])
        for r in store().list_unresolved_items(document_id)
    ]
    ledger = build_ledger(clauses, rules, atoms, unresolved)
    return {"document_id": document_id, "clauses": [e.model_dump() for e in ledger]}


@app.post("/design/skills/{skill_id}/publish")
def publish_skill(skill_id: str, body: PublishBody):
    doc = _doc_or_404(body.document_id)
    normalized, clauses = _derive(doc)

    rows = store().list_candidate_rules(body.document_id)
    selected = [
        CandidateRule.model_validate(r["rule"])
        for r in rows
        if (not body.approved_only) or r["review_status"] == "approved"
    ]
    if not selected:
        raise HTTPException(
            400,
            "no rules to publish (none approved yet). Approve candidates first or "
            "set approved_only=false.",
        )

    domain = body.domain or doc.get("domain") or "general"

    # Validate the published set; persist unresolved; block on open criticals.
    # Same grounding pack as extraction: named pack over schema-derived default.
    pack = _pack_for(doc, body.domain)
    report = run_all(selected, clauses, pack=pack)
    unresolved_mod.save(store(), body.document_id, report.unresolved_items)
    if unresolved_mod.has_open_critical(store(), body.document_id):
        raise HTTPException(
            409,
            "publication blocked: open critical unresolved item(s). Resolve or "
            "waive them first.",
        )

    file_hash = doc["file_hash"]
    meta = SkillMeta(
        skill_id=skill_id,
        name=body.name or skill_id,
        domain=domain,
        version=doc.get("version") or "1.0",
        source_document=doc["document_id"],
        file_name=doc["file_name"],
        file_hash=file_hash,
        owner=body.owner or doc.get("owner"),
    )
    written = write_skill_artifacts(
        _REGISTRY,
        meta,
        selected,
        clauses,
        normalized.canonical_markdown,
        generated_by="published",
        known_roles=pack.known_roles() if pack else None,
        known_fields=pack.known_fields() if pack else None,
        validation_report=report,
        unresolved_items=report.unresolved_items,
    )
    artifact = {"meta": meta.__dict__, "rules": [json.loads(r.model_dump_json()) for r in selected]}
    store().add_skill_version(
        skill_id=skill_id,
        version=meta.version,
        domain=domain,
        status="published",
        artifact_json=json.dumps(artifact),
        approved_by="policy_admin",
        approved_at=_now(),
    )
    return {
        "skill_id": skill_id,
        "version": meta.version,
        "status": "published",
        "rule_count": len(selected),
        "artifacts": written,
    }


@app.get("/design/skills/documents")
def list_documents():
    return {"documents": store().list_documents()}


@app.delete("/design/skills/documents/{document_id}")
def delete_document(document_id: str):
    """Remove a document and all rules/clauses derived from it."""
    try:
        store().delete_document(document_id)
    except KeyError:
        raise HTTPException(404, f"document not found: {document_id}")
    return {"deleted": document_id}


@app.get("/design/skills/versions")
def list_versions():
    return {"skill_versions": store().list_skill_versions()}


def _now() -> str:
    """Wall-clock timestamp (ISO-8601, Z)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
