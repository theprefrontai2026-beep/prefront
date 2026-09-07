"""FastAPI app: /eval/* - the query API the Findings UI (and any other
consumer) reads, plus the worker's control endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import behavior
from . import binding as binding_mod
from . import checks as checks_mod
from . import compliance as compliance_mod
from . import config, evaluate, store, visibility as visibility_mod
from .family1 import compilepack as rulepack_mod
from . import applications
from .family3 import catalog as catalog_mod
from .worker import Worker

logging.basicConfig(level=config.EVAL_LOG_LEVEL.upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("evalengine")

def _load_artifact(var: str, path: str, load):
    """Load one configured artifact, or fail with a message that names it.

    An EMPTY path means "this deployment ships without the artifact" and each
    loader returns its empty value, so the family degrades to zero verdicts
    (Hard Rule 9). A path that is SET but does not resolve is a
    MISCONFIGURATION, and these run at module scope - so an unhandled
    FileNotFoundError here means uvicorn never starts and the whole service is
    down with a bare traceback naming only a path.

    That is not hypothetical. A deployment's seed step guarded the copy of
    several artifacts on the presence of just ONE of them, so on a volume
    created before a later artifact joined that list it never landed, and this
    service died at import. The seed was fixed; this makes the failure legible
    either way. Same rule as the inline gateway's own preload(), for the same
    reason: an artifact that is configured and absent must say so, loudly.
    """
    try:
        return load(path)
    except Exception as e:
        if not path:
            raise
        raise RuntimeError(
            f"{var}={path!r} could not be loaded: {type(e).__name__}: {e}. "
            f"Unset it to run without this artifact; a set-but-unreadable path "
            f"is never treated as unconfigured."
        ) from e


_binding = _load_artifact("EVAL_TRACE_BINDING_PATH", config.TRACE_BINDING_PATH, binding_mod.load)
_visibility = _load_artifact("EVAL_VISIBILITY_PROFILE_PATH", config.VISIBILITY_PROFILE_PATH, visibility_mod.load)
_rule_pack = _load_artifact("EVAL_RULE_PACK_PATH", config.RULE_PACK_PATH, rulepack_mod.load)
_catalog = _load_artifact("EVAL_INTENT_CATALOG_PATH", config.INTENT_CATALOG_PATH, catalog_mod.load)
_packs = compliance_mod.load_packs(config.FRAMEWORK_PACKS_DIR)
_overlay = _load_artifact("EVAL_COMPLIANCE_OVERLAY_PATH", config.COMPLIANCE_OVERLAY_PATH, compliance_mod.load_overlay)
# Per-application configuration (Phase 3). The deployment-wide artifacts above
# become the DEFAULTS every application inherits when the registry is
# unconfigured or does not mention it — so an unset EVAL_APPLICATIONS_PATH is
# byte-identical to the single-tenant behaviour that predates this.
_default_app = applications.AppConfig(
    app_id="", binding=_binding, visibility=_visibility,
    rule_pack=_rule_pack, catalog=_catalog, overlay=_overlay,
)
_apps = _load_artifact("EVAL_APPLICATIONS_PATH", config.APPLICATIONS_PATH,
                       lambda p: applications.Registry(_default_app, p))
worker = Worker(_binding, _visibility, _rule_pack, _catalog, _apps)
_started_at = datetime.now(timezone.utc)


async def _wait_for_clickhouse() -> None:
    delay = 1.0
    for attempt in range(60):
        try:
            store.ensure_schema()
            log.info("clickhouse ready (%s, db=%s)", config.CLICKHOUSE_URL, config.CLICKHOUSE_DB)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("clickhouse not ready (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)
    log.error("clickhouse never became ready; continuing, requests will fail until it does")


def _configured_families(app: str = "") -> dict[str, bool]:
    """Which families have the artifact they need, FOR ONE APPLICATION.

    Family 2 is built in and always runs (Hard Rule 9); Family 1 needs a rule
    pack and Family 3 an intent catalog, so a settings UI can tell "you turned
    this off" apart from "nothing is configured, so it is idle anyway".

    Per application, because artifacts are per application now: an app with no
    rule pack of its own must not be told Family 1 is configured because some
    OTHER application has one.
    """
    cfg = _apps.for_app(app)
    return {
        "family1": bool(cfg.rule_pack.rules),
        "family2": True,
        "family3": bool(cfg.catalog.intents),
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _wait_for_clickhouse()
    # Restore the deployment's disabled-check set before the worker's first
    # poll, so a restart never re-evaluates one round with everything on.
    # A read failure is not fatal: everything-enabled is the safe default,
    # and /eval/checks will surface the error the moment it is opened.
    try:
        checks_mod.set_current(await asyncio.to_thread(store.load_check_settings))
    except Exception as e:  # noqa: BLE001
        log.warning("could not load check settings, defaulting to all enabled: %s", e)
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Prefront evaluation engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# Scope reads to one subject application. See application_isolation_design.md:
# an application is the unit of isolation, and until this existed every /eval/
# read was deployment-wide — so a page labelled with one app counted every
# other app's sessions under it.
AppQ = Query(default="", description="Restrict to one subject application (its Phoenix project, or the id EVAL_PROJECT_APP_MAP translates it to). Empty = every application, which is the historical behaviour and correct for a single-app deployment.")


@app.get("/eval/health")
async def health():
    ok = await asyncio.to_thread(store.ch.ping)
    return {"ok": ok, "clickhouse": ok}


def _profile_json(p) -> dict:
    """A ToolProfile as JSON, counts intact.

    Every distinct value keeps its support rather than being flattened to a
    list: "which roles called this" is not the reviewer's question, "which
    roles, how often, and is the tail an accident" is."""
    c = lambda xs: [{"value": x.value, "sessions": x.sessions, "calls": x.calls} for x in xs]
    return {
        "tool_name": p.tool_name, "calls": p.calls, "sessions": p.sessions,
        "first_seen": p.first_seen, "last_seen": p.last_seen, "error_calls": p.error_calls,
        "side_effects": c(p.side_effects), "roles": c(p.roles), "channels": c(p.channels),
        "params": c(p.params), "fields": c(p.fields),
        "row_count_p50": p.row_count_p50, "row_count_p99": p.row_count_p99,
        "row_count_max": p.row_count_max,
        "caller_invariants": list(p.caller_invariants), "followed_by": list(p.followed_by),
        "contested": list(p.contested), "is_contested": p.is_contested,
        "example_sessions": list(p.example_sessions),
    }


