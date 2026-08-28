"""Rule-pack compiler: a published skill's approved rules -> eval-engine's
rule_pack.yaml (autonomous_build.md §3.3, Phase B step 9).

Compiles from the SAME (CandidateRule, Clause) inputs `artifacts.py` already
uses to render extracted_rules.yaml - the rule pack is one more artifact
written alongside it at publish time, not a re-query of a separate store
path. Only review_status == "approved" rules are compiled (extracted_rules.yaml
itself is "active rules only" - see skill-builder/CLAUDE.md).

Lowers skill-builder's flat rule_type vocabulary into the three engines
eval-engine's family1 actually runs (temporal | predicate | content),
rejecting anything unlowerable rather than shipping a best-effort guess
(Hard Rule 10). The mapping is defined ONCE, here.

Two distinct failure modes, per autonomous_build.md §3.3:
  - a rule with NO materialized source citation is a hard COMPILE ERROR
    (raises) - the compiler's job is to preserve that citation, never to
    invent one, so a rule without it cannot be shipped at all.
  - a rule whose rule_type has no generic lowering is REJECTED (recorded in
    the pack's `rejected` list with a reason) - the rest of the pack still
    compiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import CandidateRule, Clause, Condition

# skill-builder rule_type -> engine. The check-families vocabulary
# (precondition | sequencing | prohibition | field_restriction | approval_gate)
# is documentation only here - what matters at runtime is which of the three
# engines evaluates the rule.
#
# Note: no rule_type maps to "temporal". skill-builder's flat IR (schema.py)
# has no ordering/sequencing construct - every rule_type is a fact/condition
# check (predicate) or a field scan (content). eval-engine's family1/temporal.py
# is real, generic machinery (ready for a future rule source that DOES express
# ordering), but today's compiler never emits an `engine: temporal` rule.
_LOWERING: dict[str, str] = {
    "approval_threshold": "predicate",
    "data_access": "content",
    "regional_access": "predicate",
    "restriction": "predicate",
    "mandatory_filter": "predicate",
}

_UNLOWERABLE: dict[str, str] = {
    "exception": (
        "an exception rule modifies another rule's applicability rather than "
        "standing alone as a checkable condition; the compiler has no "
        "cross-rule composition mechanism"
    ),
    "audit_requirement": (
        "an audit_requirement asserts an audit action occurred but does not "
        "name the intent/fact that constitutes that record; the compiler "
        "cannot construct a checkable automaton without one"
    ),
}

# skill-builder Decision -> eval-engine verdict Effect. "mask"/"escalate" have
# no direct OOB-shadow equivalent (masking is a runtime data transform, not a
# verdict) - both fold to the closest verdict-level severity.
_EFFECT_MAP: dict[str, str] = {
    "allow": "allow",
    "approval_required": "approval_required",
    "block": "block",
    "mask": "block",
    "escalate": "approval_required",
}

_OP_TEXT = {"==": "==", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=", "in": "in", "not_in": "not in"}


class CompileError(Exception):
    """A rule cannot ship at all - not the same as a soft rejection."""


@dataclass
class RejectedRule:
    rule_key: str
    rule_type: str
    reason: str


@dataclass
class CompiledRule:
    rule_id: str
    engine: str
    effect: str
    source: dict[str, Any]
    conditions: list[dict[str, Any]] = field(default_factory=list)
    expr: str = ""
    approver_roles: list[str] = field(default_factory=list)
    detectors: list[dict[str, Any]] = field(default_factory=list)
    applies_to_intents: list[str] = field(default_factory=list)


def _render_expr(conditions: list[Condition]) -> str:
    return " and ".join(f"{c.field} {_OP_TEXT.get(c.operator, c.operator)} {c.value!r}" for c in conditions)


def _source_block(rule: CandidateRule, clause: Optional[Clause]) -> dict[str, Any]:
    return {
        "document": clause.document_id if clause else None,
        "section": clause.section_path if clause else "",
        "page": clause.page_number if clause else None,
        "text": clause.source_text if clause else "",
    }


def compile_rule(rule: CandidateRule, clause: Optional[Clause]) -> CompiledRule | RejectedRule:
    if clause is None or not clause.source_text.strip():
        raise CompileError(f"rule {rule.rule_key!r} has no materialized source citation block")
    if rule.rule_type in _UNLOWERABLE:
        return RejectedRule(rule.rule_key, rule.rule_type, _UNLOWERABLE[rule.rule_type])
    engine = _LOWERING.get(rule.rule_type)
    if engine is None:
        return RejectedRule(rule.rule_key, rule.rule_type, f"unknown rule_type {rule.rule_type!r}")

    compiled = CompiledRule(
        rule_id=rule.rule_key,
        engine=engine,
        effect=_EFFECT_MAP.get(rule.effect.decision, "flag"),
        source=_source_block(rule, clause),
        conditions=[{"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions],
        expr=_render_expr(rule.conditions),
        applies_to_intents=rule.applies_to_intents,
    )
    if engine == "predicate" and rule.effect.approver_role:
        compiled.approver_roles = [rule.effect.approver_role]
    if engine == "content":
        compiled.detectors = [{
            "field_names": rule.effect.restricted_fields or [],
            "scopes": ["result", "final_answer"],
        }]
    return compiled


def compile_rule_pack(
    skill_id: str, skill_version: str, rules: list[CandidateRule], clause_by_id: dict[str, Clause]
) -> dict[str, Any]:
    """Compile every APPROVED rule in a published skill. Raises CompileError
    (halts the whole pack) on a missing source citation; collects unlowerable
    rule_types into `rejected` instead (the rest of the pack still ships)."""
    compiled: list[CompiledRule] = []
    rejected: list[RejectedRule] = []
    for r in rules:
        if r.review_status != "approved":
            continue
        clause = clause_by_id.get(r.source_clause_id or "")
        out = compile_rule(r, clause)
        (rejected if isinstance(out, RejectedRule) else compiled).append(out)  # type: ignore[arg-type]

    def _rule_doc(c: CompiledRule) -> dict[str, Any]:
        doc: dict[str, Any] = {"rule_id": c.rule_id, "engine": c.engine, "effect": c.effect}
        if c.conditions:
            doc["conditions"] = c.conditions
        if c.expr:
            doc["expr"] = c.expr
        if c.approver_roles:
            doc["approver_roles"] = c.approver_roles
        if c.detectors:
            doc["detectors"] = c.detectors
        if c.applies_to_intents:
            doc["applies_to_intents"] = c.applies_to_intents
        doc["source"] = c.source
        return doc

    return {
        "rule_pack": {
            "version": 1,
            "source_skill": skill_id,
            "source_skill_version": skill_version,
            "rules": [_rule_doc(c) for c in compiled],
            "rejected": [{"rule_key": r.rule_key, "rule_type": r.rule_type, "reason": r.reason} for r in rejected],
        }
    }
