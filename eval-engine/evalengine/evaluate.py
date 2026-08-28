"""Top-level orchestration: one session_id -> a fully version-stamped Finding
list. The one place reconstruct + provenance + family2 + combinator + store
compose - worker.py and api.py's /eval/run both call this, so there is
exactly one evaluation path (Hard Rule 12 needs that: same inputs, same code
path, same output).
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config, store
from .binding import BindingProfile
from .combinator import combine_oob
from .contract import CheckContext, Finding, VersionStamp
from .family1 import evaluate_all as evaluate_family1
from .family1.compilepack import RulePack
from .family2 import evaluate_all as evaluate_family2
from .provenance import build as build_provenance
from .reconstruct import reconstruct
from .visibility import VisibilityProfile


def version_key(binding: BindingProfile, visibility: VisibilityProfile, rule_pack: RulePack) -> str:
    return (f"{config.ENGINE_VERSION}:{binding.version}:{visibility.version}:"
           f"{rule_pack.source_skill}@{rule_pack.source_skill_version}")


def evaluate_session(session_id: str, binding: BindingProfile, visibility: VisibilityProfile,
                     rule_pack: RulePack) -> list[Finding]:
    spans = store.session_spans(session_id)
    if not spans:
        return []
    session = reconstruct(session_id, spans, binding)
    prov = build_provenance(session, config.PARAM_ROUND_ABS_TOLERANCE, config.PARAM_ROUND_REL_TOLERANCE)
    ctx = CheckContext(
        binding_version=binding.version,
        visibility_profile=visibility,
        provenance=prov,
        config={
            "round_abs_tolerance": config.PARAM_ROUND_ABS_TOLERANCE,
            "round_rel_tolerance": config.PARAM_ROUND_REL_TOLERANCE,
            "minimization_row_floor": config.MINIMIZATION_ROW_FLOOR,
            "minimization_multiple": config.MINIMIZATION_MULTIPLE,
        },
    )
    verdicts = evaluate_family2(session, ctx)
    verdicts.extend(evaluate_family1(session, rule_pack, ctx))
    versions = VersionStamp(
        engine_version=config.ENGINE_VERSION,
        binding_profile_version=binding.version,
        visibility_profile_version=visibility.version,
        rule_pack_version=f"{rule_pack.source_skill}@{rule_pack.source_skill_version}" if rule_pack.rules else "",
    )
    evaluated_at = datetime.now(timezone.utc).isoformat()
    return combine_oob(verdicts, visibility, versions, evaluated_at)


def evaluate_and_persist(session_id: str, binding: BindingProfile, visibility: VisibilityProfile,
                         rule_pack: RulePack, force: bool = False) -> dict:
    vkey = version_key(binding, visibility, rule_pack)
    if not force and store.is_evaluated(session_id, vkey):
        return {"session_id": session_id, "skipped": True, "reason": "already evaluated at this version"}
    findings = evaluate_session(session_id, binding, visibility, rule_pack)
    counts = store.persist(findings)
    store.mark_evaluated(session_id, vkey)
    return {"session_id": session_id, "skipped": False, "version_key": vkey, **counts}