@app.get("/eval/status")
async def status(since: int = 0, app: str = AppQ):
    ok = await asyncio.to_thread(store.ch.ping)
    totals = await asyncio.to_thread(store.totals, since, app) if ok else {}
    _cfg = _apps.for_app(app)
    return {
        "clickhouse": {"ok": ok, "url": config.CLICKHOUSE_URL, "database": config.CLICKHOUSE_DB, **totals},
        "worker": worker.status(),
        # Profiles are reported for the REQUESTED application, not the
        # deployment. Without this an application with no artifacts of its own
        # still showed another application's rule count as if it were its own —
        # the same mislabelling the data-side scoping fixed, one level up.
        "application": {"id": app, "registered": _apps.registered(app) if app else None,
                        "registry_configured": _apps.configured},
        "profiles": {
            "trace_binding": {"path": config.TRACE_BINDING_PATH or "(bundled default)", "version": _cfg.binding.version},
            "visibility": {"path": config.VISIBILITY_PROFILE_PATH or "(bundled default)", "version": _cfg.visibility.version},
            "rule_pack": {"path": config.RULE_PACK_PATH or "(not configured)", "rule_count": len(_cfg.rule_pack.rules)},
            "intent_catalog": {"path": config.INTENT_CATALOG_PATH or "(not configured)",
                              "intent_count": len(_cfg.catalog.intents)},
            "compliance_overlay": {"path": config.COMPLIANCE_OVERLAY_PATH or "(not configured)",
                                   "configured": _overlay.configured, "deployment": _overlay.deployment,
                                   "frameworks": list(_overlay.frameworks),
                                   "data_classes": {k: len(v) for k, v in _overlay.data_classes.items()}},
            "framework_packs": sorted(_packs),
            "checks": {"total": len(checks_mod.REGISTRY),
                       "disabled": sorted(checks_mod.current().disabled),
                       "version": checks_mod.current().version},
        },
        "retention_days": config.RETENTION_DAYS,
        "engine_version": config.ENGINE_VERSION,
        "mode": config.EVAL_MODE,
        "started_at": _started_at.isoformat(),
    }


