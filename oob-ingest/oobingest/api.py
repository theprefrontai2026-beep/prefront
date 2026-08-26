"""FastAPI app: the OTLP receiver + the /oob query API the UI reads."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import ch, config, otlp
from .model import drop_inline
from .phoenix_source import PhoenixPoller

logging.basicConfig(level=os.environ.get("OOB_LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oobingest")
logging.getLogger("httpx").setLevel(logging.WARNING)

poller = PhoenixPoller()
_otlp_received = {"requests": 0, "spans": 0, "excluded_inline": 0, "last": None, "last_error": ""}
_otlp_dropped: set[str] = set()
_started_at = datetime.now(timezone.utc)


async def _wait_for_clickhouse() -> None:
    delay = 1.0
    for attempt in range(60):
        try:
            ch.ensure_schema()
            log.info("clickhouse ready (%s, db=%s)", config.CLICKHOUSE_URL, config.CLICKHOUSE_DB)
            return
        except Exception as e:  # noqa: BLE001
            ch.reset_client()
            log.warning("clickhouse not ready (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)
    log.error("clickhouse never became ready; continuing, requests will fail until it does")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _wait_for_clickhouse()
    # Retro-fix any rows stored with a guessed service name before the OTLP tap
    # supplied the real one.
    for project in (config.PHOENIX_PROJECTS or [os.environ.get("PHOENIX_PROJECT_NAME", "prefront")]):
        try:
            n = await asyncio.to_thread(ch.relabel_phoenix_from_otlp, project)
            if n:
                log.info("relabelled %d phoenix-sourced spans in %s from OTLP names", n, project)
        except Exception as e:  # noqa: BLE001 - never block startup on a cosmetic fix
            log.warning("relabel skipped (%s: %s)", type(e).__name__, e)
    poller.start()
    yield
    await poller.stop()


app = FastAPI(title="Prefront OOB ingestion", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SinceQ = Query(default=None, ge=0, description="Only spans that started in the last N seconds (0/None = all time)")
ProjectQ = Query(default="", description="Restrict to one Phoenix project")


def _bucket_for(since: Optional[int]) -> int:
    if not since:
        return 3600
    if since <= 900:
        return 30
    if since <= 3600:
        return 60
    if since <= 6 * 3600:
        return 300
    if since <= 24 * 3600:
        return 900
    return 3600


# --- OTLP receiver -------------------------------------------------------------

@app.post("/v1/traces")
async def otlp_traces(request: Request):
    if config.OTLP_API_KEY:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {config.OTLP_API_KEY}":
            raise HTTPException(status_code=401, detail="bad api key")
    ctype = request.headers.get("content-type", "application/x-protobuf")
    body = await request.body()
    project = request.headers.get("x-prefront-project") or os.environ.get("PHOENIX_PROJECT_NAME", "prefront")
    try:
        rows = otlp.decode(body, ctype, project)
        before = len(rows)
        rows = drop_inline(rows, _otlp_dropped)
        _otlp_received["excluded_inline"] += before - len(rows)
        n = await asyncio.to_thread(ch.insert_spans, rows)
        _otlp_received["requests"] += 1
        _otlp_received["spans"] += n
        _otlp_received["last"] = datetime.now(timezone.utc).isoformat()
        _otlp_received["last_error"] = ""
    except Exception as e:  # noqa: BLE001
        _otlp_received["last_error"] = f"{type(e).__name__}: {e}"
        log.warning("otlp decode/insert failed: %s", _otlp_received["last_error"])
        raise HTTPException(status_code=400, detail=_otlp_received["last_error"])
    payload, mime = otlp.empty_response(as_json="json" in ctype.lower())
    return Response(content=payload, media_type=mime)


# --- status / control -----------------------------------------------------------

@app.get("/oob/health")
async def health():
    ok = await asyncio.to_thread(ch.ping)
    return {"ok": ok, "clickhouse": ok}


@app.get("/oob/status")
async def status():
    ok = await asyncio.to_thread(ch.ping)
    totals = await asyncio.to_thread(ch.totals) if ok else {}
    return {
        "clickhouse": {"ok": ok, "url": config.CLICKHOUSE_URL, "database": config.CLICKHOUSE_DB, **totals},
        "phoenix": poller.status(),
        "otlp": {"endpoint": "/v1/traces", "api_key_required": bool(config.OTLP_API_KEY), **_otlp_received},
        "started_at": _started_at.isoformat(),
    }


@app.post("/oob/sync")
async def sync_now(relabel: bool = True):
    try:
        report = await poller.sync_once()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    relabelled = 0
    if relabel:
        for project in poller.projects or ["prefront"]:
            try:
                relabelled += await asyncio.to_thread(ch.relabel_phoenix_from_otlp, project)
            except Exception as e:  # noqa: BLE001
                log.warning("relabel skipped for %s (%s: %s)", project, type(e).__name__, e)
    return {"ingested": report, "relabelled": relabelled, "status": poller.status()}


@app.delete("/oob/spans")
async def clear():
    await asyncio.to_thread(ch.truncate)
    poller.watermarks.clear()
    return {"ok": True}


# --- query API -------------------------------------------------------------------

@app.get("/oob/overview")
async def overview(since: Optional[int] = SinceQ, project: str = ProjectQ):
    return await asyncio.to_thread(ch.overview, since, project, _bucket_for(since))


@app.get("/oob/facets")
async def facets(since: Optional[int] = SinceQ, project: str = ProjectQ):
    return await asyncio.to_thread(ch.facets, since, project)


@app.get("/oob/traces")
async def traces(since: Optional[int] = SinceQ, project: str = ProjectQ, service: str = "",
                 kind: str = "", status: str = "", scenario: str = "",
                 q: str = "", limit: int = 50, offset: int = 0):
    return await asyncio.to_thread(
        ch.list_traces, since, project, service=service, kind=kind.upper(), status=status,
        scenario=scenario, q=q, limit=limit, offset=offset,
    )


@app.get("/oob/traces/{trace_id}")
async def trace(trace_id: str):
    spans = await asyncio.to_thread(ch.trace_detail, trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail="trace not found")
    return {"trace_id": trace_id, "spans": spans}


@app.get("/oob/llm")
async def llm(since: Optional[int] = SinceQ, project: str = ProjectQ, limit: int = 50):
    return await asyncio.to_thread(ch.llm_view, since, project, limit)


@app.get("/oob/scenarios")
async def scenarios(since: Optional[int] = SinceQ, project: str = ProjectQ):
    return {"scenarios": await asyncio.to_thread(ch.scenarios_view, since, project)}
