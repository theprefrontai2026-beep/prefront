"""ClickHouse: schema, inserts, and the read queries behind the /oob API."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import clickhouse_connect

from . import config
from .model import COLUMNS, SpanRow

log = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()

DDL = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.spans
(
    trace_id        String,
    span_id         String,
    parent_span_id  String,
    name            String,
    kind            LowCardinality(String),
    otel_kind       LowCardinality(String),
    service         LowCardinality(String),
    project         LowCardinality(String),
    source          LowCardinality(String),
    start_time      DateTime64(6, 'UTC'),
    end_time        DateTime64(6, 'UTC'),
    duration_ms     Float64,
    status          LowCardinality(String),
    status_message  String,
    attributes      Map(String, String),
    events          String,
    input_value     String,
    output_value    String,
    llm_model       LowCardinality(String),
    llm_provider    LowCardinality(String),
    tokens_prompt   UInt32,
    tokens_completion UInt32,
    tokens_total    UInt32,
    decision        LowCardinality(String),
    intent          LowCardinality(String),
    caller_role     LowCardinality(String),
    caller_key      String,
    scenario_id     LowCardinality(String),
    tool_name       LowCardinality(String),
    version         UInt8,
    ingested_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(start_time)
ORDER BY (trace_id, span_id)
"""