# ── Behavioural mining (intent_learning_design.md L1) ─────────────────────
# Read-only aggregates over observed traces, for a deployment with no policy
# document. Counting only: nothing here names or infers an intent — that is
# semantic-layer's design-time, LLM-assisted step, which consumes these.


@app.get("/eval/behavior/tools")
async def behavior_tools(since: int = 7 * 86400, app: str = AppQ, min_sessions: int = 1):
    """Per-tool profile: callers, channels, params, returned fields, side
    effect, row-count distribution, caller invariants, what followed — each
    with support counts, plus the Family 2 `contested` overlay.

    `contested` is the load-bearing field. Frequency is not legitimacy: an
    agent that has been leaking for months makes leaking look normal, so a
    profile is only credible alongside the integrity violations on the very
    sessions that support it. Family 2 needs no policy, which is what makes it
    usable as ground truth on a deployment that has none."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "tools": [], "error": "clickhouse unreachable"}
    profiles = await asyncio.to_thread(behavior.tool_profiles, since, app, min_sessions)
    return {"configured": True, "since": since, "app": app,
            "tools": [_profile_json(p) for p in profiles]}


@app.get("/eval/behavior/workflows")
async def behavior_workflows(since: int = 7 * 86400, app: str = AppQ,
                             min_sessions: int = 3, max_len: int = 6):
    """Frequent contiguous tool RUNS — intents that span several calls.

    A business operation is often a sequence, and per-tool profiles report that
    as unrelated operations. Consecutive repeats are collapsed (a retry is not
    a step), support is counted over sessions, and a run that is merely a
    fragment of a longer run with the same support is dropped — without that
    last filter the output is every prefix of every pattern and a reviewer
    cannot tell which rows are the same finding."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "workflows": []}
    ws = await asyncio.to_thread(behavior.frequent_workflows, since, app, min_sessions, 2, max_len)
    return {"configured": True, "since": since, "app": app, "workflows": [
        {"steps": list(w.steps), "sessions": w.sessions, "occurrences": w.occurrences,
         "coverage": w.coverage, "roles": list(w.roles), "contested": list(w.contested),
         "example_sessions": list(w.example_sessions)}
        for w in ws]}


@app.get("/eval/behavior/intents")
async def behavior_intents(since: int = 7 * 86400, app: str = AppQ,
                           min_sessions: int = 3, max_len: int = 6,
                           min_overlap: float = 0.6):
    """Mined runs GROUPED into candidate intents.

    A business intent rarely has one shape — "assess an applicant" appears as
    find->profile, profile->report, and the full four-step run, depending on
    what the agent already had. Reported separately those are several
    candidates with near-identical policies, and a reviewer reads the same
    operation repeatedly without seeing that it is one. Grouping is
    deterministic (step overlap); what a group MEANS is the synthesis step's
    job, and it gets the whole group at once.

    `core_steps` (in every variant) and `optional_steps` are COUNTED — that
    backbone is what a reviewer would turn into a precondition, so it must not
    depend on a model's reading."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "intents": []}
    gs = await asyncio.to_thread(behavior.group_workflows, since, app, min_sessions, max_len, min_overlap)
    return {"configured": True, "since": since, "app": app, "intents": [
        {"core_steps": list(g.core_steps), "optional_steps": list(g.optional_steps),
         "sessions": g.sessions, "roles": list(g.roles), "contested": list(g.contested),
         "example_sessions": list(g.example_sessions),
         "variants": [{"steps": list(v.steps), "sessions": v.sessions,
                       "coverage": v.coverage, "occurrences": v.occurrences}
                      for v in g.variants]}
        for g in gs]}


@app.get("/eval/behavior/cohorts")
async def behavior_cohorts(since: int = 7 * 86400, app: str = AppQ):
    """What differs BETWEEN groups of callers — the access-policy signal.

    A policy is what makes one cohort's behaviour differ from another's, so the
    differences are where it is visible and no single cohort's profile contains
    it. Returns, per cohort: the operations it performs, the ones only it
    performs, the ones others perform that it never does (with its own traffic
    volume, so a reader can weigh the claim), and — the strongest signal — the
    fields the same tool returned to others but never to it.

    Absence is reported with exposure rather than as a conclusion: a cohort
    with 400 sessions that never touched a tool is a boundary, one with 3 is
    silence, and nothing here distinguishes them without the volume."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "cohorts": []}
    cs = await asyncio.to_thread(behavior.cohort_contrasts, since, app)
    return {"configured": True, "since": since, "app": app, "cohorts": [
        {"role": c.role, "sessions": c.sessions, "calls": c.calls,
         "tools": list(c.tools), "exclusive_tools": list(c.exclusive_tools),
         "never_used": list(c.never_used), "field_gaps": list(c.field_gaps),
         "has_exposure": c.has_exposure}
        for c in cs]}


