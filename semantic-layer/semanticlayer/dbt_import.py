"""Ingest a customer-supplied **dbt semantic model** (+ a Prefront governance
overlay) into the published semantic-layer contract — deterministically, with NO
LLM.

This is an alternate design-time front-end. Where ``mapper.suggest`` + ``promote``
ask an LLM to *guess* a candidate model from the bare schema, this module
*translates* an authoritative, customer-authored model:

    dbt semantic_models.yaml   ──structure──▶  SemanticEntity / SemanticAttribute
                               ──entities────▶  candidate joins ─(FK-validated)─▶ Relationship
    prefront_overlay.yaml      ──governance──▶  PolicyHints (rules+intents) + SensitivityRule

The two inputs are split by what each format can express. dbt is an *analytics*
semantic layer: it models structure (entities, dimensions/measures, implicit
joins) but has no notion of an operation/intent, a caller, or a restricted field.
So all of Prefront's governance — which intents exist, the rules that gate them,
per-attribute sensitivity, caller scoping, derived metrics — lives in the
**overlay**, a small sidecar in the same shape skill-builder already emits.

Everything this module returns then flows through the EXISTING deterministic tail
(``build_bindings`` → ``build_query_templates`` → ``build_tools`` → ``validate``)
unchanged: the customer's model is just "a candidate that didn't come from the
LLM", and it still cannot reach the runtime without passing the §19/§23 gate
against the real physical catalog. In particular:

  * a dbt entity whose ``model`` is not a real table is dropped + reported;
  * a dbt dimension/measure whose column is not real is dropped + reported;
  * a dbt implicit join that is NOT backed by a real foreign key is **dropped +
    reported, never auto-approved** — Prefront relationships must be FK-backed
    (design §7, §19.4). That FK discipline is the one place dbt's model and
    Prefront's contract genuinely differ, so it is surfaced loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from .logutil import get_logger
from .policy import PolicyHints, policy_hints_from_extracted
from .schema import (
    Join,
    PhysicalCatalog,
    Relationship,
    SemanticAttribute,
    SemanticEntity,
    SemanticModel,
    SensitivityRule,
)

log = get_logger(__name__)

# dbt references a table as ref('customers') or source('raw','customers'); accept
# either, or a bare table name. The captured group is the physical table.
_REF = re.compile(r"""(?:ref|source)\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"]\s*)?\)""")
# dbt dimension types → Prefront attribute business types (best-effort; the
# catalog column's real type is what the runtime trusts).
_DIM_TYPE = {"categorical": "string", "time": "date"}


# --- Overlay (the Prefront governance sidecar) -------------------------------


class OverlaySensitivity(BaseModel):
    """An explicit sensitivity classification for one attribute/column.

    ``column`` accepts either a physical ``table.column`` or a semantic
    ``Entity.attribute`` key (resolved against the imported model)."""

    column: str
    classification: str = "confidential_business"
    sensitivity_level: str = "restricted"
    allowed_roles: list[str] = Field(default_factory=list)


class Overlay(BaseModel):
    """The Prefront governance overlay paired with a dbt semantic model.

    ``rules`` use the same shape skill-builder emits in ``extracted_rules.yaml``
    (``rule_key``/``rule_type``/``conditions``/``effect``/``applies_to_intents``)
    so they flow straight into ``policy_hints_from_extracted`` and the existing
    bindings/templates/tools tail."""

    overlay_version: str = "1"
    domain: str = "general"
    # dbt model name → physical table, when the dbt `model:` can't be parsed or
    # differs from the real table. Default: parse ref()/identity.
    model_table_map: dict[str, str] = Field(default_factory=dict)
    # Operations to expose. Seeds/overrides intents the rules carry.
    intents: list[str] = Field(default_factory=list)
    # Governance rules (skill-builder extracted-rule shape).
    rules: list[dict] = Field(default_factory=list)
    # Explicit per-attribute sensitivity (merged with schema [SENSITIVE] markers
    # and rule-derived restricted fields).
    sensitivity: list[OverlaySensitivity] = Field(default_factory=list)
    # Application vocabulary (never hardcoded in Prefront).
    metrics: dict[str, str] = Field(default_factory=dict)
    caller_context: dict[str, str] = Field(default_factory=dict)


# --- dbt semantic-model intermediate shapes ----------------------------------


class DbtEntity(BaseModel):
    name: str
    type: str = "foreign"  # primary | foreign | unique | natural
    expr: Optional[str] = None


