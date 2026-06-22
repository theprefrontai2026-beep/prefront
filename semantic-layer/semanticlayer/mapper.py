"""Semantic mapper — the agentic core (design §18).

At design time only, the LLM is asked to suggest a *candidate* semantic model:
business entities and attributes mapped onto the REAL physical schema, plus
sensitivity candidates. It is grounded with the physical catalog and the policy
vocabulary (data fields / roles / intents) so it adapts the design's business
concepts to whatever tables actually exist.

Everything the model returns is candidate output. We then:
  * validate it against the catalog (drop entities/attributes referencing tables
    or columns that do not exist — design rule "do not invent tables/columns"),
  * derive APPROVED join relationships *deterministically from real foreign keys*
    (the agent must never invent joins — design §7, §23.6), and
  * promote the survivors into the published contract shapes, merging sensitivity
    from policy hints + schema markers + LLM candidates (sensitive -> deny, §23.7).

Promotion stands in for the human review/publish step (design §2). For the MVP it
is automatic, but only candidates that pass validation are promoted, and their
provenance/confidence is preserved on the candidate artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from pydantic import ValidationError

from .llm import LLMClient
from .policy import PolicyHints
from .schema import (
    CandidateSemanticModel,
    Join,
    PhysicalCatalog,
    Relationship,
    SemanticAttribute,
    SemanticEntity,
    SemanticModel,
    SensitivityRule,
)

SYSTEM = (
    "You are helping build a semantic model for Prefront, a governed access layer "
    "between AI agents and enterprise data sources. The runtime uses approved "
    "intents, query templates, policies, semantic mappings, validators, and decision "
    "traces — never fresh LLM SQL.\n\n"
    "Your output is ONLY a candidate semantic model; it is not approved for runtime.\n"
    "Rules:\n"
    "- Do NOT invent tables or columns. Use ONLY the tables/columns in PHYSICAL SCHEMA.\n"
    "- Map every attribute to a real 'table.column'.\n"
    "- Do NOT create relationships without a real foreign key as evidence.\n"
    "- Mark uncertain mappings in 'ambiguities'.\n"
    "- Flag sensitive columns (PII, credit/financial, risk) as sensitivity candidates "
    "with recommended_default_access='deny'. Columns marked [SENSITIVE]/[GOVERNED] in "
    "the schema, and any field named in RESTRICTED FIELDS, are sensitive.\n"
    "- Prefer deterministic, reviewable mappings. Set a confidence in [0,1].\n"
    "- Return ONLY a JSON object. No prose, no markdown fences."
)

USER_TEMPLATE = """PHYSICAL SCHEMA (the only tables/columns you may use)
{schema}

BUSINESS VOCABULARY (context to name entities/attributes — not new columns)
- domain: {domain}
- data fields the policy evaluates: {fields}
- caller/approver roles: {roles}
- approved intents: {intents}

RESTRICTED FIELDS (treat as sensitive, default_access deny)
{restricted}