@app.get("/eval/behavior/sequences")
async def behavior_sequences(since: int = 7 * 86400, app: str = AppQ, min_support: int = 3):
    """Ordered tool pairs adjacent within a session — the closing-obligation
    hypothesis, as rates over sessions rather than calls."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "sequences": {}}
    return {"configured": True,
            "sequences": await asyncio.to_thread(behavior.following_tools, since, app, min_support)}


@app.get("/eval/behavior/invariants")
async def behavior_invariants(since: int = 7 * 86400, app: str = AppQ, min_calls: int = 3):
    """Arguments that always equalled a caller attribute — the mandatory-filter
    hypothesis, in the exact `<field> = caller` shape Family 3 enforces."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "invariants": {}}
    return {"configured": True,
            "invariants": await asyncio.to_thread(behavior.caller_invariants, since, app, min_calls)}


@app.get("/eval/behavior/labels")
async def behavior_labels(since: int = 7 * 86400, app: str = AppQ):
    """The `app.intent` a deployment already stamps, per tool.

    Exposed for SCORING a mined catalog against a hand-authored one, never as
    a mining input — see behavior/__init__.py. A deployment that stamps this
    already has the catalog mining is meant to produce."""
    ok = await asyncio.to_thread(store.ch.ping)
    if not ok:
        return {"configured": False, "labels": {}}
    return {"configured": True,
            "labels": await asyncio.to_thread(behavior.observed_intent_labels, since, app)}


@app.get("/eval/coverage")
async def coverage(since: int = 0, app: str = AppQ):
    """Rule-pack coverage: which Family-1 rules have ever produced a verdict vs.
    which have never had matching traffic ("never hit"). Authoritative — counts
    over all verdicts server-side, not the capped UI slice. Degrades to
    configured=false / zero rules when no rule pack is loaded (Hard Rule 9).
    Optionally windowed to the last `since` seconds (never-hit within window)."""
    ok = await asyncio.to_thread(store.ch.ping)
    counts = await asyncio.to_thread(store.rule_fire_counts, "family1", since, app) if ok else {}
    rules = [
        {"rule_id": r.rule_id, "check_id": r.check_id(), "engine": r.engine, "fired": counts.get(r.rule_id, 0)}
        for r in _rule_pack.rules
    ]
    fired = [r for r in rules if r["fired"] > 0]
    never = [r for r in rules if r["fired"] == 0]
    return {
        "clickhouse_ok": ok,
        "rule_pack": {
            "configured": bool(_rule_pack.rules),
            "source_skill": _rule_pack.source_skill,
            "source_skill_version": _rule_pack.source_skill_version,
            "total": len(rules),
            "fired": len(fired),
            "never_fired": len(never),
            "never_fired_ids": [r["rule_id"] for r in never],
            "rules": rules,
        },
    }


@app.get("/eval/compliance")
async def compliance(since: int = 0, framework: str = ""):
    """Framework evidence: every control of every selected pack, resolved to
    evidenced / violated / indeterminate / no_evidence / unbound /
    not_configured over the window's verdicts (compliance_design.md §2.3).
    A view over existing verdicts - nothing is evaluated or stored here.
    With no overlay configured every pack is reported and every data class
    is unbound, which is the truthful state, not an error (Hard Rule 9)."""
    ok = await asyncio.to_thread(store.ch.ping)
    rows, truncated = await asyncio.to_thread(store.verdict_rows_for_report, since, config.COMPLIANCE_ROW_CAP) if ok else ([], False)
    retention = await asyncio.to_thread(store.retention_facts) if ok else {}
    facts = {
        "clickhouse_ok": ok,
        "rule_pack_configured": bool(_rule_pack.rules),
        "catalog_configured": bool(_catalog.intents),
        "retention": retention,
        "retention_days": config.RETENTION_DAYS,
        "engine_version": config.ENGINE_VERSION,
        "mode": config.EVAL_MODE,
        "shadow": config.EVAL_MODE != "inline",
        "worker": {"polls": worker.polls, "evaluated_total": worker.evaluated_total, "last_error": worker.last_error},
        "row_cap": config.COMPLIANCE_ROW_CAP,
        "truncated": truncated,
    }
    return compliance_mod.build_report(packs=_packs, overlay=_overlay, verdict_rows=rows, since=since,
                                       facts=facts, only=framework)


