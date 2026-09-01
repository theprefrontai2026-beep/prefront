"""Top-level orchestration: one session_id -> a fully version-stamped Finding
list. The one place reconstruct + provenance + family2 + combinator + store
compose - worker.py and api.py's /eval/run both call this, so there is
exactly one evaluation path (Hard Rule 12 needs that: same inputs, same code
path, same output).
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import checks as checks_mod
from . import config, store
from .binding import BindingProfile
from .checks import CheckSettings
from .combinator import combine_oob
from .contract import CheckContext, Finding, VersionStamp
from .family1 import evaluate_all as evaluate_family1
from .family1.compilepack import RulePack
from .family2 import evaluate_all as evaluate_family2
from .family3 import evaluate_all as evaluate_family3
from .family3 import population as population_checks
from .family3.catalog import IntentCatalog
from .provenance import build as build_provenance
from .reconstruct import reconstruct
from .visibility import VisibilityProfile


def version_key(binding: BindingProfile, visibility: VisibilityProfile, rule_pack: RulePack,
                catalog: IntentCatalog, settings: CheckSettings | None = None) -> str:
    """The identity of an evaluation PASS - what `store.is_evaluated` compares.

    `checks@...` is here because turning a check off (or back on) changes
    which verdicts a session produces, so it has to invalidate that session's
    prior evaluation exactly the way a new rule pack does. Without it,
    re-enabling a check would leave every session evaluated while it was off
    permanently missing that check's verdicts.

    It reads `checks@all` on a deployment with everything on - the state every
    deployment starts in - but the segment is emitted unconditionally rather
    than only when something is disabled, so "never turned anything off" and
    "turned it off and back on" stay distinguishable. The cost is that this
    string changes for every deployment on upgrade, re-evaluating each session
    once; that is the same one-off an ENGINE_VERSION bump already carries.

    Deliberately not part of `VersionStamp`: that records what produced a
    verdict's CONTENT (Hard Rule 11), and enablement only decides whether one
    is emitted. See evalengine/checks.py."""
    s = settings if settings is not None else checks_mod.current()
    return (f"{config.ENGINE_VERSION}:{binding.version}:{visibility.version}:"
           f"{rule_pack.source_skill}@{rule_pack.source_skill_version}:catalog@{catalog.version}:"
           f"checks@{s.version}")


def evaluate_session(session_id: str, binding: BindingProfile, visibility: VisibilityProfile,
                     rule_pack: RulePack, catalog: IntentCatalog, spans: list,
                     settings: CheckSettings | None = None) -> list[Finding]:
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
    verdicts.extend(evaluate_family3(session, catalog, ctx))
    # Disabled checks are dropped HERE, after every family has run and before
    # anything is stamped or stored - one gate for all three, rather than
    # three per-family ones (the families expose check ids at three different
    # granularities; see CheckSettings.keep). Checks themselves never learn
    # about settings, so they stay pure (Hard Rule 3).
    verdicts = (settings if settings is not None else checks_mod.current()).keep(verdicts)
    versions = VersionStamp(
        engine_version=config.ENGINE_VERSION,
        binding_profile_version=binding.version,
        visibility_profile_version=visibility.version,
        rule_pack_version=f"{rule_pack.source_skill}@{rule_pack.source_skill_version}" if rule_pack.rules else "",
        catalog_version=catalog.version if catalog.intents else "",
    )
    evaluated_at = datetime.now(timezone.utc).isoformat()
    return combine_oob(verdicts, visibility, versions, evaluated_at)


def evaluate_and_persist(session_id: str, binding: BindingProfile, visibility: VisibilityProfile,
                         rule_pack: RulePack, catalog: IntentCatalog, force: bool = False,
                         settings: CheckSettings | None = None) -> dict:
    settings = settings if settings is not None else checks_mod.current()
    vkey = version_key(binding, visibility, rule_pack, catalog, settings)
    if not force and store.is_evaluated(session_id, vkey):
        return {"session_id": session_id, "skipped": True, "reason": "already evaluated at this version"}
    spans = store.session_spans(session_id)
    if not spans:
        # Not a genuine "evaluated to zero findings" result - the session
        # isn't ingested yet (or lost a race with ingestion). Do NOT
        # mark_evaluated: that would permanently skip a session that was
        # simply not ready yet, since nothing else ever retries it.
        return {"session_id": session_id, "skipped": True, "reason": "no spans ingested yet"}
    findings = evaluate_session(session_id, binding, visibility, rule_pack, catalog, spans, settings)
    counts = store.persist(findings)
    store.mark_evaluated(session_id, vkey)
    return {"session_id": session_id, "skipped": False, "version_key": vkey, **counts}


def evaluate_population(
    scenario_id: str = "", variant: str = "",
    baseline_variant: str = "", compare_variant: str = "",
    rule_id: str = "", visibility: VisibilityProfile = None,
    settings: CheckSettings | None = None,
) -> dict:
    """Population checks (autonomous_build.md step 17): on-demand aggregate
    computation, not tied to any single session's evaluation. Persists
    through the same store.persist path as per-session findings."""
    verdicts = []
    if scenario_id:
        rows = store.session_shapes(scenario_id)
        v = population_checks.outcome_consistency(scenario_id, rows, variant)
        if v is not None:
            verdicts.append(v)
        if baseline_variant and compare_variant:
            v = population_checks.invocation_drift(scenario_id, rows, baseline_variant, compare_variant)
            if v is not None:
                verdicts.append(v)
    if rule_id:
        history = store.verdict_history(rule_id=rule_id)
        v = population_checks.verdict_trend(rule_id, history)
        if v is not None:
            verdicts.append(v)

    verdicts = (settings if settings is not None else checks_mod.current()).keep(verdicts)
    versions = VersionStamp(engine_version=config.ENGINE_VERSION)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    findings = combine_oob(verdicts, visibility or VisibilityProfile(version="", captures={}), versions, evaluated_at)
    counts = store.persist(findings)
    return {"scenario_id": scenario_id, "rule_id": rule_id, "verdicts": len(findings), **counts}