class DbtField(BaseModel):
    name: str
    type: Optional[str] = None
    expr: Optional[str] = None
    agg: Optional[str] = None  # measures only


class DbtSemanticModel(BaseModel):
    name: str
    model: Optional[str] = None
    primary_entity: Optional[str] = None
    entities: list[DbtEntity] = Field(default_factory=list)
    dimensions: list[DbtField] = Field(default_factory=list)
    measures: list[DbtField] = Field(default_factory=list)

    def primary(self) -> Optional[DbtEntity]:
        """The entity that identifies this model's rows (design: exactly one)."""
        named = {e.name for e in self.entities}
        if self.primary_entity and self.primary_entity in named:
            return next(e for e in self.entities if e.name == self.primary_entity)
        return next((e for e in self.entities if e.type in ("primary", "natural", "unique")), None)


# --- Report ------------------------------------------------------------------


@dataclass
class TranslationReport:
    """What the importer did, surfaced to the reviewer in the UI/CLI.

    Nothing here is silently dropped: every entity/attribute/join the importer
    could not honor is listed with a reason."""

    entities: list[dict] = field(default_factory=list)
    relationships_approved: list[dict] = field(default_factory=list)
    relationships_dropped: list[dict] = field(default_factory=list)
    sensitivity: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "entities": self.entities,
            "relationships_approved": self.relationships_approved,
            "relationships_dropped": self.relationships_dropped,
            "sensitivity": self.sensitivity,
            "intents": self.intents,
            "warnings": self.warnings,
        }


@dataclass
class ImportResult:
    model: SemanticModel
    relationships: list[Relationship]
    sensitivity: list[SensitivityRule]
    hints: PolicyHints
    report: TranslationReport


# --- Parsing -----------------------------------------------------------------


def parse_dbt(data: str | dict) -> list[DbtSemanticModel]:
    """Parse a dbt YAML doc (or already-loaded dict) into semantic models.

    Accepts the standard ``{semantic_models: [...]}`` shape, a bare list, or a
    single model dict — whatever the customer pastes/uploads."""
    if isinstance(data, str):
        log.debug("parse_dbt: loading YAML (%d chars)", len(data))
        data = yaml.safe_load(data) or {}
    if isinstance(data, dict):
        raw = data.get("semantic_models", data.get("semantic_model", [data]))
    else:
        raw = data
    if isinstance(raw, dict):
        raw = [raw]
    models = [DbtSemanticModel.model_validate(m) for m in (raw or [])]
    log.debug("parse_dbt: %d semantic model(s): %s", len(models), [m.name for m in models])
    return models


def parse_overlay(data: str | dict) -> Overlay:
    """Parse the Prefront governance overlay YAML (or dict)."""
    if isinstance(data, str):
        log.debug("parse_overlay: loading YAML (%d chars)", len(data))
        data = yaml.safe_load(data) or {}
    overlay = Overlay.model_validate(data or {})
    log.debug(
        "parse_overlay: domain=%s intents=%s rules=%d sensitivity=%d metrics=%d caller_ctx=%d",
        overlay.domain, overlay.intents, len(overlay.rules), len(overlay.sensitivity),
        len(overlay.metrics), len(overlay.caller_context),
    )
    return overlay


# --- Translation -------------------------------------------------------------


