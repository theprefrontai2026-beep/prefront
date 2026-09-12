"""Plain-language explanations of eval-engine findings, for a reviewer.

A finding's `detail` is written by a deterministic check for the check's own
purposes: "rule R-X: restricted field(s) ['f (final answer)'] surfaced on turn
0's answer" is exact and reproducible, and close to unreadable for the person
who has to act on it. A small model rewrites it in plain words.

Advisory only, and outside the evaluator on purpose: eval-engine's verdicts
stay deterministic (its checks are pure — Hard Rule 3), and nothing here
changes a verdict, an effect or a severity. The check's own wording is always
shown beside the summary. Cached by content, so a finding is paid for once
however often it is opened — and identical findings share one summary.

Summaries are written as findings APPEAR, by ExplainWorker below: it polls
eval-engine's findings feed rather than being called by it, so evaluation
never waits on, or fails because of, a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from pydantic import BaseModel

from .logutil import get_logger

log = get_logger(__name__)

# A small model on purpose: this rewrites text it is handed, and needs no
# reasoning the check has not already done. gpt-4o-mini rather than the
# cheaper gpt-4.1-nano: side by side on 8 LoanPro findings, nano named the
# application number (7003) where the check compared an applicant id (5003)
# and called a recommendation an approval, even with the rules below;
# gpt-4o-mini got both right. The saving was cents at this volume.
# Changing it re-summarises every finding once — the model is in the cache key.
DEFAULT_EXPLAIN_MODEL = os.environ.get("SEMANTICLAYER_EXPLAIN_MODEL") or "gpt-4o-mini"

# Bump whenever EXPLAIN_SYSTEM changes. It is part of the cache key, so a
# changed prompt re-summarises every finding instead of leaving the old
# wording in place for everything already cached.
PROMPT_VERSION = 2


class FindingIn(BaseModel):
    """The fields of an eval-engine finding the explanation is written from."""
    check_id: str = ""
    rule_id: str = ""
    family_label: str = ""
    status: str = ""
    effect: str = ""
    detail: str
    evidence_excerpt: str = ""
    source: str = ""        # eval-engine's JSON-encoded citation: {document, section, text}
    user_query: str = ""
    indeterminate_reason: str = ""   # why the check could not tell, when it could not
    # Which finding this is, so its summary can be found by event later. NOT
    # part of the cache key: identical findings share one summary.
    event_id: str = ""
    app_id: str = ""


class Explanation(BaseModel):
    headline: str
    explanation: str


EXPLAIN_SYSTEM = """You explain ONE finding from an automated compliance check \
of an AI agent's behaviour, for a business reader who has to decide what to do \
about it.

You are given the check's own technical wording, the evidence it recorded, the \
policy text it cites (if any), and the question the user asked the agent.

Write:
- "headline": under 14 words saying what went wrong, in plain language. Lead \
with what the agent did.
- "explanation": two short sentences. First, what happened in this \
conversation. Second, why it matters - grounded in the cited policy when one \
is given.

Rules:
- Plain words only. No rule ids, check ids, turn or step numbers, span ids, or \
snake_case field names - say "the date of birth", not "date_of_birth".
- Use ONLY facts in the input. Do not guess who the user was, what the agent \
intended, or what happened afterwards.
- Numbers and identifiers: use only values that appear in the check's wording \
or evidence, copied exactly. Never substitute one identifier for another - a \
number from the user's question is not the one the check compared against. \
If you are unsure which number is which, leave the number out.
- Describe the agent's action exactly as the input does. Do not upgrade it: a \
recommendation is not an approval, and reading data is not sharing it.
- Do not soften or exaggerate. A violated check is a violation, not a \
suggestion; an indeterminate one means the check could not tell.

Return STRICT JSON only: {"headline": "...", "explanation": "..."}"""


def _citation(source: str) -> dict:
    try:
        parsed = json.loads(source) if source else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def render(f: FindingIn) -> str:
    cite = _citation(f.source)
    lines = [
        f"check: {f.check_id}" + (f" ({f.family_label})" if f.family_label else ""),
        f"outcome: {f.status}" + (f", effect {f.effect}" if f.effect else ""),
        f"technical wording: {f.detail}",
    ]
    if f.indeterminate_reason:
        lines.append(f"why the check could not tell: {f.indeterminate_reason}")
    if f.evidence_excerpt:
        lines.append(f"evidence recorded: {f.evidence_excerpt}")
    if cite.get("text"):
        section = f"{cite['section']}: " if cite.get("section") else ""
        lines.append(f"policy cited: {section}{str(cite['text']).strip()}")
    if f.user_query:
        lines.append(f"the user asked the agent: {f.user_query}")
    return "\n".join(lines)


def cache_key(f: FindingIn, model: str) -> str:
    """Content-addressed: the same finding content explains once, and any change
    to what the check said — or a different model or prompt — is a different
    entry."""
    parts = [model, f.check_id, f.rule_id, f.status, f.effect, f.detail,
             f.evidence_excerpt, f.source, f.user_query]
    if f.indeterminate_reason:
        parts.append(f.indeterminate_reason)
    parts.append(f"prompt:{PROMPT_VERSION}")
    payload = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def explain(f: FindingIn, llm) -> Explanation:
    """One model call. Raises on output that is not the promised JSON — the
    caller shows the check's own wording instead, never a half-parsed guess."""
    raw = llm.complete(EXPLAIN_SYSTEM, render(f))
    ex = Explanation.model_validate(json.loads(raw))
    if not ex.headline.strip() or not ex.explanation.strip():
        raise ValueError("empty headline or explanation")
    return ex


