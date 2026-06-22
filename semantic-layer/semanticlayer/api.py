"""Design-time HTTP API for the semantic layer (datasource introspection).

Backs the Policy Studio "Data Connector" tab: parse an uploaded DDL file or
introspect a live database, returning the physical catalog (tables, columns,
PK/FK, enums, sensitivity markers) for the UI to render as an ER diagram.

    POST /design/semantic/catalog/parse        # {ddl, datasource_id}  OR  multipart file
    POST /design/semantic/catalog/introspect   # {dsn, datasource_id, schema}
    GET  /healthz

Run:  python -m semanticlayer api --port 8010
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .catalog import build_catalog, build_catalog_from_dsn
from .logutil import get_logger
from .store import Store

log = get_logger(__name__)

app = FastAPI(title="Prefront Semantic Layer API", version="0.1.0")

_DB_PATH = os.environ.get("SEMANTICLAYER_DB", "semanticlayer.db")
# Where `publish` writes the approved templates — the file the MCP runtime serves.
_PUBLISH_PATH = os.environ.get(
    "SEMANTICLAYER_PUBLISH_PATH", "/artifacts/example/query_templates.yaml"
)
# Where `publish-policy` writes the bound, enforceable policy bundle.
_POLICY_PUBLISH_PATH = os.environ.get(
    "SEMANTICLAYER_POLICY_PUBLISH_PATH", "/artifacts/example/policy.yaml"
)
# Root under which each datasource gets its OWN artifact dir (per-datasource
# isolation): <root>/<datasource_id>/query_templates.yaml. Defaults to the parent
# of the legacy single publish path (e.g. /artifacts).
_ARTIFACTS_ROOT = os.environ.get("SEMANTICLAYER_ARTIFACTS_ROOT") or str(
    Path(_PUBLISH_PATH).parent.parent
)
_store: Optional[Store] = None

# Demo baseline artifact dirs kept on reset, so a UI "disconnect / forget
# everything" leaves the bundled demos working. These are the dirs the demo MCP
# servers actually serve (securebank-mcp -> /artifacts/securebank-demo,
# commercerisk -> /artifacts/commercerisk). NB: the default datasource id
# "securebank" writes to /artifacts/securebank — a USER connection, not a
# baseline, so it is intentionally NOT kept. Override with a space-separated
# env list; empty wipes the baselines too.
_KEEP_DATASOURCES = [
    s for s in os.environ.get(
        "SEMANTICLAYER_KEEP_DATASOURCES", "securebank-demo commercerisk"
    ).split() if s
]


def _safe_seg(datasource_id: str) -> str:
    """Filesystem-safe artifact-dir segment for a datasource id."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", datasource_id or "datasource").strip("._") or "datasource"


def _functions_artifact_path(datasource_id: str) -> Path:
    """Per-datasource template artifact: <root>/<safe id>/query_templates.yaml."""
    return Path(_ARTIFACTS_ROOT) / _safe_seg(datasource_id) / "query_templates.yaml"


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(_DB_PATH)
    return _store


class ReviewBody(BaseModel):
    reviewer: str = "ui_reviewer"


class BuildBody(BaseModel):
    rules: list[dict] = Field(default_factory=list)  # skill-builder rule dicts
    ddl: Optional[str] = None
    dsn: Optional[str] = None
    domain: Optional[str] = None
    # The approved operations to generate interfaces for. Used to override/seed
    # intents when the rules themselves carry no applies_to_intents.
    intents: list[str] = Field(default_factory=list)
    # APPLICATION inputs (never hardcoded in Prefront):
    # derived-value definitions, e.g. {"available_credit": "credit_limit - current_balance"}
    metrics: dict[str, str] = Field(default_factory=dict)
    # trusted caller attribute -> scoping column, e.g. {"region": "region_id"}
    caller_context: dict[str, str] = Field(default_factory=dict)
    model_id: str = "semantic_model"
    datasource_id: Optional[str] = None
    version: str = "1.0"