Return JSON with exactly this shape:
{{
  "entities": [
    {{
      "entity_key": "Customer",
      "description": "one line",
      "primary_table": "customers",
      "synonyms": ["account", "client"],
      "attributes": [
        {{"attribute_key": "credit_limit", "physical_column": "customers.credit_limit",
          "business_type": "number", "sensitivity_level": "normal|confidential|restricted",
          "confidence": 0.0, "evidence": "why this mapping"}}
      ],
      "ambiguities": []
    }}
  ],
  "relationships": [
    {{"relationship_key": "Customer_to_Order", "from_entity": "Customer", "to_entity": "Order",
      "join": {{"from": "customers.customer_id", "to": "orders.customer_id"}},
      "cardinality": "one_to_one|one_to_many|many_to_one|many_to_many",
      "confidence": 0.0, "evidence": "foreign key reference", "ambiguities": []}}
  ],
  "sensitivity_candidates": [
    {{"physical_column": "customers.tax_id", "classification": "pii",
      "recommended_default_access": "deny", "sensitivity_level": "restricted",
      "confidence": 0.0, "evidence": "column name + [SENSITIVE] marker"}}
  ]
}}"""


@dataclass
class MapResult:
    candidate: CandidateSemanticModel
    raw: str = ""
    errors: list[str] = field(default_factory=list)


def suggest(
    catalog: PhysicalCatalog,
    hints: PolicyHints,
    *,
    client: Optional[LLMClient] = None,
) -> MapResult:
    """Call the LLM for a candidate semantic model, validated against the catalog."""
    client = client or LLMClient()
    restricted = sorted(set(hints.restricted_fields) | _schema_sensitive(catalog))
    user = USER_TEMPLATE.format(
        schema=_schema_text(catalog),
        domain=hints.domain,
        fields=", ".join(hints.data_fields) or "(none)",
        roles=", ".join(hints.roles) or "(none)",
        intents=", ".join(hints.intents) or "(none)",
        restricted="\n".join(f"- {f}" for f in restricted) or "(none)",
    )

    errors: list[str] = []
    raw = client.complete(SYSTEM, user)
    payload = _loads_lenient(raw)
    if payload is None:
        errors.append("invalid_json: mapper did not return parseable JSON")
        return MapResult(candidate=CandidateSemanticModel(), raw=raw, errors=errors)

    try:
        candidate = CandidateSemanticModel.model_validate(payload)
    except ValidationError as e:
        # Don't discard the whole model over one bad field — salvage the items
        # that validate, dropping (and reporting) only the ones that don't.
        errors.append(f"schema_invalid (salvaging valid items): {e.errors()[:1]}")
        candidate = _salvage(payload, errors)

    _drop_unreal(candidate, catalog, errors)
    return MapResult(candidate=candidate, raw=raw, errors=errors)


def _salvage(payload: dict, errors: list[str]) -> CandidateSemanticModel:
    """Validate entities/relationships/sensitivity individually; keep the good ones."""
    from .schema import CandidateEntity, CandidateRelationship, CandidateSensitivity

    cand = CandidateSemanticModel()
    sections = [
        ("entities", CandidateEntity, cand.entities),
        ("relationships", CandidateRelationship, cand.relationships),
        ("sensitivity_candidates", CandidateSensitivity, cand.sensitivity_candidates),
    ]
    for key, cls, bucket in sections:
        for i, item in enumerate(payload.get(key, []) or []):
            try:
                bucket.append(cls.model_validate(item))
            except ValidationError as e:
                errors.append(f"dropped {key}[{i}]: {e.errors()[:1]}")
    return cand


def promote(
    candidate: CandidateSemanticModel,
    catalog: PhysicalCatalog,
    hints: PolicyHints,
    *,
    model_id: str,
    domain: str,
    version: str = "1.0",
    generated_by: str = "",
) -> tuple[SemanticModel, list[Relationship], list[SensitivityRule]]:
    """Turn validated candidates into the published contract (review stand-in)."""
    sensitivity = _build_sensitivity(candidate, catalog, hints)
    sens_cols = {s.physical_column.lower() for s in sensitivity}

    entities: list[SemanticEntity] = []
    for ce in candidate.entities:
        tbl = catalog.table(ce.primary_table)
        pk = f"{tbl.name}.{tbl.primary_key[0]}" if tbl and tbl.primary_key else ""
        attrs = [
            SemanticAttribute(
                attribute_key=ca.attribute_key,
                column=ca.physical_column,
                type=ca.business_type,
                required=bool(tbl and tbl.column(ca.physical_column.split(".")[-1])
                              and not tbl.column(ca.physical_column.split(".")[-1]).nullable),
                sensitivity_level=("restricted" if ca.physical_column.lower() in sens_cols
                                   else ca.sensitivity_level),
            )
            for ca in ce.attributes
        ]
        entities.append(
            SemanticEntity(
                entity_key=ce.entity_key,
                description=ce.description,
                primary_table=ce.primary_table,
                primary_key=pk,
                synonyms=ce.synonyms,
                restricted=any(a.sensitivity_level == "restricted" for a in attrs),
                attributes=attrs,
            )
        )

    model = SemanticModel(
        semantic_model_id=model_id,
        version=version,
        status="published",
        domain=domain,
        approved_by="auto_review",
        generated_by=generated_by,
        entities=entities,
    )
    relationships = _build_relationships(model, catalog, candidate, sens_cols)
    return model, relationships, sensitivity


# --- relationships from REAL foreign keys (agent must not invent joins) -------


def _build_relationships(
    model: SemanticModel,
    catalog: PhysicalCatalog,
    candidate: CandidateSemanticModel,
    sens_cols: set[str],
) -> list[Relationship]:
    table_to_entity = {e.primary_table.lower(): e.entity_key for e in model.entities}
    # Index LLM relationship metadata by the real column pair it joins on.
    llm_meta = {
        (cr.join.from_.lower(), cr.join.to.lower()): cr
        for cr in candidate.relationships
    }
    rels: list[Relationship] = []
    seen: set[tuple[str, str]] = set()
    for table in catalog.tables:
        from_entity = table_to_entity.get(table.name.lower())
        for fk in table.foreign_keys:
            to_entity = table_to_entity.get(fk.to_table.lower())
            if not from_entity or not to_entity:
                continue  # no semantic entity on one side — not expressible
            from_col = f"{table.name}.{fk.from_columns[0]}"
            to_col = f"{fk.to_table}.{fk.to_columns[0]}"
            key_pair = (from_col.lower(), to_col.lower())
            if key_pair in seen:
                continue
            seen.add(key_pair)
            meta = llm_meta.get(key_pair) or llm_meta.get((to_col.lower(), from_col.lower()))
            rels.append(
                Relationship(
                    relationship_key=(meta.relationship_key if meta
                                      else f"{from_entity}_to_{to_entity}"),
                    from_entity=from_entity,
                    to_entity=to_entity,
                    join=Join(**{"from": from_col, "to": to_col}),
                    cardinality=(meta.cardinality if meta else "many_to_one"),
                    approved=True,  # backed by a real FK
                    restricted=(from_col.lower() in sens_cols or to_col.lower() in sens_cols),
                )
            )
    return rels


# --- sensitivity merge (policy hints + schema markers + LLM candidates) -------


def _build_sensitivity(
    candidate: CandidateSemanticModel,
    catalog: PhysicalCatalog,
    hints: PolicyHints,
) -> list[SensitivityRule]:
    col_to_attr = _column_to_attr_index(candidate)
    # Gather candidate sensitive columns from all three sources.
    cols: dict[str, dict] = {}
    for s in candidate.sensitivity_candidates:
        cols.setdefault(s.physical_column.lower(), {}).update(
            classification=s.classification, level=s.sensitivity_level,
        )
    for qual in _schema_sensitive(catalog):
        cols.setdefault(qual, {}).setdefault("classification", "confidential_business")
        cols[qual].setdefault("level", "restricted")
    for fname, meta in hints.restricted_fields.items():
        qual = _resolve_field(fname, catalog)
        if qual:
            entry = cols.setdefault(qual, {})
            entry.setdefault("classification", meta["classification"])
            entry.setdefault("level", "confidential")
            entry["allowed_roles"] = meta.get("allowed_roles", [])

    rules: list[SensitivityRule] = []
    for qual, meta in cols.items():
        if not catalog.has_column(qual):
            continue
        key = col_to_attr.get(qual) or qual  # 'Entity.attr' if known, else table.col
        rules.append(
            SensitivityRule(
                key=key,
                physical_column=qual,
                classification=meta.get("classification", "confidential_business"),
                sensitivity_level=meta.get("level", "confidential"),
                default_access="deny",  # hard rule §23.7
                allowed_roles=meta.get("allowed_roles", []),
            )
        )
    return sorted(rules, key=lambda r: r.physical_column)


# --- helpers ------------------------------------------------------------------


def _column_to_attr_index(candidate: CandidateSemanticModel) -> dict[str, str]:
    idx: dict[str, str] = {}
    for e in candidate.entities:
        for a in e.attributes:
            idx[a.physical_column.lower()] = f"{e.entity_key}.{a.attribute_key}"
    return idx


def _resolve_field(field_name: str, catalog: PhysicalCatalog) -> Optional[str]:
    """Map a bare policy field ('credit_limit') to a real 'table.column'."""
    if "." in field_name and catalog.has_column(field_name):
        return field_name.lower()
    bare = field_name.split(".")[-1]
    for t in catalog.tables:
        if t.column(bare):
            return f"{t.name}.{bare}".lower()
    return None


def _schema_sensitive(catalog: PhysicalCatalog) -> set[str]:
    """Columns the schema author marked [SENSITIVE]."""
    return {
        f"{t.name}.{c.name}".lower()
        for t in catalog.tables
        for c in t.columns
        if "SENSITIVE" in c.markers
    }


def _drop_unreal(candidate: CandidateSemanticModel, catalog: PhysicalCatalog, errors: list[str]) -> None:
    kept_entities = []
    for e in candidate.entities:
        if not catalog.table(e.primary_table):
            errors.append(f"dropped entity {e.entity_key!r}: unknown table {e.primary_table!r}")
            continue
        e.attributes = [
            a for a in e.attributes
            if catalog.has_column(a.physical_column)
            or errors.append(f"dropped attr {e.entity_key}.{a.attribute_key}: "
                             f"unknown column {a.physical_column!r}")
        ]
        kept_entities.append(e)
    candidate.entities = kept_entities


def _schema_text(catalog: PhysicalCatalog) -> str:
    lines = []
    for t in catalog.tables:
        cols = []
        for c in t.columns:
            tag = f"{c.name} {c.type}"
            if c.enum_values:
                tag += f" enum{c.enum_values}"
            if c.markers:
                tag += " " + " ".join(f"[{m}]" for m in c.markers)
            cols.append(tag)
        pk = f" PK({', '.join(t.primary_key)})" if t.primary_key else ""
        lines.append(f"TABLE {t.name}({'; '.join(cols)}){pk}")
        for fk in t.foreign_keys:
            lines.append(
                f"  FK {t.name}.{','.join(fk.from_columns)} -> "
                f"{fk.to_table}.{','.join(fk.to_columns)}"
            )
    return "\n".join(lines)


def _loads_lenient(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):] if "{" in raw else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