# ── Explaining findings as they are created ──────────────────────────────

_FIELDS = ("check_id", "rule_id", "family_label", "status", "effect", "detail",
           "evidence_excerpt", "source", "user_query", "indeterminate_reason", "event_id", "app_id")

Feed = Callable[[int, int, int], list[dict]]


def _http_page(eval_url: str, path: str = "/eval/findings", key: str = "findings",
               extra: Optional[dict] = None) -> Feed:
    def fetch(since: int, limit: int, offset: int) -> list[dict]:
        q = urllib.parse.urlencode({"since": since, "limit": limit, "offset": offset, **(extra or {})})
        with urllib.request.urlopen(f"{eval_url.rstrip('/')}{path}?{q}", timeout=30) as r:
            return list(json.load(r).get(key) or [])
    return fetch


def _eval_feeds(eval_url: str) -> list[Feed]:
    """Everything a reader sees as "something went wrong": /eval/findings is
    violations only, so indeterminate verdicts come from the unified feed."""
    return [_http_page(eval_url),
            _http_page(eval_url, "/eval/verdicts", "verdicts", {"status": "indeterminate"})]


class ExplainWorker:
    """Summarises new findings in the background, as eval-engine writes them.

    Each round pages the findings feed over a `since` window, and for every
    violated/indeterminate finding either links it to an existing summary (same
    content) or writes one, up to `per_poll` model calls a round. It starts on
    the long `backfill_seconds` window and stays there until a round finishes
    under budget, so a backlog is worked through rather than skipped when the
    window shortens. A finding whose summary fails 3 times is left to the
    on-demand path.
    """

    def __init__(self, store, llm_factory: Callable[[], Any], model: str = DEFAULT_EXPLAIN_MODEL,
                 eval_url: str = "", fetch: Optional[Feed] = None,
                 feeds: Optional[list[Feed]] = None,
                 poll_seconds: int = 10, backfill_seconds: int = 30 * 86400, per_poll: int = 60,
                 concurrency: int = 6, page_size: int = 500, max_rows: int = 10000) -> None:
        self.store = store
        self.llm_factory = llm_factory
        self.model = model
        self.feeds = feeds or ([fetch] if fetch else _eval_feeds(eval_url))
        self.poll_seconds = poll_seconds
        self.backfill_seconds = backfill_seconds
        self.per_poll = per_poll
        self.concurrency = concurrency
        self.page_size = page_size
        self.max_rows = max_rows
        self.backfilling = True
        self.failures: dict[str, int] = {}
        self._llm = None

    def _findings(self, since: int):
        for feed in self.feeds:
            offset = 0
            while offset < self.max_rows:
                page = feed(since, self.page_size, offset)
                yield from page
                if len(page) < self.page_size:
                    break
                offset += self.page_size

    def poll_once(self) -> int:
        """One round. Returns how many model calls it made.

        The calls run CONCURRENTLY: they are independent, each takes a couple
        of seconds, and done one after another a round of 25 kept a reader
        waiting over a minute for a summary of something that had already
        happened.
        """
        since = self.backfill_seconds if self.backfilling else max(self.poll_seconds * 4, 600)
        rows, todo = [], {}
        for row in self._findings(since):
            if row.get("status") == "satisfied" or not row.get("detail"):
                continue
            f = FindingIn.model_validate({k: str(row.get(k) or "") for k in _FIELDS})
            rows.append(f)
            key = cache_key(f, self.model)
            if self.store.get_explanation(key) or self.failures.get(key, 0) >= 3:
                continue
            todo.setdefault(key, f)      # one call per distinct content

        over_budget = len(todo) > self.per_poll
        batch = list(todo.items())[:self.per_poll]
        if batch:
            if self._llm is None:
                self._llm = self.llm_factory()
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                futures = {pool.submit(explain, f, self._llm): (key, f) for key, f in batch}
                for fut in as_completed(futures):
                    key, f = futures[fut]
                    try:
                        ex = fut.result()
                    except Exception as e:  # noqa: BLE001 - one bad finding must not stop the round
                        self.failures[key] = self.failures.get(key, 0) + 1
                        log.warning("could not explain finding %s: %s", f.event_id, e)
                        continue
                    self.store.put_explanation(key, ex.headline, ex.explanation, self.model)

        for f in rows:                   # link every event whose summary now exists
            if f.event_id and self.store.get_explanation(cache_key(f, self.model)):
                self.store.link_explanation(f.app_id, f.event_id, cache_key(f, self.model))
        self.backfilling = over_budget
        return len(batch)

    def run_forever(self) -> None:
        while True:
            try:
                n = self.poll_once()
                if n:
                    log.info("explained %d finding(s)%s", n, " (backlog remaining)" if self.backfilling else "")
            except Exception as e:  # noqa: BLE001 - eval-engine down is a pause, not a crash
                log.warning("finding explainer round failed: %s", e)
            time.sleep(self.poll_seconds)
