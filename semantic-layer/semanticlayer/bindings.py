"""Intent bindings (design §10) — deterministic join of policy + semantic model.

For each approved intent the policy names, bind it to the semantic entities and
attributes it may touch, the sensitive attributes it must NOT expose, the
mandatory filters and policies the runtime must enforce, and a trace requirement.

This is the most important runtime artifact: it is what turns "an intent" into a
bounded, enforceable contract. No LLM — it is a reviewable projection of the
(already approved) semantic model + policy hints.
"""

from __future__ import annotations

from .policy import PolicyHints
from .schema import IntentBinding, MandatoryFilter, SemanticModel, SensitivityRule


def build_bindings(
    model: SemanticModel,
    sensitivity: list[SensitivityRule],
    hints: PolicyHints,
) -> list[IntentBinding]:
    # Decide restriction by PHYSICAL COLUMN, so it is robust no matter how the
    # sensitivity rule is keyed (Entity.attr vs table.col). An attribute whose
    # column is restricted is never an allowed attribute (sensitive -> deny).
    restricted_cols = {s.physical_column.lower() for s in sensitivity}

    bindings: list[IntentBinding] = []
    for intent in hints.intents:
        required = _required_entities(intent, model, hints)
        allowed, restricted = [], []
        for e in model.entities:
            if e.entity_key not in required:
                continue
            for a in e.attributes:
                key = f"{e.entity_key}.{a.attribute_key}"
                (restricted if a.column.lower() in restricted_cols else allowed).append(key)
        restricted = sorted(restricted)
        filters = [
            MandatoryFilter(semantic_filter_id=fid, expression=expr)
            for fid, expr in hints.mandatory_filters_for_intent(intent)
        ]
        bindings.append(
            IntentBinding(
                intent_id=intent,
                description=_describe(intent),
                required_entities=required,
                allowed_attributes=allowed,
                restricted_attributes=restricted,
                mandatory_filters=filters,
                policies=hints.policies_for_intent(intent),
                trace_required=True,
            )
        )
    return bindings


def _required_entities(intent: str, model: SemanticModel, hints: PolicyHints) -> list[str]:
    """Entities whose columns the intent's policy fields reference; else fall back."""
    field_to_entity = _field_index(model)
    found: list[str] = []
    for rule in hints.rules_for_intent(intent):
        for cond in rule.data_conditions():
            ent = field_to_entity.get(str(cond.get("field", "")).split(".")[-1].lower())
            if ent and ent not in found:
                found.append(ent)
        for f in rule.restricted_fields:
            ent = field_to_entity.get(f.split(".")[-1].lower())
            if ent and ent not in found:
                found.append(ent)
    if not found and model.entities:
        # Generic fallback: an entity whose name/table matches a noun in the
        # intent (find_customers -> customers), else the first entity.
        nouns = set(intent.lower().split("_"))
        root = next(
            (e.entity_key for e in model.entities
             if {e.entity_key.lower(), e.primary_table.lower(),
                 e.primary_table.lower().rstrip("s")} & nouns
             or e.primary_table.lower() in {n + "s" for n in nouns}),
            model.entities[0].entity_key,
        )
        found = [root]
    return found


def _field_index(model: SemanticModel) -> dict[str, str]:
    """bare column/attribute name -> entity_key."""
    idx: dict[str, str] = {}
    for e in model.entities:
        for a in e.attributes:
            idx.setdefault(a.attribute_key.lower(), e.entity_key)
            idx.setdefault(a.column.split(".")[-1].lower(), e.entity_key)
    return idx


def _describe(intent: str) -> str:
    return intent.replace("_", " ").capitalize() + "."
