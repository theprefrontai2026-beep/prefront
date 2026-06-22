"""Pipeline orchestrator.

Wires the stages into one flow that the CLI calls:

    physical catalog (DDL)        ─┐
                                   ├─> LLM semantic mapper ─> promote ─┐
    policy hints (skill-builder)  ─┘                                   │
                                                                       ▼
                          intent bindings ─> MCP tool contracts ─> validate

Returns a :class:`PipelineResult` of plain pydantic objects; the caller renders
artifacts. The semantic model is stamped ``published`` only if validation passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .bindings import build_bindings
from .catalog import build_catalog_from_file
from .llm import LLMClient
from .logutil import get_logger
from .mapper import promote, suggest
from .mcptools import build_tools
from .policy import load_policy
from .querygen import build_query_templates
from .schema import (
    CandidateSemanticModel,
    IntentBinding,
    McpTool,
    PhysicalCatalog,
    QueryTemplate,
    Relationship,
    SemanticModel,
    SensitivityRule,
)
from .validate import ValidationResult, validate

log = get_logger(__name__)


@dataclass
class PipelineResult:
    catalog: PhysicalCatalog
    model: SemanticModel
    relationships: list[Relationship]
    sensitivity: list[SensitivityRule]
    bindings: list[IntentBinding]
    templates: list[QueryTemplate]
    tools: list[McpTool]
    candidate: CandidateSemanticModel
    validation: ValidationResult
    generated_by: str = ""
    errors: list[str] = field(default_factory=list)
    # Set only by the dbt-import path: the deterministic translation report
    # (entities mapped, joins approved vs dropped-non-FK, sensitivity, warnings).
    report: Optional[dict] = None


def run_pipeline(
    schema_path: str | Path,
    rules_dir: str | Path,
    *,
    model_id: str,
    datasource_id: Optional[str] = None,
    domain: Optional[str] = None,
    version: str = "1.0",
    client: Optional[LLMClient] = None,
    metrics: Optional[dict[str, str]] = None,
    caller_context: Optional[dict[str, str]] = None,
) -> PipelineResult:
    client = client or LLMClient()
    catalog = build_catalog_from_file(schema_path, datasource_id=datasource_id)
    hints = load_policy(rules_dir)
    domain = domain or hints.domain

    mapped = suggest(catalog, hints, client=client)
    model, relationships, sensitivity = promote(
        mapped.candidate, catalog, hints,
        model_id=model_id, domain=domain, version=version, generated_by=client.model,
    )
    bindings = build_bindings(model, sensitivity, hints)
    # Compose query templates first — this also back-links template_ids onto the
    # bindings, so the bindings/tools that follow reference an approved template.
    templates = build_query_templates(model, relationships, bindings, catalog, hints,
                                      metrics=metrics, caller_context=caller_context)
    tools = build_tools(bindings, model, hints, catalog, metrics=metrics)

    result = validate(catalog, model, relationships, sensitivity, bindings, templates, tools)
    model.status = "published" if result.ok else "draft"

    return PipelineResult(
        catalog=catalog,
        model=model,
        relationships=relationships,
        sensitivity=sensitivity,
        bindings=bindings,
        templates=templates,
        tools=tools,
        candidate=mapped.candidate,
        validation=result,
        generated_by=client.model,
        errors=mapped.errors,
    )


def run_import_pipeline(
    dbt_doc: str | dict,
    overlay_doc: str | dict,
    catalog: PhysicalCatalog,
    *,
    model_id: str,
    domain: Optional[str] = None,
    version: str = "1.0",
) -> PipelineResult:
    """Build the published contract from a customer-supplied dbt semantic model +
    a Prefront governance overlay — the deterministic (non-LLM) counterpart to
    ``run_pipeline``.

    The dbt model is *translated* (not guessed) into entities/attributes and
    FK-validated relationships; the overlay supplies the governance (rules,
    intents, sensitivity, metrics, caller scoping). From the resulting model the
    flow rejoins the SAME deterministic tail as the LLM path — bindings → query
    templates → MCP tools → validate — so a customer model is held to the exact
    same §19/§23 gate before it can be published.
    """
    from .dbt_import import parse_dbt, parse_overlay, to_prefront

    log.debug("run_import_pipeline: model_id=%s domain=%s version=%s tables=%d",
              model_id, domain, version, len(catalog.tables))
    dbt_models = parse_dbt(dbt_doc)
    overlay = parse_overlay(overlay_doc)
    domain = domain or overlay.domain

    imported = to_prefront(
        dbt_models, overlay, catalog, model_id=model_id, domain=domain, version=version,
    )
    model, relationships, sensitivity, hints = (
        imported.model, imported.relationships, imported.sensitivity, imported.hints,
    )

    bindings = build_bindings(model, sensitivity, hints)
    templates = build_query_templates(
        model, relationships, bindings, catalog, hints,
        metrics=overlay.metrics, caller_context=overlay.caller_context,
    )
    tools = build_tools(bindings, model, hints, catalog, metrics=overlay.metrics)

    result = validate(catalog, model, relationships, sensitivity, bindings, templates, tools)
    model.status = "published" if result.ok else "draft"
    log.debug(
        "run_import_pipeline: entities=%d relationships=%d sensitive=%d intents=%d "
        "templates=%d tools=%d validation_ok=%s errors=%d",
        len(model.entities), len(relationships), len(sensitivity), len(bindings),
        len(templates), len(tools), result.ok, len(result.errors),
    )
    for e in result.errors:
        log.warning("run_import_pipeline: validation ✗ %s", e)

    return PipelineResult(
        catalog=catalog,
        model=model,
        relationships=relationships,
        sensitivity=sensitivity,
        bindings=bindings,
        templates=templates,
        tools=tools,
        candidate=CandidateSemanticModel(),  # import path has no LLM candidate
        validation=result,
        generated_by="dbt-import",
        errors=imported.report.warnings,
        report=imported.report.as_dict(),
    )
