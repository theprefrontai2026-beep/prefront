"""Inline reuse of eval-engine's SAFE, single-call-evaluable checks
(autonomous_build.md step 18). See ../../../eval-engine/CLAUDE.md's
"Phase D / step 18" section for the full investigation and why most of
Family 1/2 do NOT belong here.

Wired in: family3.call (catalog_membership, entitlement, version_conformance,
side_effect_class - args + caller only, no session history needed) runs
PRE-execution and can gate the call; family1.content (field_restriction -
the executed result) runs POST-execution and can only mask, since the read
already happened by the time its result exists. Never Family 2 (needs
cross-step provenance this stateless per-call gateway cannot supply) and
never family1.predicate/temporal here (would run in parallel with, not
replace, the native governance/rules.py pipeline skill-builder's published
policy.yaml already drives - out of scope for this pass).

`combine_inline`'s `flag` effect never changes anything here: this native
engine's decide.aggregate() has no flag concept (block > approval_required >
allowed only) - a flag is reported in the returned verdicts (so the caller
can still annotate the span with it) but never gates or masks a call.

Both artifact paths default to unconfigured (empty pack/catalog -> every
call is a no-op here, same posture as eval-engine's own Hard Rule 9).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..evalengine.combinator import combine_inline
from ..evalengine.contract import CheckContext, Session, Step, Verdict
from ..evalengine.family1 import content as content_checks
from ..evalengine.family1.compilepack import RulePack
from ..evalengine.family1.compilepack import load as _load_rule_pack
from ..evalengine.family3 import call as call_checks
from ..evalengine.family3.catalog import IntentCatalog
from ..evalengine.family3.catalog import load as _load_intent_catalog

RULE_PACK_PATH = os.environ.get("PREFRONT_RULE_PACK_PATH", "")
INTENT_CATALOG_PATH = os.environ.get("PREFRONT_INTENT_CATALOG_PATH", "")

_rule_pack: Optional[RulePack] = None
_catalog: Optional[IntentCatalog] = None


def rule_pack() -> RulePack:
    global _rule_pack
    if _rule_pack is None:
        _rule_pack = _load_rule_pack(RULE_PACK_PATH)
    return _rule_pack


def intent_catalog() -> IntentCatalog:
    global _catalog
    if _catalog is None:
        _catalog = _load_intent_catalog(INTENT_CATALOG_PATH)
    return _catalog


def reload() -> None:
    """Test/ops hook: force the next call to re-read both artifacts."""
    global _rule_pack, _catalog
    _rule_pack = None
    _catalog = None


def _step(intent: str, tool_name: str, args: dict[str, Any], result: Any, side_effect: str) -> Step:
    return Step(
        span_id="inline", trace_id="inline", seq=0, start_time="", end_time="",
        tool_name=tool_name, intent=intent, args=dict(args or {}), result=result,
        status="OK", side_effect=side_effect,
    )


def _session(step: Step, caller_role: str, channel: str) -> Session:
    return Session(
        session_id="inline", trace_ids=(), user_id="", caller_role=caller_role or "",
        channel=channel or "", turns=(), steps=(step,), final_answer="",
    )


_CTX = CheckContext(binding_version="inline", visibility_profile=None, provenance=None, config={})


def evaluate_pre_execution(
    intent: str, tool_name: str, args: dict[str, Any], caller_role: str, channel: str, side_effect: str,
) -> tuple[str, list[Verdict]]:
    """catalog_membership / entitlement / version_conformance / side_effect_class -
    everything decidable BEFORE the call executes. Returns (effect, verdicts);
    effect is block|approval_required|allow (a `flag` from combine_inline maps
    to "allow" here - see module docstring)."""
    catalog = intent_catalog()
    if not catalog.intents:
        return "allow", []
    session = _session(_step(intent, tool_name, args, None, side_effect), caller_role, channel)
    verdicts = call_checks.evaluate(session, catalog, _CTX)
    effect, _ = combine_inline(verdicts)
    return ("allow" if effect == "flag" else effect), verdicts


def evaluate_post_execution(
    intent: str, tool_name: str, args: dict[str, Any], result: Any, caller_role: str, channel: str,
) -> tuple[str, list[Verdict]]:
    """field_restriction (family1/content.py) over the executed result. The
    read already happened - this can only ever inform masking, never a
    block; effect is returned for completeness/annotation but the caller
    should treat "block" here as "mask the fields the violated verdicts
    name" (see restricted_field_names below), not as a call it can
    retroactively refuse."""
    pack = rule_pack()
    if not pack.rules:
        return "allow", []
    session = _session(_step(intent, tool_name, args, result, ""), caller_role, channel)
    verdicts = content_checks.evaluate(session, pack, _CTX)
    effect, _ = combine_inline(verdicts)
    return ("allow" if effect == "flag" else effect), verdicts


def restricted_field_names(result: Any) -> set[str]:
    """The union of every content-engine detector's field_names actually
    present in `result` (by key name, same matching content.py's own
    _field_in_result uses) - what the caller should mask before returning
    this result, regardless of whether the native policy.yaml rules already
    caught it (idempotent either way)."""
    from ..evalengine.provenance import flatten

    def normalize(name: str) -> str:
        import re
        return re.sub(r"[\s_-]+", "", name.strip().lower())

    present = {normalize(p.rsplit(".", 1)[-1].split("[")[0]) for p, _ in flatten(result)}
    hits: set[str] = set()
    for rule in rule_pack().by_engine("content"):
        for det in rule.detectors:
            for fname in det.get("field_names") or []:
                if normalize(fname) in present:
                    hits.add(fname)
    return hits
