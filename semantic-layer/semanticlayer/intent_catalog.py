"""intent_catalog.yaml (autonomous_build.md §3.4): Family 3's approved-intent
catalog - eval-engine's call/scope/session checks (`../eval-engine/evalengine/
family3/`) evaluate every session against this artifact.

Generated from an already-built semantic model's bindings/tools/policy hints
where they exist (`build_intent_catalog`, below) - a faithful, reviewable
projection of already-bound artifacts, same posture as `policy.py` (no LLM,
no I/O beyond the explicit load/dump helpers). A bring-your-own-agent
deployment with no semantic-layer binding at all (e.g. LoanPro) authors one
by hand instead - see `loanpro-demo/policy/intent_catalog.yaml`, which this
module's schema matches field-for-field.

Fields with no upstream source in the bound model (`allowed_callers.channels`,
`toxic_with`, `closing_obligation`, `trigger_descriptors`, `trust`,
`expected_volume`) are left at their defaults by `build_intent_catalog` -
review-and-fill, never invented, matching every other candidate artifact in
this repo.

No wired API endpoint yet (nothing downstream calls this module's functions
over HTTP) - these are library functions, ready for a future
`/design/semantic/intent-catalog` endpoint the same way `mcptools.build_tools`
was library code before it got one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from .policy import PolicyHints
from .schema import IntentBinding, McpTool, QueryTemplate


class AllowedCallers(BaseModel):
    roles: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class ExpectedVolume(BaseModel):
    rows_p99: Optional[int] = None


class IntentCatalogEntry(BaseModel):
    intent: str
    tool_name: str = ""
    params: list[str] = Field(default_factory=list)
    side_effect: Literal["read", "write"] = "read"
    allowed_callers: AllowedCallers = Field(default_factory=AllowedCallers)
    fields: list[str] = Field(default_factory=list)
    restricted_fields: list[str] = Field(default_factory=list)
    mandatory_filters: list[str] = Field(default_factory=list)
    expected_volume: ExpectedVolume = Field(default_factory=ExpectedVolume)
    closing_obligation: Optional[str] = None
    toxic_with: list[str] = Field(default_factory=list)
    trigger_descriptors: list[str] = Field(default_factory=list)
    trust: str = "trusted"
    policy: list[str] = Field(default_factory=list)


class IntentCatalog(BaseModel):
    version: int = 1
    intents: list[IntentCatalogEntry] = Field(default_factory=list)

    def by_intent(self) -> dict[str, IntentCatalogEntry]:
        return {e.intent: e for e in self.intents}


def load_intent_catalog(path: str) -> IntentCatalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return IntentCatalog.model_validate((raw or {}).get("intent_catalog") or {})


def dump_intent_catalog(catalog: IntentCatalog) -> str:
    doc = {"intent_catalog": catalog.model_dump(exclude_none=True)}
    return yaml.dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)


def validate_intent_catalog(catalog: IntentCatalog) -> list[str]:
    """Structural cross-reference checks only - unknown closing_obligation /
    toxic_with targets, missing roles. Does not (cannot, without a trace)
    check whether the catalog matches reality; that's the grading harness's
    job (autonomous_build.md step 15)."""
    errors: list[str] = []
    names = {e.intent for e in catalog.intents}
    for e in catalog.intents:
        if e.closing_obligation and e.closing_obligation not in names:
            errors.append(f"{e.intent}: closing_obligation {e.closing_obligation!r} is not a catalog intent")
        for other in e.toxic_with:
            if other not in names:
                errors.append(f"{e.intent}: toxic_with {other!r} is not a catalog intent")
        if not e.allowed_callers.roles:
            errors.append(f"{e.intent}: no allowed_callers.roles declared")
    return errors


def build_intent_catalog(
    bindings: list[IntentBinding],
    tools: list[McpTool],
    templates: list[QueryTemplate],
    hints: PolicyHints,
) -> IntentCatalog:
    """Candidate catalog from an already-built semantic model. Pure - no I/O,
    no LLM; every field either comes from `bindings`/`tools`/`templates`/
    `hints` or is left at its human-curated default."""
    tools_by_intent = {t.source_intent: t for t in tools}
    templates_by_intent = {t.intent_id: t for t in templates}
    entries: list[IntentCatalogEntry] = []
    for b in bindings:
        tool = tools_by_intent.get(b.intent_id)
        template = templates_by_intent.get(b.intent_id)
        side_effect: Literal["read", "write"] = "read" if (template is None or template.read_only) else "write"
        roles = (tool.allowed_roles if tool and tool.allowed_roles else None) or hints.allowed_roles_for_intent(b.intent_id)
        params = sorted((tool.input_schema.get("properties") or {}).keys()) if tool else []
        entries.append(IntentCatalogEntry(
            intent=b.intent_id,
            tool_name=b.intent_id,
            params=params,
            side_effect=side_effect,
            allowed_callers=AllowedCallers(roles=list(roles), channels=[]),
            fields=list(b.allowed_attributes),
            restricted_fields=list(b.restricted_attributes),
            mandatory_filters=[mf.expression for mf in b.mandatory_filters],
            policy=list(b.policies),
        ))
    return IntentCatalog(version=1, intents=entries)
