"""Policy loader: skill-builder output -> reusable hints (deterministic).

Reads the approved policy artifacts produced by the Skill Builder
(``extracted_rules.yaml`` + ``policy_skill.yaml`` under
``skills/<id>/v<ver>/``) and distills them into the facts the semantic layer
needs downstream:

  * ``intents``       — what the runtime can be asked to do (drives intent bindings + MCP tools)
  * ``data_fields``   — the per-request fields rules condition on (grounds the LLM mapper)
  * ``roles``         — caller/approver roles seen in the policy
  * ``restricted_fields`` — fields a rule restricts/governs (sensitivity hints)
  * per-intent **policies**, **mandatory filters**, **approval behavior**, **allowed roles**

No LLM here — this is a faithful, reviewable projection of already-approved rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Condition fields that name the caller, not per-request data.
_CALLER_FIELDS = {"caller.role", "role", "caller_role"}


@dataclass
class RuleHint:
    rule_key: str
    rule_type: str
    conditions: list[dict[str, Any]]
    decision: str
    approver_role: Optional[str] = None
    restricted_fields: list[str] = field(default_factory=list)
    message: str = ""
    applies_to_intents: list[str] = field(default_factory=list)

    def caller_roles(self) -> list[str]:
        """Roles named in a caller.role condition (the callers this rule is about)."""
        out: list[str] = []
        for c in self.conditions:
            if str(c.get("field", "")).lower() in _CALLER_FIELDS:
                v = c.get("value")
                out.extend(v if isinstance(v, list) else [v])
        return [str(r) for r in out if r]

    def data_conditions(self) -> list[dict[str, Any]]:
        return [
            c for c in self.conditions
            if str(c.get("field", "")).lower() not in _CALLER_FIELDS
        ]


@dataclass
class PolicyHints:
    skill_id: str
    version: str
    domain: str
    rules: list[RuleHint]
    intents: list[str]

    # -- vocabularies for grounding the LLM mapper ----------------------------

    @property
    def data_fields(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.rules:
            for c in r.data_conditions():
                f = str(c.get("field", "")).strip()
                if f:
                    seen.setdefault(f, None)
            for f in r.restricted_fields:
                seen.setdefault(f, None)
        return list(seen)

    @property
    def roles(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.rules:
            for role in r.caller_roles():
                seen.setdefault(role, None)
            if r.approver_role:
                # approver_role may pack several roles ("A, B") — keep verbatim.
                seen.setdefault(r.approver_role, None)
        return list(seen)

    @property
    def restricted_fields(self) -> dict[str, dict[str, Any]]:
        """field -> {classification, allowed_roles, source_rule} (sensitivity hints)."""
        out: dict[str, dict[str, Any]] = {}
        for r in self.rules:
            # Explicit restricted_fields lists, plus fields named in data_access rules.
            fields = list(r.restricted_fields)
            if r.rule_type == "data_access":
                fields += [str(c.get("field")) for c in r.data_conditions()]
            for f in fields:
                if not f:
                    continue
                entry = out.setdefault(
                    f, {"classification": _classify(f), "allowed_roles": [], "source_rule": r.rule_key}
                )
                # An *allow* data_access rule tells us which roles may read it.
                if r.decision == "allow":
                    for role in r.caller_roles():
                        if role not in entry["allowed_roles"]:
                            entry["allowed_roles"].append(role)
        return out

    # -- per-intent projections ----------------------------------------------

    def rules_for_intent(self, intent: str) -> list[RuleHint]:
        return [r for r in self.rules if intent in r.applies_to_intents]

    def policies_for_intent(self, intent: str) -> list[str]:
        return [self.skill_id] + [r.rule_key for r in self.rules_for_intent(intent)]

    def mandatory_filters_for_intent(self, intent: str) -> list[tuple[str, str]]:
        """(filter_id, expression) for each hard *block* rule on the intent.

        A block rule means 'reject when <conditions>'; the mandatory filter is
        therefore the positive guard ``NOT (<conditions>)`` the runtime must add.
        """
        out: list[tuple[str, str]] = []
        for r in self.rules_for_intent(intent):
            if r.decision == "block" and r.data_conditions():
                out.append((r.rule_key, "NOT (" + _expr(r.data_conditions()) + ")"))
        return out

    def approval_for_intent(self, intent: str) -> Optional[dict[str, Any]]:
        """First approval_required rule on the intent -> approval behavior."""
        for r in self.rules_for_intent(intent):
            if r.decision == "approval_required":
                return {
                    "may_require_approval": True,
                    "approval_condition": _expr(r.data_conditions()) or None,
                    "approval_role": r.approver_role,
                }
        return None

    def allowed_roles_for_intent(self, intent: str) -> list[str]:
        """Roles a rule explicitly *allows* to invoke this intent; else all roles."""
        out: list[str] = []
        for r in self.rules_for_intent(intent):
            if r.decision == "allow":
                for role in r.caller_roles():
                    if role not in out:
                        out.append(role)
        return out or self.roles


def _classify(field_name: str) -> str:
    f = field_name.lower()
    if "tax" in f or "ssn" in f or "contact" in f or "bank" in f:
        return "pii"
    if "credit" in f or "balance" in f or "risk" in f:
        return "financial_sensitive"
    return "confidential_business"


def _expr(conditions: list[dict[str, Any]]) -> str:
    parts = []
    for c in conditions:
        f, op, v = c.get("field"), c.get("operator"), c.get("value")
        v = f"'{v}'" if isinstance(v, str) else v
        parts.append(f"{f} {op} {v}")
    return " AND ".join(parts)


# --- loading ------------------------------------------------------------------


def policy_hints_from_extracted(extracted: dict, skill: dict | None = None) -> PolicyHints:
    """Build PolicyHints from the in-memory extracted_rules + (optional) skill dicts.

    ``extracted`` matches the skill-builder ``extracted_rules.yaml`` shape:
    ``{skill_id, document_version, domain, rules: [{rule_key, rule_type,
    conditions, effect, applies_to_intents}]}``.
    """
    extracted = extracted or {}
    skill = skill or {}
    rules: list[RuleHint] = []
    for r in extracted.get("rules", []) or []:
        effect = r.get("effect", {}) or {}
        rules.append(
            RuleHint(
                rule_key=r.get("rule_key", ""),
                rule_type=r.get("rule_type", "restriction"),
                conditions=r.get("conditions", []) or [],
                decision=effect.get("decision", ""),
                approver_role=effect.get("approver_role"),
                restricted_fields=effect.get("restricted_fields", []) or [],
                message=effect.get("message", ""),
                applies_to_intents=r.get("applies_to_intents", []) or [],
            )
        )

    intents: list[str] = []
    for src in (skill.get("applies_to", []) or []), *(r.applies_to_intents for r in rules):
        for i in src:
            if i and i not in intents:
                intents.append(i)

    return PolicyHints(
        skill_id=extracted.get("skill_id") or skill.get("skill_id", "policy"),
        version=str(extracted.get("document_version") or skill.get("version", "1.0")),
        domain=extracted.get("domain") or skill.get("domain", "general"),
        rules=rules,
        intents=intents,
    )


def load_policy(rules_dir: str | Path) -> PolicyHints:
    """Load a skill version directory (containing the skill-builder YAMLs)."""
    d = Path(rules_dir)
    extracted = yaml.safe_load((d / "extracted_rules.yaml").read_text(encoding="utf-8"))
    skill_path = d / "policy_skill.yaml"
    skill = yaml.safe_load(skill_path.read_text(encoding="utf-8")) if skill_path.exists() else {}
    return policy_hints_from_extracted(extracted, skill)