def to_prefront(
    dbt_models: list[DbtSemanticModel],
    overlay: Overlay,
    catalog: PhysicalCatalog,
    *,
    model_id: str,
    domain: Optional[str] = None,
    version: str = "1.0",
) -> ImportResult:
    """Translate dbt structure + overlay governance into the published contract."""
    domain = domain or overlay.domain
    log.debug(
        "to_prefront: model_id=%s domain=%s version=%s | dbt_models=%d catalog_tables=%d",
        model_id, domain, version, len(dbt_models), len(catalog.tables),
    )
    report = TranslationReport()

    # 1) Governance hints from the overlay rules (reuses the existing builder).
    hints = policy_hints_from_extracted(
        {"domain": domain, "rules": overlay.rules, "skill_id": f"{model_id}_overlay"}
    )
    if overlay.intents:
        hints.intents = list(overlay.intents)
    report.intents = list(hints.intents)
    log.debug("to_prefront: hints intents=%s restricted_fields=%s",
              hints.intents, list(hints.restricted_fields))
    if not hints.intents:
        report.warnings.append(
            "overlay declares no intents — no MCP tools will be generated "
            "(dbt has no concept of an operation; list intents in the overlay)"
        )

    # 2) Entities + attributes from dbt models (validated against the catalog).
    entities = _build_entities(dbt_models, overlay, catalog, report)

    # 3) Sensitivity: overlay overrides ∪ schema markers ∪ rule-derived fields.
    sensitivity = _build_sensitivity(overlay, catalog, hints, entities, report)
    sens_cols = {s.physical_column.lower() for s in sensitivity}

    # 4) Stamp sensitivity onto attributes / entities (sensitive → restricted).
    for e in entities:
        for a in e.attributes:
            if a.column.lower() in sens_cols:
                a.sensitivity_level = "restricted"
        e.restricted = any(a.sensitivity_level == "restricted" for a in e.attributes)

    model = SemanticModel(
        semantic_model_id=model_id,
        version=version,
        status="draft",  # promoted to 'published' by the pipeline iff validation passes
        domain=domain,
        approved_by="dbt_import",
        generated_by="dbt-import",
        entities=entities,
    )

    # 5) Relationships from dbt's implicit (entity-name) joins, FK-validated.
    relationships = _build_relationships(dbt_models, model, catalog, sens_cols, overlay, report)

    log.debug(
        "to_prefront: done — entities=%d attributes=%d sensitivity=%d "
        "relationships(approved)=%d relationships(dropped)=%d warnings=%d",
        len(entities), sum(len(e.attributes) for e in entities), len(sensitivity),
        len(relationships), len(report.relationships_dropped), len(report.warnings),
    )
    return ImportResult(model, relationships, sensitivity, hints, report)


def _resolve_table(sm: DbtSemanticModel, overlay: Overlay, catalog: PhysicalCatalog) -> Optional[str]:
    """Map a dbt semantic model to a real physical table name (or None)."""
    if sm.name in overlay.model_table_map:
        cand = overlay.model_table_map[sm.name]
        log.debug("_resolve_table: %s -> %s (overlay map)", sm.name, cand)
    elif sm.model:
        m = _REF.search(sm.model)
        cand = (m.group(2) or m.group(1)) if m else sm.model.strip()
        log.debug("_resolve_table: %s -> %s (from model=%r)", sm.name, cand, sm.model)
    else:
        cand = sm.name
        log.debug("_resolve_table: %s -> %s (identity)", sm.name, cand)
    tbl = catalog.table(cand)
    return tbl.name if tbl else None


def _build_entities(
    dbt_models: list[DbtSemanticModel],
    overlay: Overlay,
    catalog: PhysicalCatalog,
    report: TranslationReport,
) -> list[SemanticEntity]:
    """One SemanticEntity per dbt model; attributes from dimensions + measures +
    entity key columns. Drops anything not backed by a real catalog column."""
    entities: list[SemanticEntity] = []

    for sm in dbt_models:
        table = _resolve_table(sm, overlay, catalog)
        if not table:
            msg = f"dropped entity {sm.name!r}: dbt model maps to no real table"
            log.warning("_build_entities: %s", msg)
            report.warnings.append(msg)
            continue
        tbl = catalog.table(table)

        # Primary key: the primary entity's expr/name, else the catalog PK.
        pk_entity = sm.primary()
        pk_col = (pk_entity.expr or pk_entity.name) if pk_entity else None
        if not (pk_col and tbl.column(pk_col)):
            pk_col = tbl.primary_key[0] if tbl.primary_key else None
        primary_key = f"{table}.{pk_col}" if pk_col else ""

        attrs: list[SemanticAttribute] = []
        seen_cols: set[str] = set()

        def _add(akey: str, col_name: str, btype: str) -> None:
            qual = f"{table}.{col_name}"
            if not tbl.column(col_name):
                msg = f"dropped attr {sm.name}.{akey}: unknown column {qual!r}"
                log.warning("_build_entities: %s", msg)
                report.warnings.append(msg)
                return
            if col_name.lower() in seen_cols:
                return
            seen_cols.add(col_name.lower())
            col = tbl.column(col_name)
            attrs.append(SemanticAttribute(
                attribute_key=akey, column=qual, type=btype, required=not col.nullable,
            ))

        # Dimensions, then measures (only column-backed measures become attrs).
        for d in sm.dimensions:
            _add(d.name, d.expr or d.name, _DIM_TYPE.get((d.type or "").lower(), "string"))
        for m in sm.measures:
            expr = m.expr or m.name
            if not _is_bare_column(expr):
                msg = (f"measure {sm.name}.{m.name} routed to metrics — aggregate "
                       f"expr {expr!r} is not a stored column")
                log.debug("_build_entities: %s", msg)
                report.warnings.append(msg)
                continue
            _add(m.name, expr, "number")
        # Entity key columns (primary/foreign/unique) so scoping/keys are
        # selectable. Key attributes are named for their COLUMN (customer_id),
        # not the dbt entity (customer), so the attribute_key — and therefore the
        # template's result-column name — matches the actual SQL projection.
        for ent in sm.entities:
            col_name = ent.expr or ent.name
            if tbl.column(col_name):
                _add(col_name, col_name, _col_btype(tbl.column(col_name)))

        entities.append(SemanticEntity(
            entity_key=sm.name,
            description=f"Imported from dbt semantic model '{sm.name}'.",
            primary_table=table,
            primary_key=primary_key,
            attributes=attrs,
        ))
        report.entities.append({
            "entity": sm.name, "table": table, "primary_key": primary_key,
            "attributes": [a.attribute_key for a in attrs],
        })
        log.debug("_build_entities: %s -> table=%s pk=%s attrs=%d",
                  sm.name, table, primary_key, len(attrs))

    return entities