@app.get("/eval/compliance/packs")
async def compliance_packs():
    """The loaded Layer A packs, in full - what a deployment's overlay can
    select from, and the reference for authoring an extra pack."""
    return {
        "packs": [
            {
                "framework": p.framework, "title": p.title, "version": p.version,
                "out_of_scope": list(p.out_of_scope),
                "controls": [
                    {"id": c.id, "title": c.title, "control_class": c.control_class,
                     "data_class": c.data_class, "note": c.note,
                     "check_ids": list(compliance_mod.CONTROL_CLASS_CHECKS.get(c.control_class, ()))}
                    for c in p.controls
                ],
            }
            for p in _packs.values()
        ],
        "control_classes": {k: list(v) for k, v in compliance_mod.CONTROL_CLASS_CHECKS.items()},
        "field_aware_checks": sorted(compliance_mod.FIELD_AWARE_CHECKS),
    }


@app.post("/eval/sync")
async def sync_now():
    try:
        report = await worker.poll_once()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    return {"report": report, "status": worker.status()}


@app.post("/eval/run")
async def run(session_id: str, force: bool = False):
    spans = await asyncio.to_thread(store.session_spans, session_id)
    if not spans:
        raise HTTPException(status_code=404, detail="session not found (no spans)")
    result = await asyncio.to_thread(
        evaluate.evaluate_and_persist, session_id, _binding, _visibility, _rule_pack, _catalog, force,
        # The registry, same as the worker passes. Without it this route
        # evaluates against the DEPLOYMENT defaults while the worker uses the
        # application's own artifacts — two code paths producing different
        # version keys for the same session, which is exactly the divergence
        # Hard Rule 12 ("one code path") exists to prevent.
        checks_mod.current(), _apps
    )
    return result


@app.post("/eval/population")
async def population(scenario_id: str = "", variant: str = "", baseline_variant: str = "",
                     compare_variant: str = "", rule_id: str = "", app: str = AppQ):
    if not scenario_id and not rule_id:
        raise HTTPException(400, "supply scenario_id and/or rule_id")
    result = await asyncio.to_thread(
        evaluate.evaluate_population, scenario_id, variant, baseline_variant, compare_variant, rule_id,
        _visibility, checks_mod.current(), app
    )
    return result


@app.get("/eval/findings")
async def findings(check_id: str = "", family: str = "", limit: int = 100, offset: int = 0, since: int = 0,
                   app: str = AppQ):
    return await asyncio.to_thread(store.list_findings, check_id=check_id, family=family, limit=limit,
                                   offset=offset, since=since, app=app)


@app.get("/eval/verdicts")
async def verdicts(status: str = "", check_id: str = "", family: str = "", limit: int = 100, offset: int = 0,
                   since: int = 0, include_disabled: bool = False, app: str = AppQ):
    """The unified feed: every verdict regardless of status (satisfied included),
    so a clean session shows up beside the violations, associated with the
    policy/rule it satisfied. `status` narrows to one outcome when set.

    `include_disabled=true` additionally returns the records a check wrote
    before it was disabled - normally hidden on every read (ch._disabled_clause)
    - and names that set in `disabled_checks` so the caller can label them.
    Disabling a check has never DELETED anything; this is how a reader sees
    what the switch is hiding without having to turn the check back on."""
    return await asyncio.to_thread(store.list_feed, status=status, check_id=check_id, family=family,
                                   limit=limit, offset=offset, since=since,
                                   include_disabled=include_disabled, app=app)


