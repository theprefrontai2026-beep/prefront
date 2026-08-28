"""Inline reuse of eval-engine's SAFE, single-call-evaluable checks
(autonomous_build.md step 18). See ../../../eval-engine/CLAUDE.md's
"Phase D / step 18" section for the full investigation and why
family1.predicate/temporal still do NOT belong here.

Wired in: family3.call (catalog_membership, entitlement, version_conformance,
side_effect_class - args + caller only, no session history needed) runs
PRE-execution and can gate the call; family1.content (field_restriction -
the executed result) runs POST-execution and can only mask, since the read
already happened by the time its result exists; five of Family 2's six
parameter-side checks (param_mutation, param_discard, param_taint,
param_staleness, entity_consistency - see evaluate_family2_parameter_side
below) run PRE-execution using governance/session_state.py's per-connection
history, now that that history exists (added past the first pass of this
module - see the CLAUDE.md section).

param_provenance is DELIBERATELY EXCLUDED from that five, and this is a
different, harder finding than the "categorical decision" gap documented
earlier: it isn't an occasional false positive, it is unconditional. This
gateway has no "turn"/user-message concept at all - only tool-call args and
results ever exist here - and param_provenance's applicability test treats
"no candidate origin found at all" (match == "none") as status=violated,
effect=block, not as inapplicable. eval-engine's own OOB path reconstructs
real turns from the LLM conversation, so a first call's args usually DO
trace to the user's message there; inline, that corpus structurally does
not exist, so EVERY first call's bare, user-supplied argument (e.g. an id
typed into a form) would be flagged as fabricated and BLOCKED, forever -
verified live via test_inline_checks_wiring.py's pre-existing
test_entitled_caller_executes, which failed exactly this way the moment
param_provenance was wired in. The other five checks were individually
verified NOT to share this failure mode: param_mutation and param_taint
both skip (never flag) a value with no candidate at all (`if
origin.candidate is None: continue`), param_staleness only ever compares
tool-result-sourced candidates (never user-message ones, so a turn-less
context just yields fewer candidates, not wrong ones), and
param_discard/entity_consistency never touch ctx.provenance - they compare
raw args across steps directly. Family 2's result-side checks
(result_fidelity, error_blindness, approval_evidence, minimization) and
family1.predicate/temporal remain out of scope for a different reason: the
former need a final answer, which a single governed MCP tool call never
produces; the latter would run in parallel with, not replace, the native
governance/rules.py pipeline skill-builder's published policy.yaml already
drives - a bigger unification decision, not attempted here.

`combine_inline`'s `flag` effect never changes anything here: this native
engine's decide.aggregate() has no flag concept (block > approval_required >
allowed only) - a flag is reported in the returned verdicts (so the caller
can still annotate the span with it) but never gates or masks a call.

Both artifact paths default to unconfigured (empty pack/catalog -> every
call is a no-op here, same posture as eval-engine's own Hard Rule 9); Family 2
is built-in and always runs regardless of artifact configuration (also
Hard Rule 9).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..evalengine.combinator import combine_inline
from ..evalengine.contract import CheckContext, Session, Step, Verdict
from ..evalengine.family1 import content as content_checks
from ..evalengine.family1.compilepack import RulePack
from ..evalengine.family1.compilepack import load as _load_rule_pack
from ..evalengine.family2 import entity_consistency as f2_entity_consistency
from ..evalengine.family2 import param_discard as f2_param_discard
from ..evalengine.family2 import param_mutation as f2_param_mutation
from ..evalengine.family2 import param_staleness as f2_param_staleness
from ..evalengine.family2 import param_taint as f2_param_taint
from ..evalengine.family3 import call as call_checks
from ..evalengine.family3.catalog import IntentCatalog
from ..evalengine.family3.catalog import load as _load_intent_catalog
from ..evalengine.provenance import build as _build_provenance

# param_provenance excluded - see module docstring: unconditional false
# positives with no turn/user-message corpus, not merely occasional ones.
_FAMILY2_PARAM_CHECKS = (
    f2_param_mutation, f2_param_discard,
    f2_param_taint, f2_param_staleness, f2_entity_consistency,
)

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


def _step(
    intent: str, tool_name: str, args: dict[str, Any], result: Any, side_effect: str, seq: int = 0,
) -> Step:
    return Step(
        span_id=f"inline-{seq}", trace_id="inline", seq=seq, start_time="", end_time="",
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


def evaluate_family2_parameter_side(
    intent: str, tool_name: str, args: dict[str, Any], side_effect: str,
    prior_steps: tuple[Step, ...], caller_role: str, channel: str,
) -> tuple[str, list[Verdict], Step]:
    """param_mutation / param_discard / param_taint / param_staleness /
    entity_consistency - five of Family 2's six built-in, always-on
    parameter-side checks (param_provenance excluded - see module
    docstring), now that session_state.py supplies the missing prerequisite
    (cross-call history for this MCP connection). Runs
    PRE-execution: every one of these six checks reasons about this call's
    ARGS against prior steps' already-known results, never against this
    call's own not-yet-known result - param_staleness's write-args signal is
    the same (see its docstring: it reads a write step's args, not a later
    re-read). `prior_steps` must be the FULLY completed steps already
    accumulated for this connection (real results, not placeholders).

    Returns (effect, verdicts-about-this-call-only, the built current Step).
    `effect` folds like evaluate_pre_execution's (flag -> allow). The caller
    must append the returned Step to session_state once the real result is
    known, so later calls see this one's actual result rather than a None
    placeholder - this function does not mutate session_state itself
    (Hard Rule 3: checks stay pure; state mutation is the caller's job).
    """
    seq = len(prior_steps)
    current = _step(intent, tool_name, args, None, side_effect, seq=seq)
    session = Session(
        session_id="inline", trace_ids=(), user_id="",
        caller_role=caller_role or "", channel=channel or "",
        turns=(), steps=(*prior_steps, current), final_answer="",
    )
    ctx = CheckContext(
        binding_version="inline", visibility_profile=None,
        provenance=_build_provenance(session, abs_tol=0.01, rel_tol=0.005), config={},
    )
    verdicts: list[Verdict] = []
    for mod in _FAMILY2_PARAM_CHECKS:
        verdicts.extend(mod.evaluate(session, ctx))
    own = [v for v in verdicts if current.span_id in v.evidence.span_ids]
    effect, _ = combine_inline(own)
    return ("allow" if effect == "flag" else effect), own, current


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
