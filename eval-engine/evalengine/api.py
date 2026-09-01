"""FastAPI app: /eval/* - the query API the Findings UI (and any other
consumer) reads, plus the worker's control endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import binding as binding_mod
from . import checks as checks_mod
from . import compliance as compliance_mod
from . import config, evaluate, store, visibility as visibility_mod
from .family1 import compilepack as rulepack_mod
from .family3 import catalog as catalog_mod
from .worker import Worker

logging.basicConfig(level=config.EVAL_LOG_LEVEL.upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("evalengine")

_binding = binding_mod.load(config.TRACE_BINDING_PATH)
_visibility = visibility_mod.load(config.VISIBILITY_PROFILE_PATH)
_rule_pack = rulepack_mod.load(config.RULE_PACK_PATH)
_catalog = catalog_mod.load(config.INTENT_CATALOG_PATH)
_packs = compliance_mod.load_packs(config.FRAMEWORK_PACKS_DIR)
_overlay = compliance_mod.load_overlay(config.COMPLIANCE_OVERLAY_PATH)
worker = Worker(_binding, _visibility, _rule_pack, _catalog)
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


def _configured_families() -> dict[str, bool]:
    """Which families have the artifact they need. Family 2 is built in and
    always runs (Hard Rule 9); Family 1 needs a rule pack and Family 3 an
    intent catalog, so a settings UI can tell "you turned this off" apart
    from "nothing is configured, so it is idle anyway"."""
    return {
        "family1": bool(_rule_pack.rules),
        "family2": True,
        "family3": bool(_catalog.intents),
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


@app.get("/eval/health")
async def health():
    ok = await asyncio.to_thread(store.ch.ping)
    return {"ok": ok, "clickhouse": ok}


@app.get("/eval/status")
async def status(since: int = 0):
    ok = await asyncio.to_thread(store.ch.ping)
    totals = await asyncio.to_thread(store.totals, since) if ok else {}
    return {
        "clickhouse": {"ok": ok, "url": config.CLICKHOUSE_URL, "database": config.CLICKHOUSE_DB, **totals},
        "worker": worker.status(),
        "profiles": {
            "trace_binding": {"path": config.TRACE_BINDING_PATH or "(bundled default)", "version": _binding.version},
            "visibility": {"path": config.VISIBILITY_PROFILE_PATH or "(bundled default)", "version": _visibility.version},
            "rule_pack": {"path": config.RULE_PACK_PATH or "(not configured)", "rule_count": len(_rule_pack.rules)},
            "intent_catalog": {"path": config.INTENT_CATALOG_PATH or "(not configured)",
                              "intent_count": len(_catalog.intents)},
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


@app.get("/eval/coverage")
async def coverage(since: int = 0):
    """Rule-pack coverage: which Family-1 rules have ever produced a verdict vs.
    which have never had matching traffic ("never hit"). Authoritative — counts
    over all verdicts server-side, not the capped UI slice. Degrades to
    configured=false / zero rules when no rule pack is loaded (Hard Rule 9).
    Optionally windowed to the last `since` seconds (never-hit within window)."""
    ok = await asyncio.to_thread(store.ch.ping)
    counts = await asyncio.to_thread(store.rule_fire_counts, "family1", since) if ok else {}
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
        checks_mod.current()
    )
    return result


@app.post("/eval/population")
async def population(scenario_id: str = "", variant: str = "", baseline_variant: str = "",
                     compare_variant: str = "", rule_id: str = ""):
    if not scenario_id and not rule_id:
        raise HTTPException(400, "supply scenario_id and/or rule_id")
    result = await asyncio.to_thread(
        evaluate.evaluate_population, scenario_id, variant, baseline_variant, compare_variant, rule_id,
        _visibility, checks_mod.current()
    )
    return result


@app.get("/eval/findings")
async def findings(check_id: str = "", family: str = "", limit: int = 100, offset: int = 0, since: int = 0):
    return await asyncio.to_thread(store.list_findings, check_id=check_id, family=family, limit=limit, offset=offset, since=since)


@app.get("/eval/verdicts")
async def verdicts(status: str = "", check_id: str = "", family: str = "", limit: int = 100, offset: int = 0,
                   since: int = 0, include_disabled: bool = False):
    """The unified feed: every verdict regardless of status (satisfied included),
    so a clean session shows up beside the violations, associated with the
    policy/rule it satisfied. `status` narrows to one outcome when set.

    `include_disabled=true` additionally returns the records a check wrote
    before it was disabled - normally hidden on every read (ch._disabled_clause)
    - and names that set in `disabled_checks` so the caller can label them.
    Disabling a check has never DELETED anything; this is how a reader sees
    what the switch is hiding without having to turn the check back on."""
    return await asyncio.to_thread(store.list_feed, status=status, check_id=check_id, family=family,
                                   limit=limit, offset=offset, since=since, include_disabled=include_disabled)


@app.get("/eval/conformance")
async def conformance(limit: int = 100, offset: int = 0, since: int = 0):
    return await asyncio.to_thread(store.list_conformance, limit=limit, offset=offset, since=since)


@app.get("/eval/sessions/{session_id}/verdicts")
async def session_verdicts(session_id: str, status: str = ""):
    result = await asyncio.to_thread(store.list_verdicts, session_id=session_id, status=status, limit=500)
    return result


@app.get("/eval/sessions/{session_id}/conformance")
async def session_conformance(session_id: str):
    tags = await asyncio.to_thread(store.session_conformance, session_id)
    return {"session_id": session_id, "conformance_tags": tags}


@app.get("/eval/checks")
async def get_checks():
    """The full check registry grouped by family, each check flagged enabled
    or disabled for this deployment - what the Settings panel renders."""
    return checks_mod.describe(configured=_configured_families())


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
async def clear():
    await asyncio.to_thread(store.truncate)
    return {"ok": True}
