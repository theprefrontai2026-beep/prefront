"""Tail Arize Phoenix's REST API into ClickHouse.

Phoenix is the OTLP collector every Prefront service already exports to, so
"hooking Phoenix" gives OOB ingestion every span the stack produces without a
single change to the services. The poller:

* lists projects (or uses PHOENIX_PROJECTS),
* per project, pages ``GET /v1/projects/{p}/spans?start_time=<watermark-lookback>``
  through ``next_cursor`` until exhausted,
* normalizes -> ``SpanRow`` and bulk-inserts,
* persists a watermark (max start_time seen) per project in ClickHouse.

Overlap is deliberate (see PHOENIX_LOOKBACK_SECONDS): the table is a
ReplacingMergeTree keyed by (trace_id, span_id), so re-ingesting a span is a
no-op after merge, and reads use FINAL.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import ch, config
from .model import SpanRow, drop_inline, from_phoenix, inherit_services, parse_ts

log = logging.getLogger(__name__)

# Polls a parent-less span is held for before it is dropped as unprovable.
ORPHAN_MAX_TRIES = 5


class PhoenixPoller:
    def __init__(self) -> None:
        self.endpoint = config.PHOENIX_URL
        self.enabled = bool(self.endpoint)
        self.last_sync: Optional[datetime] = None
        self.last_error: str = ""
        self.ingested_total = 0
        self.polls = 0
        self.watermarks: dict[str, Optional[datetime]] = {}
        self.projects: list[str] = []
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._busy = asyncio.Lock()
        # (trace_id, span_id) already written, per project — the lookback window
        # re-reads recent spans on every poll and this keeps those re-reads from
        # becoming re-inserts. Bounded: entries older than the lookback are dropped.
        self._seen: dict[str, dict[tuple[str, str], datetime]] = {}
        self._dropped: dict[str, set[str]] = {}   # inline span ids excluded, per project
        # Spans whose parent has not been seen yet. Phoenix pages are not in
        # parent-before-child order, so a child can arrive in an earlier batch
        # than the parent that excludes it. Such a span is HELD (never admitted
        # on a guess) until its parent is proven kept — or dropped after
        # ORPHAN_MAX_TRIES polls, because an unprovable ancestry may be inline.
        self._pending: dict[str, list[tuple[SpanRow, int]]] = {}
        self.excluded_total = 0
        self.deferred_dropped = 0

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="phoenix-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Exception as e:  # noqa: BLE001 - keep tailing, surface in /status
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("phoenix poll failed: %s", self.last_error)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=config.PHOENIX_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    # --- one sync ------------------------------------------------------------

    async def sync_once(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        async with self._busy:
            async with httpx.AsyncClient(base_url=self.endpoint, timeout=30.0) as http:
                projects = config.PHOENIX_PROJECTS or await self._list_projects(http)
                self.projects = projects
                report: dict[str, Any] = {}
                for project in projects:
                    n = await self._sync_project(http, project)
                    report[project] = n
            self.polls += 1
            self.last_sync = datetime.now(timezone.utc)
            self.last_error = ""
            return report

    async def _list_projects(self, http: httpx.AsyncClient) -> list[str]:
        names: list[str] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = await http.get("/v1/projects", params=params)
            r.raise_for_status()
            body = r.json()
            names.extend(p["name"] for p in body.get("data", []) if p.get("name"))
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return names

    def _watermark(self, project: str) -> Optional[datetime]:
        if project not in self.watermarks:
            raw = ch.get_state(f"phoenix:{project}:watermark")
            self.watermarks[project] = parse_ts(raw) if raw else None
        return self.watermarks[project]

    async def _sync_project(self, http: httpx.AsyncClient, project: str) -> int:
        wm = self._watermark(project)
        params: dict[str, Any] = {"limit": config.PHOENIX_PAGE_SIZE}
        if wm is not None:
            params["start_time"] = (wm - timedelta(seconds=config.PHOENIX_LOOKBACK_SECONDS)).isoformat()

        seen = self._seen.setdefault(project, {})
        batch: list[SpanRow] = []
        newest = wm
        cursor: Optional[str] = None
        pages = 0
        while True:
            q = dict(params)
            if cursor:
                q["cursor"] = cursor
            r = await http.get(f"/v1/projects/{project}/spans", params=q)
            if r.status_code == 404:
                return 0
            r.raise_for_status()
            body = r.json()
            for raw in body.get("data", []):
                row = from_phoenix(raw, project)
                if row is None:
                    continue
                key = (row.trace_id, row.span_id)
                if key in seen:
                    continue
                seen[key] = row.start_time
                batch.append(row)
                if newest is None or row.start_time > newest:
                    newest = row.start_time
            cursor = body.get("next_cursor")
            pages += 1
            if not cursor or pages > 1000:
                break

        if newest is not None:
            horizon = newest - timedelta(seconds=config.PHOENIX_LOOKBACK_SECONDS * 2)
            for k in [k for k, t in seen.items() if t < horizon]:
                del seen[k]
        dropped = self._dropped.setdefault(project, set())
        # Re-attempt spans held from earlier polls alongside this batch.
        pending = self._pending.setdefault(project, [])
        tries = {r.span_id: n for r, n in pending}
        batch = [r for r, _ in pending] + batch
        pending.clear()

        before = len(batch)
        batch = drop_inline(batch, dropped)
        self.excluded_total += before - len(batch)
        if not batch:
            return 0

        batch, held = self._hold_orphans(batch, dropped, tries, pending)
        if held:
            log.debug("phoenix[%s]: holding %d spans with unresolved parents", project, held)
        if not batch:
            return 0
        inherit_services(batch, self._stored_parents(batch))
        n = ch.insert_spans(batch)
        self.ingested_total += n
        if newest is not None and newest != wm:
            self.watermarks[project] = newest
            ch.set_state(f"phoenix:{project}:watermark", newest.isoformat())
        log.info("phoenix[%s]: ingested %d spans (%d pages), watermark=%s", project, n, pages, newest)
        return n

    def _stored_parents(self, batch: list[SpanRow]) -> dict[str, tuple[str, str]]:
        """(parent_span_id, service) for parents already in ClickHouse."""
        missing = {r.parent_span_id for r in batch if r.parent_span_id} - {r.span_id for r in batch}
        if not missing:
            return {}
        return {
            p["span_id"]: (p["parent_span_id"], p["service"])
            for p in ch.rows(
                f"SELECT span_id, parent_span_id, service FROM {ch.T} WHERE span_id IN %(ids)s",
                {"ids": list(missing)},
            )
        }

    def _hold_orphans(self, batch: list[SpanRow], dropped: set[str], tries: dict[str, int],
                      pending: list[tuple[SpanRow, int]]) -> tuple[list[SpanRow], int]:
        """Admit only spans with provable ancestry; hold or drop the rest.

        A span whose parent is neither in this batch, nor already stored (kept),
        nor known-dropped has unprovable ancestry: it may be a child of an inline
        span. Never admit it on a guess.
        """
        ids = {r.span_id for r in batch}
        stored = set(self._stored_parents(batch))
        keep: list[SpanRow] = []
        held = 0
        for r in batch:
            pid = r.parent_span_id
            if not pid or pid in ids or pid in stored:
                keep.append(r)
                continue
            n = tries.get(r.span_id, 0) + 1
            if n >= ORPHAN_MAX_TRIES:
                self.deferred_dropped += 1
                dropped.add(r.span_id)     # its own children must go too
            else:
                pending.append((r, n))
                held += 1
        return keep, held

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "projects": self.projects,
            "poll_seconds": config.PHOENIX_POLL_SECONDS,
            "lookback_seconds": config.PHOENIX_LOOKBACK_SECONDS,
            "polls": self.polls,
            "ingested_total": self.ingested_total,
            "excluded_inline_total": self.excluded_total,
            "unprovable_dropped": self.deferred_dropped,
            "held_for_parent": sum(len(v) for v in self._pending.values()),
            "exclude": {"attr_prefixes": config.EXCLUDE_ATTR_PREFIXES,
                        "span_names": config.EXCLUDE_SPAN_NAMES,
                        "services": config.EXCLUDE_SERVICES,
                        "strip_attr_prefixes": config.STRIP_ATTR_PREFIXES},
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_error": self.last_error,
            "watermarks": {k: (v.isoformat() if v else None) for k, v in self.watermarks.items()},
        }
