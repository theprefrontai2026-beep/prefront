"""LLM candidate-rule extractor.

Wraps the NVIDIA NIM OpenAI-compatible endpoint (default model
``meta/llama-3.3-70b-instruct``) and the strict-JSON extraction prompt from
design.md. The model only ever produces *candidate* rules; every rule returned
here is ``review_status='pending'`` and must pass schema validation before it is
even shown to a reviewer.

Design rules honored:
  * "Do not summarize. Do not invent missing conditions."
  * "If the clause is only explanatory text, return no rules."
  * Output that fails schema validation is dropped (and reported), never coerced.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable, Optional

from pydantic import ValidationError

from .schema import CandidateRule, Clause

log = logging.getLogger(__name__)

# Provider presets. Each is an OpenAI-compatible endpoint; only base_url,
# default model, and the API-key env var differ. Pick one via SKILLBUILDER_PROVIDER
# (or the `provider=` arg); explicit base_url/model/api_key always override.
PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.3-70b-instruct",
        "key_env": "NVIDIA_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3",
        "key_env": "XAI_API_KEY",
        "key_env_alts": ["GROK_API_KEY"],
    },
    "groq": {  # Groq Cloud (gsk_ keys) — NOT xAI Grok. Hosts open models.
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "key_env_alts": ["XAI_API_KEY"],
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
}
# Friendly aliases -> canonical provider name.
PROVIDER_ALIASES = {"xai": "grok", "x.ai": "grok", "llama": "nvidia"}
DEFAULT_PROVIDER = "nvidia"

# Backwards-compatible aliases (referenced elsewhere / by tests).
DEFAULT_BASE_URL = PROVIDERS[DEFAULT_PROVIDER]["base_url"]
DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER]["model"]

# Clause types that never carry an enforceable rule — skip the LLM call entirely.
_SKIP_TYPES = {"definition", "explanatory"}

SYSTEM_PROMPT = (
    "You are extracting candidate runtime policy rules for Prefront, a governed "
    "runtime layer between AI agents and enterprise data sources. You are given ONE "
    "section of a policy document (the POLICY TEXT) and a REFERENCE VOCABULARY.\n\n"
    "Extract machine-enforceable rules ONLY from the POLICY TEXT. Hard requirements:\n"
    "- The REFERENCE VOCABULARY (domain, roles, fields, intents) is context only. "
    "NEVER create a rule whose subject is the vocabulary itself. Never output a rule "
    "whose condition.field is 'domain', 'role', 'known_domain', 'known_roles', "
    "'known_fields', 'known_intents', 'review_frequency', or 'log_retention_period'.\n"
    "- Each condition.field MUST be a concrete per-request data field the gateway can "
    "evaluate (e.g. credit_status, current_balance, order_value, discount_percentage, "
    "risk_rating, region_id, requested_fields, caller.role). Prefer fields from the "
    "REFERENCE VOCABULARY when they fit.\n"
    "- 'conditions' is a LIST of field/operator/value tests, ALL of which must hold "
    "(logical AND). When a control combines facts (e.g. 'high risk AND order over "
    "USD 50,000'), emit BOTH conditions in the list — do not drop the threshold.\n"
    "- Copy thresholds, numbers, field names, and role names VERBATIM from the POLICY "
    "TEXT. NEVER invent a number, threshold, value, or field that is not written there. "
    "Write numbers as plain numerics (50000, not 'USD 50,000').\n"
    "- Extract a rule for any enforceable control: an order/quote/credit decision, "
    "a field-access restriction, a region/territory scope, an approval requirement, "
    "a ROLE-BASED ACCESS permission, or a DATA-OWNERSHIP scope.\n"
    "- ROLE-BASED ACCESS (who may perform an action / reach a resource): enforce it "
    "deny-by-default. When a resource or action is limited to certain roles, emit a "
    "'restriction' that BLOCKS when the caller's role is not permitted — e.g. "
    "{conditions:[{field:'caller.role', operator:'not_in', value:['bank_teller','bank_manager']}], "
    "effect:{decision:'block'}, applies_to_intents:['view_users']}. An affirmative "
    "'Account Holders may apply for loans' is the permission for that intent; capture "
    "the corresponding access boundary as the rule.\n"
    "- DATA-OWNERSHIP ('own accounts', 'own data only', 'their own records'): emit a "
    "'mandatory_filter' scoping the owning column to the caller — e.g. "
    "{conditions:[{field:'user_id', operator:'==', value:'caller.user_id'}], "
    "effect:{decision:'allow'}, applies_to_intents:['view_accounts']}. Use the actual "
    "owner/identity column from the REFERENCE VOCABULARY when one exists.\n"
    "- Return NO rules for: headings, purpose, scope, definitions, revision history, "
    "related-documents lists, or audit/logging/retention text. (A plain capability "
    "list IS extractable when it implies an access boundary per the rules above.)\n"
    "- Do not summarize. If a rule is ambiguous, still emit it and list the ambiguity. "
    "If the POLICY TEXT states no enforceable control, return an empty list.\n"
    "- operator in: ==, !=, >, <, >=, <=, in, not_in. "
    "rule_type in: approval_threshold, data_access, regional_access, restriction, "
    "exception, audit_requirement, mandatory_filter. "
    "effect.decision in: allow, approval_required, block, mask, escalate.\n"
    "- Return ONLY a JSON object. No prose, no markdown fences."
)

USER_TEMPLATE = """POLICY TEXT (the only source of rules):
\"\"\"
{clause_text}
\"\"\"