# Ingestion bookkeeping (watermarks per source/project) survives restarts here.
DDL_STATE = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.ingest_state
(
    key        String,
    value      String,
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY key
"""


def client():
    global _client
    with _lock:
        if _client is None:
            # No server-side session: with one, clickhouse-connect refuses
            # concurrent queries ("Attempt to execute concurrent queries within
            # the same session") — and the poller's reads DO overlap with the
            # OTLP receiver's inserts. Sessionless requests share the HTTP pool.
            _client = clickhouse_connect.get_client(
                dsn=config.CLICKHOUSE_URL,
                username=config.CLICKHOUSE_USER,
                password=config.CLICKHOUSE_PASSWORD,
                connect_timeout=5,
                send_receive_timeout=60,
                autogenerate_session_id=False,
            )
        return _client


def reset_client() -> None:
    global _client
    with _lock:
        _client = None


def ensure_schema() -> None:
    c = client()
    c.command(f"CREATE DATABASE IF NOT EXISTS {config.CLICKHOUSE_DB}")
    c.command(DDL)
    c.command(DDL_STATE)


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:  # noqa: BLE001
        reset_client()
        return False


def insert_spans(rows: Iterable[SpanRow]) -> int:
    data = [r.as_tuple() for r in rows]
    if not data:
        return 0
    client().insert(f"{config.CLICKHOUSE_DB}.spans", data, column_names=COLUMNS)
    return len(data)


def get_state(key: str) -> Optional[str]:
    res = client().query(
        f"SELECT value FROM {config.CLICKHOUSE_DB}.ingest_state FINAL WHERE key = %(k)s LIMIT 1",
        parameters={"k": key},
    )
    return res.result_rows[0][0] if res.result_rows else None


def set_state(key: str, value: str) -> None:
    client().insert(f"{config.CLICKHOUSE_DB}.ingest_state", [[key, value]], column_names=["key", "value"])


# --- reads --------------------------------------------------------------------

T = f"{config.CLICKHOUSE_DB}.spans FINAL"


def _since_clause(since_s: Optional[int], project: str) -> tuple[str, dict[str, Any]]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if since_s and since_s > 0:
        params["since"] = datetime.now(timezone.utc) - timedelta(seconds=since_s)
        where.append("start_time >= %(since)s")
    if project:
        params["project"] = project
        where.append("project = %(project)s")
    return " AND ".join(where), params


def rows(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    res = client().query(sql, parameters=params or {})
    cols = res.column_names
    out = []
    for r in res.result_rows:
        d = {}
        for k, v in zip(cols, r):
            if isinstance(v, datetime):
                v = v.replace(tzinfo=timezone.utc).isoformat() if v.tzinfo is None else v.isoformat()
            d[k] = v
        out.append(d)
    return out


def one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = rows(sql, params)
    return r[0] if r else {}


def overview(since_s: Optional[int], project: str, bucket_s: int) -> dict[str, Any]:
    where, params = _since_clause(since_s, project)
    params["bucket"] = max(1, int(bucket_s))

    kpis = one(f"""
        SELECT
          uniqExact(trace_id)                                        AS traces,
          count()                                                    AS spans,
          countIf(status = 'ERROR')                                  AS error_spans,
          uniqExactIf(trace_id, status = 'ERROR')                    AS error_traces,
          countIf(kind = 'LLM')                                      AS llm_calls,
          sum(tokens_prompt)                                         AS tokens_prompt,
          sum(tokens_completion)                                     AS tokens_completion,
          sum(tokens_total)                                          AS tokens_total,
          countIf(kind = 'TOOL' OR tool_name != '')                  AS tool_calls,
          uniqExact(service)                                         AS services,
          uniqExactIf(llm_model, llm_model != '')                    AS models
        FROM {T} WHERE {where}
    """, params)

    roots = one(f"""
        SELECT
          quantileExact(0.5)(duration_ms)  AS p50_ms,
          quantileExact(0.95)(duration_ms) AS p95_ms,
          max(duration_ms)                 AS max_ms,
          avg(duration_ms)                 AS avg_ms
        FROM {T} WHERE {where} AND parent_span_id = ''
    """, params)

    by_service = rows(f"""
        SELECT service, count() AS spans, uniqExact(trace_id) AS traces,
               countIf(status='ERROR') AS errors,
               quantileExact(0.5)(duration_ms) AS p50_ms, quantileExact(0.95)(duration_ms) AS p95_ms
        FROM {T} WHERE {where} GROUP BY service ORDER BY spans DESC
    """, params)
    by_kind = rows(f"""
        SELECT if(kind = '', 'SPAN', kind) AS kind, count() AS spans, countIf(status='ERROR') AS errors,
               quantileExact(0.5)(duration_ms) AS p50_ms
        FROM {T} WHERE {where} GROUP BY kind ORDER BY spans DESC
    """, params)
    by_model = rows(f"""
        SELECT llm_model AS model, llm_provider AS provider, count() AS calls,
               sum(tokens_prompt) AS tokens_prompt, sum(tokens_completion) AS tokens_completion,
               sum(tokens_total) AS tokens_total,
               quantileExact(0.5)(duration_ms) AS p50_ms, quantileExact(0.95)(duration_ms) AS p95_ms,
               countIf(status='ERROR') AS errors
        FROM {T} WHERE {where} AND kind = 'LLM' GROUP BY model, provider ORDER BY calls DESC
    """, params)
    by_tool = rows(f"""
        SELECT if(tool_name = '', name, tool_name) AS tool, count() AS calls,
               countIf(status='ERROR') AS errors, quantileExact(0.5)(duration_ms) AS p50_ms
        FROM {T} WHERE {where} AND (kind = 'TOOL' OR tool_name != '') GROUP BY tool ORDER BY calls DESC
    """, params)
    series = rows(f"""
        SELECT toStartOfInterval(start_time, INTERVAL %(bucket)s SECOND) AS bucket,
               uniqExactIf(trace_id, parent_span_id = '') AS traces,
               count() AS spans,
               countIf(status='ERROR') AS errors,
               countIf(kind='LLM') AS llm_calls,
               sum(tokens_total) AS tokens,
               countIf(kind='TOOL') AS tool_calls,
               quantileExactIf(0.95)(duration_ms, parent_span_id = '') AS p95_ms
        FROM {T} WHERE {where} GROUP BY bucket ORDER BY bucket
    """, params)

    in_p, out_p = 0.0, 0.0
    cost = 0.0
    for m in by_model:
        ip, op = config.price_for(m["model"])
        m["cost_usd"] = (m["tokens_prompt"] * ip + m["tokens_completion"] * op) / 1_000_000
        cost += m["cost_usd"]
        in_p, out_p = ip, op
    kpis.update(roots)
    kpis["cost_usd"] = cost
    kpis["error_rate"] = (kpis["error_traces"] / kpis["traces"]) if kpis.get("traces") else 0.0
    return {
        "kpis": kpis,
        "by_service": by_service,
        "by_kind": by_kind,
        "by_model": by_model,
        "by_tool": by_tool,
        "series": series,
        "bucket_seconds": params["bucket"],
    }


def list_traces(since_s: Optional[int], project: str, *, service: str = "", kind: str = "",
                status: str = "", scenario: str = "", q: str = "",
                limit: int = 50, offset: int = 0) -> dict[str, Any]:
    where, params = _since_clause(since_s, project)
    # Trace-level filters are "any span in the trace matches".
    having = []
    if service:
        params["service"] = service
        having.append("has(services, %(service)s)")
    if kind:
        params["kind"] = kind
        having.append("has(kinds, %(kind)s)")
    if status == "error":
        having.append("errors > 0")
    elif status == "ok":
        having.append("errors = 0")
    if scenario:
        params["scenario"] = scenario
        having.append("scenario_id = %(scenario)s")
    if q:
        params["q"] = f"%{q.lower()}%"
        having.append("(lower(root_name) LIKE %(q)s OR lower(input_preview) LIKE %(q)s OR "
                      "lower(output_preview) LIKE %(q)s OR lower(trace_id) LIKE %(q)s OR "
                      "lower(arrayStringConcat(tools, ' ')) LIKE %(q)s OR "
                      "lower(arrayStringConcat(services, ' ')) LIKE %(q)s)")
    having_sql = ("HAVING " + " AND ".join(having)) if having else ""
    params["limit"] = max(1, min(int(limit), 500))
    params["offset"] = max(0, int(offset))

    base = f"""
        SELECT trace_id,
               min(start_time) AS t_start,
               max(end_time)   AS t_end,
               dateDiff('millisecond', min(start_time), max(end_time)) AS duration_ms,
               count() AS span_count,
               countIf(status='ERROR') AS errors,
               countIf(kind='LLM') AS llm_calls,
               sum(tokens_total) AS tokens_total,
               argMinIf(name, start_time, parent_span_id = '') AS root_name,
               argMinIf(service, start_time, parent_span_id = '') AS root_service,
               argMinIf(kind, start_time, parent_span_id = '') AS root_kind,
               argMinIf(status, start_time, parent_span_id = '') AS root_status,
               argMinIf(substring(input_value, 1, 240), start_time, parent_span_id = '') AS input_preview,
               argMinIf(substring(output_value, 1, 240), start_time, parent_span_id = '') AS output_preview,
               arrayDistinct(groupArray(service)) AS services,
               arrayDistinct(groupArrayIf(kind, kind != '')) AS kinds,
               arrayDistinct(groupArrayIf(tool_name, tool_name != '')) AS tools,
               countIf(kind='TOOL' OR tool_name != '') AS tool_calls,
               arrayDistinct(groupArrayIf(llm_model, llm_model != '')) AS models,
               anyIf(scenario_id, scenario_id != '') AS scenario_id,
               any(project) AS project
        FROM {T} WHERE {where}
        GROUP BY trace_id {having_sql}
    """
    total = one(f"SELECT count() AS n FROM ({base})", params).get("n", 0)
    data = rows(f"SELECT * EXCEPT (t_start, t_end), t_start AS start_time, t_end AS end_time "
                f"FROM ({base}) ORDER BY t_start DESC LIMIT %(limit)s OFFSET %(offset)s", params)
    return {"traces": data, "total": total, "limit": params["limit"], "offset": params["offset"]}


def trace_detail(trace_id: str) -> list[dict[str, Any]]:
    return rows(f"""
        SELECT trace_id, span_id, parent_span_id, name, kind, otel_kind, service, project, source,
               start_time, end_time, duration_ms, status, status_message, attributes, events,
               input_value, output_value, llm_model, llm_provider, tokens_prompt, tokens_completion,
               tokens_total, scenario_id, tool_name
        FROM {T} WHERE trace_id = %(t)s ORDER BY start_time, span_id
    """, {"t": trace_id})


def llm_view(since_s: Optional[int], project: str, limit: int = 50) -> dict[str, Any]:
    where, params = _since_clause(since_s, project)
    params["limit"] = max(1, min(int(limit), 500))
    by_model = rows(f"""
        SELECT llm_model AS model, llm_provider AS provider, count() AS calls,
               sum(tokens_prompt) AS tokens_prompt, sum(tokens_completion) AS tokens_completion,
               sum(tokens_total) AS tokens_total,
               quantileExact(0.5)(duration_ms) AS p50_ms, quantileExact(0.95)(duration_ms) AS p95_ms,
               countIf(status='ERROR') AS errors,
               countIf(attributes['llm.output_messages.0.message.tool_calls.0.tool_call.function.name'] != '') AS tool_call_turns
        FROM {T} WHERE {where} AND kind = 'LLM' GROUP BY model, provider ORDER BY calls DESC
    """, params)
    for m in by_model:
        ip, op = config.price_for(m["model"])
        m["cost_usd"] = (m["tokens_prompt"] * ip + m["tokens_completion"] * op) / 1_000_000
    by_service = rows(f"""
        SELECT service, count() AS calls, sum(tokens_total) AS tokens_total,
               quantileExact(0.5)(duration_ms) AS p50_ms
        FROM {T} WHERE {where} AND kind = 'LLM' GROUP BY service ORDER BY calls DESC
    """, params)
    tools_requested = rows(f"""
        SELECT attributes['llm.output_messages.0.message.tool_calls.0.tool_call.function.name'] AS tool,
               count() AS n
        FROM {T} WHERE {where} AND kind = 'LLM' AND tool != '' GROUP BY tool ORDER BY n DESC
    """, params)
    recent = rows(f"""
        SELECT trace_id, span_id, name, service, start_time, duration_ms, status, llm_model AS model,
               tokens_prompt, tokens_completion, tokens_total,
               attributes['llm.finish_reason'] AS finish_reason,
               attributes['llm.output_messages.0.message.tool_calls.0.tool_call.function.name'] AS tool_called,
               -- The OpenAI instrumentor puts the WHOLE response object in
               -- output.value, which is unreadable as a preview. Prefer the
               -- assistant message; fall back to the tool call it asked for.
               substring(
                 if(attributes['llm.output_messages.0.message.content'] != '',
                    attributes['llm.output_messages.0.message.content'],
                    if(tool_called != '',
                       concat('→ ', tool_called, ' ',
                              attributes['llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments']),
                       output_value)),
                 1, 240) AS output_preview
        FROM {T} WHERE {where} AND kind = 'LLM' ORDER BY start_time DESC LIMIT %(limit)s
    """, params)
    return {"by_model": by_model, "by_service": by_service, "tools_requested": tools_requested, "recent": recent}


def scenarios_view(since_s: Optional[int], project: str) -> list[dict[str, Any]]:
    where, params = _since_clause(since_s, project)
    return rows(f"""
        SELECT scenario_id, count() AS runs,
               anyLast(attributes['scenario.capability']) AS capability,
               anyLast(attributes['scenario.role']) AS role,
               quantileExact(0.5)(duration_ms) AS p50_ms,
               max(start_time) AS last_run
        FROM {T} WHERE {where} AND scenario_id != '' AND parent_span_id = ''
        GROUP BY scenario_id ORDER BY scenario_id
    """, params)


def facets(since_s: Optional[int], project: str) -> dict[str, list[str]]:
    where, params = _since_clause(since_s, project)
    def col(c: str, extra: str = "") -> list[str]:
        return [r[c] for r in rows(f"SELECT DISTINCT {c} FROM {T} WHERE {where} {extra} ORDER BY {c}", params) if r[c]]
    return {
        "services": col("service"),
        "kinds": col("kind"),
        "tools": col("tool_name"),
        "scenarios": col("scenario_id"),
        "projects": [r["project"] for r in rows(f"SELECT DISTINCT project FROM {T} ORDER BY project") if r["project"]],
        "models": col("llm_model"),
    }


def totals() -> dict[str, Any]:
    return one(f"""
        SELECT count() AS spans, uniqExact(trace_id) AS traces,
               countIf(source='phoenix') AS from_phoenix, countIf(source='otlp') AS from_otlp,
               min(start_time) AS oldest, max(start_time) AS newest
        FROM {T}
    """)


def truncate() -> None:
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.spans")
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.ingest_state")