class ImportDbtBody(BaseModel):
    # Customer-authored dbt semantic_models YAML (text). A dict is also accepted.
    dbt_model: object = ""
    # Prefront governance overlay (text or dict): intents, rules, sensitivity,
    # metrics, caller_context.
    overlay: object = Field(default_factory=dict)
    ddl: Optional[str] = None
    dsn: Optional[str] = None
    domain: Optional[str] = None
    model_id: str = "semantic_model"
    datasource_id: Optional[str] = None
    version: str = "1.0"


class FunctionsBody(BaseModel):
    ddl: Optional[str] = None
    dsn: Optional[str] = None
    datasource_id: Optional[str] = None
    recompute: bool = False  # re-run the LLM descriptions for every function
    owner_column: Optional[str] = None  # caller-scope reads by this column (e.g. user_id)


class FunctionSetBody(BaseModel):
    datasource_id: str
    name: str
    approved: bool = True


class FunctionBulkBody(BaseModel):
    datasource_id: str


class ParseBody(BaseModel):
    ddl: str
    datasource_id: Optional[str] = None


class IntrospectBody(BaseModel):
    dsn: str
    datasource_id: Optional[str] = None
    schema_: str = Field("public", alias="schema")

    model_config = {"populate_by_name": True}


def _catalog_payload(catalog) -> dict:
    """Catalog as JSON, a flat relationships list the ERD can draw edges from,
    and a default set of suggested intents the UI pre-fills (then a human curates)."""
    from .catalog import suggest_intents

    data = catalog.model_dump()
    rels = []
    for t in catalog.tables:
        for fk in t.foreign_keys:
            rels.append({
                "from_table": t.name,
                "from_column": fk.from_columns[0] if fk.from_columns else None,
                "to_table": fk.to_table,
                "to_column": fk.to_columns[0] if fk.to_columns else None,
            })
    data["relationships"] = rels
    data["suggested_intents"] = suggest_intents(catalog)
    return data


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- derived CRUD functions (computed on connect, reviewed in Data Connector) ---


def _split_intent(name: str) -> tuple[str, str]:
    """find_customers -> ('find', 'customers'); get_customer -> ('get', 'customer')."""
    verb, _, entity = name.partition("_")
    return verb, entity


def _fallback_desc(name: str) -> str:
    verb, entity = _split_intent(name)
    e = entity.replace("_", " ")
    return {
        "find": f"List {e}.",
        "get": f"Get a single {e} by its id.",
        "create": f"Create a new {e}.",
        "update": f"Update an existing {e}.",
        "delete": f"Delete a {e} by its id.",
    }.get(verb, f"{verb} {e}.")


def _describe_functions(catalog, names: list[str]) -> dict[str, str]:
    """One LLM call to write a business description per operation; deterministic
    fallback if the LLM is unavailable or returns nothing usable."""
    if not names:
        return {}
    from .llm import LLMClient
    from .mapper import _loads_lenient, _schema_text

    system = ("You write one-line descriptions of database operations for an AI agent "
              "that must pick the right operation for a user's natural-language request. "
              "For each operation, name the entity AND its notable columns (e.g. balance, "
              "status, ssn, amount) so a request phrased around those fields maps to it — "
              "e.g. 'List a user's accounts (account_id, type, balance, status).' Keep it "
              "one line. Return ONLY a JSON object mapping each operation name to its "
              "description.")
    user = (f"SCHEMA\n{_schema_text(catalog)}\n\nOPERATIONS\n"
            + "\n".join(f"- {n}" for n in names)
            + "\n\nReturn JSON: {\"<operation>\": \"<one-line description naming key columns>\"}")
    try:
        data = _loads_lenient(LLMClient().complete(system, user))
        if isinstance(data, dict):
            return {n: (str(data.get(n)).strip() or _fallback_desc(n)) for n in names}
    except Exception as e:  # noqa: BLE001
        log.warning("describe_functions: LLM failed (%s) — using fallback", e)
    return {n: _fallback_desc(n) for n in names}