REFERENCE VOCABULARY (context only — NEVER the subject or source of a rule):
- policy domain: {domain}
- approver/caller roles that may appear: {known_roles}
- data fields the gateway can evaluate: {known_fields}
- intents this may apply to: {known_intents}

Required JSON shape:
{{
  "candidate_rules": [
    {{
      "rule_key": "string_snake_case",
      "rule_type": "approval_threshold | data_access | regional_access | restriction | exception | audit_requirement | mandatory_filter",
      "conditions": [{{"field": "a concrete data field", "operator": "one of the allowed operators", "value": "literal/number/list copied from the text"}}],
      "effect": {{"decision": "allow | approval_required | block | mask | escalate", "approver_role": "optional", "restricted_fields": ["optional"], "message": "human-readable"}},
      "applies_to_intents": [],
      "requires_trace": true,
      "confidence": 0.0,
      "ambiguities": [],
      "source_evidence": "short exact phrase copied from the POLICY TEXT"
    }}
  ]
}}"""

# Fields that signal the model mined the reference vocabulary / boilerplate
# instead of the policy text. Rules conditioning on these are dropped.
_BLOCKED_FIELDS = {
    "domain",
    "known_domain",
    "known_roles",
    "known_fields",
    "known_intents",
    "known_region",
    "known_regions",
    "review_frequency",
    "log_retention_period",
}


@dataclass
class ExtractionContext:
    """Domain knowledge passed to the extractor to ground the model."""

    domain: str = "general"
    known_roles: list[str] = field(default_factory=list)
    known_fields: list[str] = field(default_factory=list)
    known_intents: list[str] = field(default_factory=list)


@dataclass
class ClauseExtraction:
    """Result of extracting one clause."""

    clause: Clause
    candidates: list[CandidateRule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False


class RuleExtractor:
    """Calls the LLM per clause and validates the candidate rules it returns."""

    def __init__(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_workers: int = 8,
        client=None,
    ) -> None:
        # Resolve the provider preset (arg > env > default), then let explicit
        # model/base_url/api_key (or SKILLBUILDER_* env) override any field.
        raw = (
            provider or os.environ.get("SKILLBUILDER_PROVIDER", DEFAULT_PROVIDER)
        ).lower()
        self.provider = PROVIDER_ALIASES.get(raw, raw)
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"unknown provider {raw!r}; choose from "
                f"{list(PROVIDERS) + list(PROVIDER_ALIASES)}"
            )
        preset = PROVIDERS[self.provider]

        self.model = model or os.environ.get("SKILLBUILDER_MODEL") or preset["model"]
        self.temperature = temperature
        self.max_workers = max_workers
        self._client = client  # injectable for tests / offline runs
        self._base_url = (
            base_url or os.environ.get("SKILLBUILDER_BASE_URL") or preset["base_url"]
        )
        # Prefer the provider's own key env(s) so base_url and key never mismatch;
        # fall back to a generic OPENAI_API_KEY.
        self._key_env = preset["key_env"]
        self._key_envs = [preset["key_env"], *preset.get("key_env_alts", [])]
        self._api_key = api_key or next(
            (
                os.environ[e]
                for e in (*self._key_envs, "OPENAI_API_KEY")
                if os.environ.get(e)
            ),
            None,
        )
        log.debug(
            "RuleExtractor provider=%s model=%s base_url=%s api_key=%s "
            "json_mode=%s max_workers=%d",
            self.provider, self.model, self._base_url,
            "set" if (self._api_key or client) else "MISSING",
            self.supports_json_mode, self.max_workers,
        )

    @property
    def supports_json_mode(self) -> bool:
        """deepseek-reasoner (R1) rejects response_format/temperature; chat is fine."""
        return "reasoner" not in self.model.lower()

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            if not self._api_key:
                raise RuntimeError(
                    f"No API key found for provider '{self.provider}'. "
                    f"Set {' or '.join(self._key_envs)} (or pass api_key=)."
                )
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    # -- public API -----------------------------------------------------------

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        """Generic structured-JSON call reused by the profiler/classifier/atoms
        passes — same provider/JSON-mode/lenient-parse plumbing as rule
        extraction. Returns the parsed object, or None on unparseable output."""
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.supports_json_mode:
            kwargs["temperature"] = self.temperature
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(**kwargs)
        return _loads_lenient(resp.choices[0].message.content or "")

    def extract_clause(
        self, clause: Clause, ctx: ExtractionContext
    ) -> ClauseExtraction:
        """Extract candidate rules from a single clause."""
        if clause.clause_type in _SKIP_TYPES:
            log.debug("clause %s skipped (type=%s)", clause.clause_id, clause.clause_type)
            return ClauseExtraction(clause=clause, skipped=True)

        t0 = time.perf_counter()
        try:
            raw = self._complete(clause, ctx)
        except Exception as e:  # network / API error — surface, do not crash run
            log.warning("clause %s llm_error: %s", clause.clause_id, e)
            return ClauseExtraction(clause=clause, errors=[f"llm_error: {e}"])

        result = self._parse(clause, raw)
        log.debug(
            "clause %s -> %d candidate(s), %d error(s) in %.2fs",
            clause.clause_id, len(result.candidates), len(result.errors),
            time.perf_counter() - t0,
        )
        for c in result.candidates:
            log.debug("    rule %s -> %s", c.rule_key, c.effect.decision)
        return result

    def extract_clauses(
        self, clauses: Iterable[Clause], ctx: ExtractionContext
    ) -> list[ClauseExtraction]:
        """Extract all clauses concurrently (I/O-bound), preserving input order.

        ``skipped`` clauses never hit the network. ``max_workers <= 1`` runs
        sequentially (useful for deterministic debugging).
        """
        clauses = list(clauses)
        if self.max_workers <= 1 or len(clauses) <= 1:
            return [self.extract_clause(c, ctx) for c in clauses]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(lambda c: self.extract_clause(c, ctx), clauses))

    # -- internals ------------------------------------------------------------

    def _complete(self, clause: Clause, ctx: ExtractionContext) -> str:
        user = USER_TEMPLATE.format(
            clause_text=clause.source_text,
            domain=ctx.domain,
            known_roles=", ".join(ctx.known_roles) or "(none provided)",
            known_fields=", ".join(ctx.known_fields) or "(none provided)",
            known_intents=", ".join(ctx.known_intents) or "(none provided)",
        )
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        # Reasoner/R1-style models reject these; chat/instruct models want them.
        if self.supports_json_mode:
            kwargs["temperature"] = self.temperature
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _parse(self, clause: Clause, raw: str) -> ClauseExtraction:
        result = ClauseExtraction(clause=clause)
        payload = _loads_lenient(raw)
        if payload is None:
            result.errors.append("invalid_json: model did not return parseable JSON")
            return result

        for i, item in enumerate(payload.get("candidate_rules", []) or []):
            try:
                rule = CandidateRule.model_validate(item)
            except ValidationError as e:
                result.errors.append(f"schema_invalid[{i}]: {_short_err(e)}")
                continue
            # Defense-in-depth: drop rules that mined the reference vocabulary
            # rather than the policy text (see _BLOCKED_FIELDS).
            blocked = next(
                (
                    c.field
                    for c in rule.conditions
                    if c.field.strip().lower() in _BLOCKED_FIELDS
                ),
                None,
            )
            if blocked is not None:
                result.errors.append(f"dropped_meta_field[{i}]: field={blocked!r}")
                continue
            rule.source_clause_id = clause.clause_id
            if not rule.source_evidence:
                rule.source_evidence = clause.source_text
            result.candidates.append(rule)
        return result


def _loads_lenient(raw: str) -> Optional[dict]:
    """Parse JSON, tolerating accidental markdown fences or surrounding prose."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Strip ```json fences.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _short_err(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:3]:
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
