"""FastAPI app: /eval/* - the query API the Findings UI (and any other
consumer) reads, plus the worker's control endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import binding as binding_mod
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _wait_for_clickhouse()
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
async def status():
    ok = await asyncio.to_thread(store.ch.ping)
    totals = await asyncio.to_thread(store.totals) if ok else {}
    return {
        "clickhouse": {"ok": ok, "url": config.CLICKHOUSE_URL, "database": config.CLICKHOUSE_DB, **totals},
        "worker": worker.status(),
        "profiles": {
            "trace_binding": {"path": config.TRACE_BINDING_PATH or "(bundled default)", "version": _binding.version},
            "visibility": {"path": config.VISIBILITY_PROFILE_PATH or "(bundled default)", "version": _visibility.version},
            "rule_pack": {"path": config.RULE_PACK_PATH or "(not configured)", "rule_count": len(_rule_pack.rules)},
            "intent_catalog": {"path": config.INTENT_CATALOG_PATH or "(not configured)",
                              "intent_count": len(_catalog.intents)},
        },
        "engine_version": config.ENGINE_VERSION,
        "mode": config.EVAL_MODE,
        "started_at": _started_at.isoformat(),
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
        evaluate.evaluate_and_persist, session_id, _binding, _visibility, _rule_pack, _catalog, force
    )
    return result


@app.get("/eval/findings")
async def findings(check_id: str = "", family: str = "", limit: int = 100, offset: int = 0):
    return await asyncio.to_thread(store.list_findings, check_id=check_id, family=family, limit=limit, offset=offset)


@app.get("/eval/sessions/{session_id}/verdicts")
async def session_verdicts(session_id: str, status: str = ""):
    result = await asyncio.to_thread(store.list_verdicts, session_id=session_id, status=status, limit=500)
    return result


@app.get("/eval/sessions/{session_id}/conformance")
async def session_conformance(session_id: str):
    tags = await asyncio.to_thread(store.session_conformance, session_id)
    return {"session_id": session_id, "conformance_tags": tags}


@app.delete("/eval/verdicts")
async def clear():
    await asyncio.to_thread(store.truncate)
    return {"ok": True}