@app.post("/design/semantic/functions")
def compute_functions(body: FunctionsBody):
    """Derive the CRUD operations from the connected schema, write an LLM
    description for each, persist them (status 'pending'), and return the list.
    Existing approvals are preserved across recompute."""
    from .catalog import suggest_intents

    if not (body.ddl or body.dsn):
        raise HTTPException(400, "provide a schema via 'ddl' or 'dsn'")
    ds = body.datasource_id or "datasource"
    try:
        catalog = (build_catalog(body.ddl, datasource_id=ds) if body.ddl
                   else build_catalog_from_dsn(body.dsn, datasource_id=ds))
    except Exception as e:
        raise HTTPException(502, f"schema read failed: {type(e).__name__}: {e}")
    if not catalog.tables:
        raise HTTPException(422, "no tables found in the provided schema")

    # persist schema + the caller-scope column so approval can regenerate scoped reads
    store().upsert_datasource(ds, body.ddl, body.dsn, (body.owner_column or "").strip() or None)
    names = suggest_intents(catalog)
    existing = {f["name"] for f in store().list_functions(ds)}
    todo = names if body.recompute else [n for n in names if n not in existing]
    descriptions = _describe_functions(catalog, todo)
    upserts = []
    for n in todo:
        verb, entity = _split_intent(n)
        upserts.append({"name": n, "verb": verb, "entity": entity,
                        "description": descriptions.get(n, _fallback_desc(n))})
    if upserts:
        store().upsert_functions(ds, upserts)
    log.debug("compute_functions: ds=%s total=%d described=%d", ds, len(names), len(upserts))
    return {"datasource_id": ds, "functions": store().list_functions(ds)}


def _datasource_policy_path(datasource_id: str) -> Path:
    """The policy bundle sits next to the templates in the datasource's dir."""
    return _functions_artifact_path(datasource_id).parent / "policy.yaml"


