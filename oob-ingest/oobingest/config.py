"""Environment-driven settings. Every knob has a compose-friendly default."""

from __future__ import annotations

import json
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
