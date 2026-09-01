"""Background loop: find sessions that look closed (no new spans for
EVAL_QUIET_SECONDS) and haven't been evaluated at the current artifact
versions yet, and evaluate them. Idempotent - re-running over the same spans
and versions is a fast no-op (store.is_evaluated), so the poll interval only
trades off latency, never correctness (Hard Rule 12).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from . import checks as checks_mod
from . import config, evaluate, store
from .binding import BindingProfile
from .family1.compilepack import RulePack
from .family3.catalog import IntentCatalog
from .visibility import VisibilityProfile

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, binding: BindingProfile, visibility: VisibilityProfile, rule_pack: RulePack,
                catalog: IntentCatalog) -> None:
        self.binding = binding
        self.visibility = visibility
        self.rule_pack = rule_pack
        self.catalog = catalog
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self.polls = 0
        self.evaluated_total = 0
        self.skipped_total = 0
        self.last_error = ""
        self.last_poll: Optional[datetime] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="eval-worker")

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
                await self.poll_once()
            except Exception as e:  # noqa: BLE001 - keep the loop alive, surface in /eval/status
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("eval poll failed: %s", self.last_error)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=config.EVAL_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def poll_once(self) -> dict:
        candidates = await asyncio.to_thread(store.candidate_sessions, config.EVAL_QUIET_SECONDS)
        # Read the check settings ONCE per poll and pass them down, rather
        # than letting each call re-read the module holder: a write landing
        # mid-poll would otherwise evaluate some sessions under the old set
        # and mark them with the new version key. Reading per poll (not per
        # process) is also what makes a settings change take effect without
        # a restart - the next poll picks it up.
        settings = checks_mod.current()
        vkey = evaluate.version_key(self.binding, self.visibility, self.rule_pack, self.catalog, settings)
        results = []
        for row in candidates:
            session_id = row["session_id"]
            if await asyncio.to_thread(store.is_evaluated, session_id, vkey):
                self.skipped_total += 1
                continue
            result = await asyncio.to_thread(
                evaluate.evaluate_and_persist, session_id, self.binding, self.visibility,
                self.rule_pack, self.catalog, False, settings
            )
            results.append(result)
            self.evaluated_total += 1
        self.polls += 1
        self.last_poll = datetime.now(timezone.utc)
        self.last_error = ""
        return {"candidates": len(candidates), "evaluated": len(results), "results": results}

    def status(self) -> dict:
        return {
            "polls": self.polls,
            "evaluated_total": self.evaluated_total,
            "skipped_total": self.skipped_total,
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
            "last_error": self.last_error,
            "quiet_seconds": config.EVAL_QUIET_SECONDS,
            "poll_seconds": config.EVAL_POLL_SECONDS,
            "binding_profile_version": self.binding.version,
            "visibility_profile_version": self.visibility.version,
            "rule_pack": {
                "configured": bool(self.rule_pack.rules),
                "source_skill": self.rule_pack.source_skill,
                "source_skill_version": self.rule_pack.source_skill_version,
                "rule_count": len(self.rule_pack.rules),
            },
            "intent_catalog": {
                "configured": bool(self.catalog.intents),
                "version": self.catalog.version,
                "intent_count": len(self.catalog.intents),
            },
            "checks": {
                "total": len(checks_mod.REGISTRY),
                "enabled": len(checks_mod.REGISTRY) - len(checks_mod.current().disabled),
                "disabled": sorted(checks_mod.current().disabled),
                "version": checks_mod.current().version,
            },
        }
