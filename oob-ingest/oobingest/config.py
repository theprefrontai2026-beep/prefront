"""Environment-driven settings. Every knob has a compose-friendly default."""

from __future__ import annotations

import json
import json as _json
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


CLICKHOUSE_URL = _env("CLICKHOUSE_URL", "http://clickhouse:8123")
CLICKHOUSE_USER = _env("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = _env("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = _env("CLICKHOUSE_DB", "prefront")

# Phoenix source. Empty PHOENIX_URL disables the poller (OTLP receiver still on).
PHOENIX_URL = _env("PHOENIX_URL", "http://phoenix:6006").rstrip("/")
# Comma-separated Phoenix projects to tail; empty => every project Phoenix has.
PHOENIX_PROJECTS = [p.strip() for p in _env("PHOENIX_PROJECTS").split(",") if p.strip()]
# Rename a Phoenix project on the way IN, as a JSON object
# (e.g. {"old-project": "new-project"}). The partition that separates one
# subject application's traces from another's is the project
# (application_isolation_design.md §5), so a project that has been RETIRED —
# renamed when its services were partitioned — still sits in Phoenix and is
# still polled, and its spans would keep arriving under the dead name. Worse,
# they arrive as the SAME (trace_id, span_id) already stored under the new
# name, so a ReplacingMergeTree resolves them back to the old project and
# silently un-does the migration on every restart. Measured: 21 of 867 spans
# reverted that way.
#
# Applied on both ingest paths, so a one-line alias fixes it permanently
# instead of relabelling the table again after each restart. Config, never
# code — the engine names no project (Hard Rule 1). A malformed value degrades
# to no aliasing rather than stopping ingestion.
def _json_env(name: str) -> dict[str, str]:
    raw = _env(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = _json.loads(raw)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


PROJECT_ALIASES = _json_env("OOB_PROJECT_ALIASES")


def project_alias(project: str) -> str:
    """The canonical project name for `project` (identity if unmapped)."""
    return PROJECT_ALIASES.get(project, project)


PHOENIX_POLL_SECONDS = float(_env("PHOENIX_POLL_SECONDS", "5"))
PHOENIX_PAGE_SIZE = int(_env("PHOENIX_PAGE_SIZE", "500"))
# Re-read this far behind the watermark on every poll so late-arriving spans
# (a batch exporter flushes on its own schedule) are never missed. Dedup is
# ClickHouse's job (ReplacingMergeTree keyed by span), so overlap is free.
PHOENIX_LOOKBACK_SECONDS = float(_env("PHOENIX_LOOKBACK_SECONDS", "300"))

# OOB means out-of-band: nothing inline is observed here. Spans that carry any
# attribute with one of these prefixes, or whose name starts with one of these
# names, are dropped TOGETHER WITH THEIR WHOLE SUBTREE (the governed agent's own
# LLM calls are inline too). Defaults exclude Prefront's engine spans and the
# demo's governed-agent branch; the raw app agent is what remains.
EXCLUDE_ATTR_PREFIXES = [p.strip() for p in _env("OOB_EXCLUDE_ATTR_PREFIXES", "prefront.").split(",") if p.strip()]
EXCLUDE_SPAN_NAMES = [p.strip() for p in _env("OOB_EXCLUDE_SPAN_NAMES", "governed agent,govern ").split(",") if p.strip()]
EXCLUDE_SERVICES = [p.strip() for p in _env("OOB_EXCLUDE_SERVICES", "").split(",") if p.strip()]

# Attributes stripped from a span that is otherwise kept. The demo harness's
# scenario root describes BOTH sides of a run, so its governed-side annotations
# are removed here rather than dropping the root (which is what gives the app
# agent's spans a trace to hang off).
STRIP_ATTR_PREFIXES = [p.strip() for p in
                       _env("OOB_STRIP_ATTR_PREFIXES", "scenario.governed_,scenario.expected").split(",")
                       if p.strip()]

# Bearer/ API key for the OTLP receiver (optional); if set, POST /v1/traces
# must carry `Authorization: Bearer <key>`.
OTLP_API_KEY = _env("OOB_OTLP_API_KEY")

# Retention for the `spans` table as a ClickHouse TTL on start_time. 0 = none
# (unbounded growth, the historical behaviour). Coordinate with Phoenix's own
# retention: a span aged out here but still in Phoenix is re-pulled on the
# next poll only if it is inside PHOENIX_LOOKBACK_SECONDS of the watermark,
# so in practice it stays gone. eval-engine has the matching
# EVAL_RETENTION_DAYS for its verdict tables (compliance_design.md §5.1).
RETENTION_DAYS = int(_env("OOB_RETENTION_DAYS", "0"))

# Per-1M-token prices used for the cost estimate. Override with a JSON map,
# e.g. OOB_MODEL_PRICES='{"gpt-4o-mini": {"input": 0.15, "output": 0.6}}'.
_DEFAULT_PRICES = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
}


def model_prices() -> dict[str, dict[str, float]]:
    raw = _env("OOB_MODEL_PRICES")
    prices = dict(_DEFAULT_PRICES)
    if raw:
        try:
            prices.update(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return prices


def price_for(model: str) -> tuple[float, float]:
    """(input $/1M, output $/1M) for a model; prefix match, unknown => 0."""
    m = (model or "").lower()
    prices = model_prices()
    if m in prices:
        p = prices[m]
        return float(p.get("input", 0)), float(p.get("output", 0))
    best = ""
    for key in prices:
        if m.startswith(key.lower()) and len(key) > len(best):
            best = key
    if best:
        p = prices[best]
        return float(p.get("input", 0)), float(p.get("output", 0))
    return 0.0, 0.0