@app.get("/eval/conformance")
async def conformance(limit: int = 100, offset: int = 0, since: int = 0, app: str = AppQ):
    return await asyncio.to_thread(store.list_conformance, limit=limit, offset=offset, since=since, app=app)


@app.get("/eval/sessions/{session_id}/verdicts")
async def session_verdicts(session_id: str, status: str = ""):
    result = await asyncio.to_thread(store.list_verdicts, session_id=session_id, status=status, limit=500)
    return result


@app.get("/eval/sessions/{session_id}/conformance")
async def session_conformance(session_id: str):
    tags = await asyncio.to_thread(store.session_conformance, session_id)
    return {"session_id": session_id, "conformance_tags": tags}


@app.get("/eval/checks")
async def get_checks(app: str = AppQ):
    """The full check registry grouped by family, each check flagged enabled or
    disabled - what the Settings panel renders.

    With `app`, this reports the set actually IN FORCE for that application,
    which is not always the deployment's: a registered application may declare
    its own disabled set, and that REPLACES the deployment-wide one
    (applications.AppConfig.settings). Without this the panel would show a
    switch that quietly does not apply to the application the operator is
    looking at — the "two switches, unpredictable combined effect" outcome
    TODO entry 8 names as the worst of the three.

    `source` says which is in force, so the UI can label a per-application set
    as coming from the registry (an artifact, edited on disk) rather than
    implying the PUT below would change it.
    """
    deployment = checks_mod.current()
    cfg = _apps.for_app(app)
    effective = cfg.settings(deployment)
    out = checks_mod.describe(effective, _configured_families(app))
    out["source"] = "application" if effective is not deployment else "deployment"
    out["application"] = app or None
    # PUT/DELETE below write the DEPLOYMENT set. When an application overrides
    # it, saying so is the difference between an honest panel and one that
    # appears to accept an edit with no effect.
    out["editable"] = out["source"] == "deployment"
    return out


@app.put("/eval/checks")
async def put_checks(body: dict = Body(...)):
    """Replace the disabled set. Body: `{"disabled": ["check_id", ...]}`.

    Whole-set replacement rather than per-check toggles: the settings UI
    edits a list and saves it, and a PATCH-per-check API would make two
    concurrent editors silently merge into a state neither of them chose.

    Unknown ids are dropped rather than rejected (`CheckSettings.from_ids`),
    so a client built against a different engine version cannot lock itself
    out of saving. The response says exactly what was stored."""
    raw = body.get("disabled", [])
    if not isinstance(raw, list):
        raise HTTPException(400, "`disabled` must be an array of check ids")
    if len(raw) > len(checks_mod.REGISTRY):
        raise HTTPException(400, "more ids than there are checks")
    settings = checks_mod.CheckSettings.from_ids(str(i) for i in raw)
    unknown = sorted({str(i) for i in raw} - settings.disabled)
    try:
        await asyncio.to_thread(store.save_check_settings, settings)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"could not persist check settings: {type(e).__name__}: {e}")
    checks_mod.set_current(settings)
    # Re-evaluate straight away rather than waiting out the poll interval:
    # the version key just changed, so every session the worker can see is
    # now eligible, and the user is watching a page that shows the result.
    worker.wake()
    return {**checks_mod.describe(settings, _configured_families()), "unknown": unknown}


@app.delete("/eval/checks")
async def reset_checks():
    """Forget the stored set - every check enabled again, the default."""
    try:
        await asyncio.to_thread(store.clear_check_settings)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"could not clear check settings: {type(e).__name__}: {e}")
    settings = checks_mod.set_current(checks_mod.EMPTY)
    worker.wake()
    return checks_mod.describe(settings, _configured_families())


@app.delete("/eval/verdicts")
async def clear(app: str = AppQ):
    """Clear verdicts, conformance tags and evaluated-session markers.

    `app` clears ONE application's; empty clears every application's. The
    parameter exists because every read on this service is app-scoped now, and
    a delete that is not would destroy other applications' evidence from a
    surface labelled with one — mislabelling, but irreversible. Callers that
    genuinely mean "everything" pass nothing and get the old behaviour.

    Unattributed rows (empty app_id) are left alone by a scoped clear: they may
    belong to any application, and guessing would delete another's evidence.
    """
    await asyncio.to_thread(store.truncate, app)
    return {"ok": True, "app": app or None, "scope": "application" if app else "deployment"}