def _build_relationships(
    dbt_models: list[DbtSemanticModel],
    model: SemanticModel,
    catalog: PhysicalCatalog,
    sens_cols: set[str],
    overlay: Overlay,
    report: TranslationReport,
) -> list[Relationship]:
    """Derive joins from dbt's implicit entity-name matching, then keep ONLY the
    ones a real foreign key backs (design §7/§19.4). Report the rest."""
    # Map a dbt *entity name* (the join key's identity) to the model that owns it
    # as its primary entity, plus that model's primary column.
    primary_owner: dict[str, tuple[str, str]] = {}
    for sm in dbt_models:
        p = sm.primary()
        table = model.entity(sm.name).primary_table if model.entity(sm.name) else None
        if p and table:
            primary_owner[p.name] = (sm.name, p.expr or p.name)

    rels: list[Relationship] = []
    seen: set[tuple[str, str]] = set()
    for sm in dbt_models:
        from_entity = sm.name  # dbt model name == Prefront entity_key
        from_tbl = model.entity(sm.name).primary_table if model.entity(sm.name) else None
        if not from_tbl:
            continue
        for fe in sm.entities:
            if fe.type not in ("foreign",):
                continue
            owner = primary_owner.get(fe.name)
            if not owner or owner[0] == sm.name:
                continue  # no other model identified by this entity name
            to_entity, to_pcol = owner
            to_tbl = model.entity(to_entity).primary_table
            from_col = f"{from_tbl}.{fe.expr or fe.name}"
            to_col = f"{to_tbl}.{to_pcol}"
            key_pair = (from_col.lower(), to_col.lower())
            if key_pair in seen:
                continue
            seen.add(key_pair)

            backed = _fk_backed(catalog, from_tbl, fe.expr or fe.name, to_tbl, to_pcol)
            join_desc = {"from": from_col, "to": to_col,
                         "from_entity": from_entity, "to_entity": to_entity}
            if not (catalog.has_column(from_col) and catalog.has_column(to_col)):
                join_desc["reason"] = "join column not found in catalog"
                log.warning("_build_relationships: dropped %s -> %s (missing column)",
                            from_col, to_col)
                report.relationships_dropped.append(join_desc)
                continue
            if not backed:
                join_desc["reason"] = "no real foreign key backs this dbt join (not approved)"
                log.warning("_build_relationships: dropped %s -> %s (no FK backing)",
                            from_col, to_col)
                report.relationships_dropped.append(join_desc)
                continue
            rels.append(Relationship(
                relationship_key=f"{from_entity}_to_{to_entity}",
                from_entity=from_entity, to_entity=to_entity,
                join=Join(**{"from": from_col, "to": to_col}),
                cardinality="many_to_one", approved=True,
                restricted=(from_col.lower() in sens_cols or to_col.lower() in sens_cols),
            ))
            log.debug("_build_relationships: approved %s -> %s (FK-backed)", from_col, to_col)
            report.relationships_approved.append(join_desc)

    return rels