def _load_policy_bundle(path: Path) -> dict:
    """Existing policy_bundle dict at `path`, or {} — so the two writers (function
    approval = intents, publish-policy = rules) don't clobber each other's half."""
    import yaml as _yaml
    try:
        return (_yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("policy_bundle") or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_policy_bundle(datasource_id, intents: dict, rules: list, metrics: dict,
                         path: Path, domain: str | None = None) -> None:
    from datetime import datetime, timezone

    import yaml as _yaml

    bundle = {"policy_bundle": {
        "version": "1",
        "domain": domain or datasource_id,
        "datasource_id": datasource_id,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": metrics or {},
        "intents": intents,
        "rules": rules,
    }}
    header = (
        "# Per-datasource policy bundle (loaded by the governance layer alongside\n"
        "# query_templates.yaml). intents = the approved functions; rules are bound\n"
        "# from Policy Studio (publish-policy). With a bundle present the runtime is\n"
        "# GOVERNED: a caller identity is required even before any rule fires.\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + _yaml.safe_dump(bundle, sort_keys=False, width=100), encoding="utf-8")


def _build_functions(datasource_id: str):
    """Build (catalog, [QueryTemplate], [intent_id]) for a datasource's APPROVED
    functions — a table→entity model promoted from the catalog, bound, composed
    into parameterized CRUD templates. (None, [], []) when nothing is approved."""
    from .bindings import build_bindings
    from .mapper import promote
    from .policy import policy_hints_from_extracted
    from .querygen import build_query_templates
    from .schema import CandidateAttribute, CandidateEntity, CandidateSemanticModel

    src = store().get_datasource(datasource_id)
    approved = [f["name"] for f in store().list_functions(datasource_id)
                if f["status"] == "approved"]
    if not src or not approved:
        return None, [], []
    catalog = (build_catalog(src["ddl"], datasource_id=datasource_id) if src.get("ddl")
               else build_catalog_from_dsn(src["dsn"], datasource_id=datasource_id))
    hints = policy_hints_from_extracted({"domain": datasource_id, "rules": []})
    hints.intents = approved
    candidate = CandidateSemanticModel(entities=[
        CandidateEntity(
            entity_key=t.name, primary_table=t.name,
            attributes=[CandidateAttribute(attribute_key=c.name, physical_column=f"{t.name}.{c.name}")
                        for c in t.columns],
        ) for t in catalog.tables])
    model, rels, sens = promote(candidate, catalog, hints,
                                model_id=f"{datasource_id}_model", domain=datasource_id)
    bindings = build_bindings(model, sens, hints)
    # Caller-scope reads/writes by the datasource's owner column (e.g. user_id) so
    # an account holder only sees their own rows — WHERE <owner> = :caller_<owner>.
    owner = (src.get("owner_column") or "").strip()
    caller_context = {owner: owner} if owner else {}
    templates = build_query_templates(model, rels, bindings, catalog, hints,
                                      caller_context=caller_context)
    # Carry each function's LLM description onto its template so the MCP server
    # exposes it as the tool's meaning (server._describe prefers template.description).
    descs = {f["name"]: (f.get("description") or "") for f in store().list_functions(datasource_id)}
    for t in templates:
        if descs.get(t.intent_id):
            t.description = descs[t.intent_id]
    return catalog, templates, [b.intent_id for b in bindings]


def _publish_functions(datasource_id: str) -> dict:
    """Write the APPROVED functions' query templates + refresh the policy bundle's
    intents in the datasource's own dir (<root>/<datasource_id>/), PRESERVING any
    rules already bound there by publish-policy. Returns {published, path, policy}."""
    from .artifacts import render_query_templates

    _, templates, intents = _build_functions(datasource_id)
    path = _functions_artifact_path(datasource_id)
    policy_path = _datasource_policy_path(datasource_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_query_templates(templates) if templates else "query_templates: {}\n",
                    encoding="utf-8")

    prev = _load_policy_bundle(policy_path)
    prev_intents = prev.get("intents") or {}
    intents_dict = {i: {"allowed_roles": (prev_intents.get(i) or {}).get("allowed_roles", [])}
                    for i in intents}
    _write_policy_bundle(datasource_id, intents_dict, prev.get("rules") or [],
                         prev.get("metrics") or {}, policy_path)
    log.debug("_publish_functions: ds=%s → %d templates + policy(intents=%d, rules kept=%d) @ %s",
              datasource_id, len(templates), len(intents_dict), len(prev.get("rules") or []), path.parent)
    return {"published": len(templates), "path": str(path), "policy": str(policy_path)}


@app.post("/design/semantic/functions/set")
def set_function(body: FunctionSetBody):
    try:
        store().set_function(body.datasource_id, body.name,
                             "approved" if body.approved else "pending")
    except KeyError:
        raise HTTPException(404, f"function not found: {body.name}")
    pub = _publish_functions(body.datasource_id)
    return {"functions": store().list_functions(body.datasource_id),
            "published": pub["published"], "artifact": pub["path"], "policy": pub["policy"]}


@app.post("/design/semantic/functions/approve-all")
def approve_all_functions(body: FunctionBulkBody):
    n = store().set_all_functions(body.datasource_id, "approved")
    pub = _publish_functions(body.datasource_id)
    return {"updated": n, "functions": store().list_functions(body.datasource_id),
            "published": pub["published"], "artifact": pub["path"], "policy": pub["policy"]}


@app.post("/design/semantic/functions/reset")
def reset_functions(body: FunctionBulkBody):
    n = store().set_all_functions(body.datasource_id, "pending")
    pub = _publish_functions(body.datasource_id)
    return {"updated": n, "functions": store().list_functions(body.datasource_id),
            "published": pub["published"], "artifact": pub["path"], "policy": pub["policy"]}


@app.post("/design/semantic/catalog/parse")
async def parse_schema(request: Request):
    """Parse DDL from a JSON ``{ddl}`` body or a multipart ``file`` upload."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(400, "multipart upload requires a 'file' field")
        ddl = (await upload.read()).decode("utf-8", errors="replace")
        datasource_id = form.get("datasource_id") or (upload.filename or "schema")
    else:
        try:
            body = ParseBody.model_validate(await request.json())
        except Exception as e:
            raise HTTPException(400, f"provide JSON with 'ddl' or a multipart file: {e}")
        ddl, datasource_id = body.ddl, body.datasource_id or "schema"

    if not ddl.strip():
        raise HTTPException(400, "empty DDL")
    catalog = build_catalog(ddl, datasource_id=datasource_id)
    if not catalog.tables:
        raise HTTPException(422, "no CREATE TABLE statements found in the DDL")
    return _catalog_payload(catalog)


@app.post("/design/semantic/build")
def build_interfaces(body: BuildBody):
    """Generate the query-template interfaces (and MCP tools) from a schema +
    approved policy rules. Runs the semantic-layer pipeline (LLM mapper) and
    returns the generated artifacts as JSON."""
    from .bindings import build_bindings
    from .llm import LLMClient
    from .mapper import promote, suggest
    from .mcptools import build_tools
    from .policy import policy_hints_from_extracted
    from .querygen import build_query_templates
    from .validate import validate

    if not body.rules:
        raise HTTPException(400, "no policy rules provided")
    if not (body.ddl or body.dsn):
        raise HTTPException(400, "provide a schema via 'ddl' or 'dsn'")

    ds = body.datasource_id or "datasource"
    try:
        catalog = (
            build_catalog(body.ddl, datasource_id=ds) if body.ddl
            else build_catalog_from_dsn(body.dsn, datasource_id=ds)
        )
    except Exception as e:
        raise HTTPException(502, f"schema read failed: {type(e).__name__}: {e}")
    if not catalog.tables:
        raise HTTPException(422, "no tables found in the provided schema")

    hints = policy_hints_from_extracted({"domain": body.domain, "rules": body.rules})
    # Seed/override intents: generated rules often lack applies_to_intents, so
    # fall back to the explicit intents the caller requested.
    explicit = [i.strip() for i in (body.intents or []) if i.strip()]
    if explicit:
        hints.intents = explicit
    if not hints.intents:
        raise HTTPException(
            422,
            "no intents to generate interfaces for — specify the operations "
            "(e.g. create_order, find_customers) or tag the rules with applies_to_intents",
        )
    try:
        client = LLMClient()
        mapped = suggest(catalog, hints, client=client)
        model, rels, sens = promote(
            mapped.candidate, catalog, hints,
            model_id=body.model_id, domain=body.domain or hints.domain,
            version=body.version, generated_by=client.model,
        )
    except Exception as e:
        raise HTTPException(502, f"semantic mapping failed: {type(e).__name__}: {e}")

    bindings = build_bindings(model, sens, hints)
    templates = build_query_templates(model, rels, bindings, catalog, hints,
                                      metrics=body.metrics,
                                      caller_context=body.caller_context)
    tools = build_tools(bindings, model, hints, catalog, metrics=body.metrics)
    val = validate(catalog, model, rels, sens, bindings, templates, tools)

    # Persist the generated set (status defaults to pending) so approvals survive.
    persisted = store().replace_templates(
        model.semantic_model_id, ds, [t.model_dump() for t in templates]
    )

    return {
        "semantic_model_id": model.semantic_model_id,
        "status": model.status,
        "generated_by": model.generated_by,
        "query_templates": persisted,
        "mcp_tools": [t.model_dump() for t in tools],
        "intents": [b.intent_id for b in bindings],
        "validation": {"ok": val.ok, "errors": val.errors},
        "mapper_errors": mapped.errors,
    }


@app.post("/design/semantic/import/dbt")
def import_dbt(body: ImportDbtBody):
    """Ingest a customer-supplied dbt semantic model + a Prefront governance
    overlay, translate them deterministically (NO LLM) into the published
    contract, and persist the generated templates as ``pending`` for review.

    Mirrors the ``/build`` response shape so the existing template review /
    approve / publish UI works unchanged — and adds a ``translation_report`` that
    surfaces exactly what was mapped, and which dbt joins were dropped for not
    being backed by a real foreign key."""
    from .dbt_import import parse_overlay
    from .pipeline import run_import_pipeline

    if not body.dbt_model:
        raise HTTPException(400, "no dbt_model provided")
    if not (body.ddl or body.dsn):
        raise HTTPException(400, "provide the datasource schema via 'ddl' or 'dsn'")

    ds = body.datasource_id or "datasource"
    log.debug("import_dbt: model_id=%s datasource=%s domain=%s has_ddl=%s has_dsn=%s",
              body.model_id, ds, body.domain, bool(body.ddl), bool(body.dsn))
    try:
        catalog = (
            build_catalog(body.ddl, datasource_id=ds) if body.ddl
            else build_catalog_from_dsn(body.dsn, datasource_id=ds)
        )
    except Exception as e:
        raise HTTPException(502, f"schema read failed: {type(e).__name__}: {e}")
    if not catalog.tables:
        raise HTTPException(422, "no tables found in the provided schema")

    try:
        result = run_import_pipeline(
            body.dbt_model, body.overlay, catalog,
            model_id=body.model_id, domain=body.domain, version=body.version,
        )
    except Exception as e:
        log.exception("import_dbt: translation failed")
        raise HTTPException(422, f"dbt import failed: {type(e).__name__}: {e}")

    persisted = store().replace_templates(
        result.model.semantic_model_id, ds, [t.model_dump() for t in result.templates]
    )
    log.debug("import_dbt: persisted %d template(s); validation_ok=%s",
              len(persisted), result.validation.ok)

    # Echo back the overlay's governance so the UI can publish the enforceable
    # policy bundle (POST /publish-policy) from the same overlay, one click later.
    overlay = parse_overlay(body.overlay)
    policy = {
        "rules": overlay.rules,
        "metrics": overlay.metrics,
        "domain": body.domain or overlay.domain,
    }

    return {
        "semantic_model_id": result.model.semantic_model_id,
        "status": result.model.status,
        "generated_by": result.generated_by,
        "query_templates": persisted,
        "mcp_tools": [t.model_dump() for t in result.tools],
        "intents": [b.intent_id for b in result.bindings],
        "validation": {"ok": result.validation.ok, "errors": result.validation.errors},
        "translation_report": result.report,
        "import_warnings": result.errors,
        "policy": policy,
    }


@app.get("/design/semantic/templates")
def list_templates(semantic_model_id: Optional[str] = None):
    """Previously generated templates with their persisted approval status."""
    return {"query_templates": store().list_templates(semantic_model_id)}


@app.post("/design/semantic/templates/{template_id}/approve")
def approve_template(template_id: str, body: ReviewBody = ReviewBody()):
    try:
        return store().set_status(template_id, "approved", body.reviewer)
    except KeyError:
        raise HTTPException(404, f"template not found: {template_id}")


@app.post("/design/semantic/templates/{template_id}/reject")
def reject_template(template_id: str, body: ReviewBody = ReviewBody()):
    try:
        return store().set_status(template_id, "rejected", body.reviewer)
    except KeyError:
        raise HTTPException(404, f"template not found: {template_id}")


class PublishBody(BaseModel):
    semantic_model_id: Optional[str] = None


class PublishPolicyBody(BaseModel):
    rules: list[dict] = Field(default_factory=list)  # approved skill-builder rules
    ddl: Optional[str] = None
    dsn: Optional[str] = None
    domain: Optional[str] = None
    datasource_id: Optional[str] = None
    # Application-defined derived values used by rules (e.g. available_credit).
    metrics: dict[str, str] = Field(default_factory=dict)


@app.post("/design/semantic/publish-policy")
def publish_policy(body: PublishPolicyBody):
    """Bind the approved business rules against the datasource vocabulary and
    publish the enforceable policy bundle the runtime governance layer loads.

    Every rule symbol must resolve (column / declared request param / metric /
    caller.*); rules with unresolvable vocabulary are rejected here — never
    shipped to the runtime."""
    from datetime import datetime, timezone
    from pathlib import Path

    import yaml as _yaml

    from .policybind import bind_rules

    if not body.rules:
        raise HTTPException(400, "no rules provided (approve rules in Policy Studio first)")
    if not (body.ddl or body.dsn):
        raise HTTPException(400, "provide the datasource vocabulary via 'ddl' or 'dsn'")

    ds = body.datasource_id or "datasource"
    try:
        catalog = (
            build_catalog(body.ddl, datasource_id=ds) if body.ddl
            else build_catalog_from_dsn(body.dsn, datasource_id=ds)
        )
    except Exception as e:
        raise HTTPException(502, f"schema read failed: {type(e).__name__}: {e}")
    if not catalog.tables:
        raise HTTPException(422, "no tables found in the provided schema")

    # Bind against the datasource's OWN approved-function templates (so request
    # params / root tables resolve); fall back to the legacy store when none.
    _, fn_templates, fn_intents = _build_functions(ds)
    templates = [t.model_dump() for t in fn_templates] if fn_templates else store().list_templates()
    bound, rejected, skipped, intents_map = bind_rules(
        body.rules, catalog, templates, body.metrics)
    if not bound:
        raise HTTPException(
            422,
            f"no enforceable rules survived binding "
            f"(rejected={[r['rule_key'] for r in rejected]}, skipped_no_intent={skipped})",
        )

    # Keep every approved function as an intent (allowed_roles from any allow rule),
    # plus any rule-only intents the binder surfaced — so publishing rules never
    # drops the function catalog that approval wrote.
    intents = {i: {"allowed_roles": (intents_map.get(i) or {}).get("allowed_roles", [])}
               for i in fn_intents}
    for k, v in intents_map.items():
        intents.setdefault(k, v)

    policy_path = _datasource_policy_path(ds)  # per-datasource, beside its templates
    _write_policy_bundle(ds, intents, bound, body.metrics, policy_path, domain=body.domain)
    return {
        "published": len(bound),
        "rules": [r["rule_key"] for r in bound],
        "rejected": rejected,
        "skipped_no_intent": skipped,
        "intents": intents,
        "path": str(policy_path),
    }


@app.post("/design/semantic/publish")
def publish(body: PublishBody = PublishBody()):
    """Write the APPROVED templates to the runtime artifact (query_templates.yaml)
    that the semantic-mcp-server serves. The server live-reloads the file, so the
    approved templates become callable MCP tools without a restart."""
    from pathlib import Path

    from .artifacts import render_query_templates
    from .schema import QueryTemplate

    rows = store().list_templates(body.semantic_model_id)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        raise HTTPException(400, "no approved templates to publish")

    # Stored rows are QueryTemplate dumps (+ status/reviewer, which pydantic ignores).
    templates = [QueryTemplate.model_validate(r) for r in approved]
    path = Path(_PUBLISH_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_query_templates(templates), encoding="utf-8")
    return {
        "published": len(templates),
        "path": str(path),
        "templates": [t.template_id for t in templates],
    }


@app.post("/design/semantic/catalog/introspect")
def introspect(body: IntrospectBody):
    """Introspect a live PostgreSQL database into a catalog."""
    try:
        catalog = build_catalog_from_dsn(
            body.dsn, schema=body.schema_, datasource_id=body.datasource_id or "datasource"
        )
    except Exception as e:
        raise HTTPException(502, f"introspection failed: {type(e).__name__}: {e}")
    if not catalog.tables:
        raise HTTPException(422, f"no tables found in schema '{body.schema_}'")
    return _catalog_payload(catalog)


class ResetBody(BaseModel):
    # Keep the demo baselines (securebank/commercerisk) by default.
    keep_baselines: bool = True
    # Explicit override of which datasource ids to preserve (wins over keep_baselines).
    keep_datasource_ids: Optional[list[str]] = None


@app.post("/design/semantic/reset")
def reset(body: Optional[ResetBody] = None):
    """Forget connected datasources: clear the datasource/function/query-template
    store and remove published per-datasource artifact dirs. Demo baselines
    (securebank/commercerisk) are preserved unless ``keep_baselines`` is false."""
    body = body or ResetBody()
    keep = (body.keep_datasource_ids if body.keep_datasource_ids is not None
            else (_KEEP_DATASOURCES if body.keep_baselines else []))
    keep_segs = {_safe_seg(k) for k in keep}

    cleared = store().clear(keep_datasource_ids=keep)

    removed_dirs: list[str] = []
    root = Path(_ARTIFACTS_ROOT)
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in keep_segs:
                shutil.rmtree(d, ignore_errors=True)
                removed_dirs.append(d.name)
    log.info("reset: cleared store=%s removed artifact dirs=%s kept=%s",
             cleared, removed_dirs, sorted(keep_segs))
    return {"cleared": cleared, "removed_artifact_dirs": removed_dirs, "kept": sorted(keep_segs)}