def _fk_backed(catalog: PhysicalCatalog, from_tbl: str, from_col: str,
               to_tbl: str, to_col: str) -> bool:
    """True iff a real FK connects ``from_tbl.from_col`` and ``to_tbl.to_col``
    (in either direction — dbt does not order the join the way the FK is declared)."""
    def _match(t_name: str, col: str, target: str, target_col: str) -> bool:
        t = catalog.table(t_name)
        if not t:
            return False
        for fk in t.foreign_keys:
            if (fk.to_table.lower() == target.lower()
                    and col.lower() in [c.lower() for c in fk.from_columns]
                    and target_col.lower() in [c.lower() for c in fk.to_columns]):
                return True
        return False

    return (_match(from_tbl, from_col, to_tbl, to_col)
            or _match(to_tbl, to_col, from_tbl, from_col))


def _build_sensitivity(
    overlay: Overlay,
    catalog: PhysicalCatalog,
    hints: PolicyHints,
    entities: list[SemanticEntity],
    report: TranslationReport,
) -> list[SensitivityRule]:
    """Merge sensitivity from three sources, all defaulting to ``deny`` (§23.7):
    explicit overlay entries, schema [SENSITIVE] markers, and rule-derived
    restricted fields."""
    attr_to_col = {f"{e.entity_key}.{a.attribute_key}".lower(): a.column.lower()
                   for e in entities for a in e.attributes}
    col_to_attr = {v: k for k, v in attr_to_col.items()}

    cols: dict[str, dict] = {}

    # (a) explicit overlay sensitivity
    for s in overlay.sensitivity:
        qual = _resolve_sensitivity_key(s.column, attr_to_col, catalog)
        if not qual:
            msg = f"overlay sensitivity {s.column!r} does not resolve to a real column — ignored"
            log.warning("_build_sensitivity: %s", msg)
            report.warnings.append(msg)
            continue
        cols.setdefault(qual, {}).update(
            classification=s.classification, level=s.sensitivity_level,
            allowed_roles=s.allowed_roles,
        )

    # (b) schema [SENSITIVE] markers
    for t in catalog.tables:
        for c in t.columns:
            if "SENSITIVE" in c.markers:
                qual = f"{t.name}.{c.name}".lower()
                cols.setdefault(qual, {}).setdefault("classification", "confidential_business")
                cols[qual].setdefault("level", "restricted")

    # (c) rule-derived restricted fields (from the overlay's rules)
    for fname, meta in hints.restricted_fields.items():
        qual = _resolve_field(fname, catalog)
        if not qual:
            continue
        entry = cols.setdefault(qual, {})
        entry.setdefault("classification", meta["classification"])
        entry.setdefault("level", "confidential")
        entry.setdefault("allowed_roles", meta.get("allowed_roles", []))

    rules: list[SensitivityRule] = []
    for qual, meta in cols.items():
        if not catalog.has_column(qual):
            continue
        rules.append(SensitivityRule(
            key=col_to_attr.get(qual, qual),
            physical_column=qual,
            classification=meta.get("classification", "confidential_business"),
            sensitivity_level=meta.get("level", "confidential"),
            default_access="deny",  # hard rule §23.7
            allowed_roles=meta.get("allowed_roles", []),
        ))
    rules.sort(key=lambda r: r.physical_column)
    report.sensitivity = [r.physical_column for r in rules]
    log.debug("_build_sensitivity: %d restricted column(s): %s",
              len(rules), report.sensitivity)
    return rules


def _resolve_sensitivity_key(key: str, attr_to_col: dict[str, str],
                             catalog: PhysicalCatalog) -> Optional[str]:
    """Resolve an overlay sensitivity key (Entity.attr OR table.col OR bare) to
    a real ``table.column``."""
    low = key.lower()
    if low in attr_to_col:
        return attr_to_col[low]
    if catalog.has_column(key):
        return low
    return _resolve_field(key, catalog)


def _resolve_field(field_name: str, catalog: PhysicalCatalog) -> Optional[str]:
    """Map a bare field ('credit_limit') or 'table.col' to a real lowercased
    'table.column' (mirrors mapper._resolve_field)."""
    if "." in field_name and catalog.has_column(field_name):
        return field_name.lower()
    bare = field_name.split(".")[-1]
    for t in catalog.tables:
        if t.column(bare):
            return f"{t.name}.{bare}".lower()
    return None


# --- small helpers -----------------------------------------------------------


def _is_bare_column(expr: str) -> bool:
    """A measure/dimension expr that is a single column name (not an aggregate)."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", (expr or "").strip()))


def _col_btype(col: Any) -> str:
    t = (col.type or "").lower()
    if any(s in t for s in ("int", "numeric", "decimal", "real", "double", "float", "money")):
        return "number"
    if "date" in t or "timestamp" in t:
        return "date"
    return "string"
