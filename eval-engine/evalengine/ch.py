"""ClickHouse client: reads the shared `spans` table (read-only - the engine
never mutates a span, per design.md's corollary), writes the engine's own
`eval_verdicts` / `eval_conformance_tags` tables in the same database.

A separate module from oob-ingest's ch.py on purpose: different Docker build
context (own Dockerfile, no shared code - same convention as every other
Prefront service pair in this repo), and this service only ever reads spans,
never writes them.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import clickhouse_connect

from . import config
from .contract import family_label

_client = None
_lock = threading.Lock()

DDL_VERDICTS = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.eval_verdicts
(
    session_id                  String,
    check_id                    LowCardinality(String),
    family                      LowCardinality(String),
    rule_id                     String,
    status                      LowCardinality(String),
    effect                      LowCardinality(String),
    indeterminate_reason        LowCardinality(String),
    detail                      String,
    evidence_span_ids           Array(String),
    evidence_excerpt            String,
    source                      String,
    mode                        LowCardinality(String),
    engine_version               LowCardinality(String),
    binding_profile_version      LowCardinality(String),
    visibility_profile_version   LowCardinality(String),
    rule_pack_version            LowCardinality(String),
    catalog_version               LowCardinality(String),
    evaluated_at                 DateTime64(3, 'UTC'),
    event_id                     String,
    row_version                  DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = ReplacingMergeTree(row_version)
ORDER BY (session_id, check_id, rule_id, evidence_excerpt)
"""

# Columns added after the table first shipped. A volume created by an older
# build lacks them; ADD COLUMN IF NOT EXISTS is metadata-only on MergeTree
# (instant, old rows read as '' for a String default), so it is safe to run
# on every start - same self-healing convention as oob-ingest/ch.py's
# _ADDED_COLUMNS. NOT part of ORDER BY (see contract.py's Finding.event_id
# docstring): adding a column here never changes the dedup identity.
_ADDED_VERDICT_COLUMNS = (
    ("event_id", "String"),
)

DDL_CONFORMANCE = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.eval_conformance_tags
(
    session_id       String,
    check_id         LowCardinality(String),
    rule_id          String,
    policy_document  String,
    clause_id        String,
    section          String,
    page             UInt32,
    clause_text      String,
    evidence_span_ids Array(String),
    engine_version    LowCardinality(String),
    rule_pack_version LowCardinality(String),
    catalog_version   LowCardinality(String),
    evaluated_at      DateTime64(3, 'UTC'),
    row_version       DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = ReplacingMergeTree(row_version)
ORDER BY (session_id, check_id, rule_id, evidence_span_ids)
"""

# Bookkeeping: which session_ids have been evaluated at which artifact
# versions, so the worker never re-evaluates the same (session, versions)
# pair twice (Hard Rule 12: idempotent replay is a no-op, not a duplicate).
DDL_EVALUATED = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.eval_evaluated_sessions
(
    session_id  String,
    version_key String,
    evaluated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(evaluated_at)
ORDER BY (session_id, version_key)
"""


def client():
    global _client
    with _lock:
        if _client is None:
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
    c.command(DDL_VERDICTS)
    c.command(DDL_CONFORMANCE)
    c.command(DDL_EVALUATED)
    for name, typ in _ADDED_VERDICT_COLUMNS:
        c.command(f"ALTER TABLE {config.CLICKHOUSE_DB}.eval_verdicts ADD COLUMN IF NOT EXISTS {name} {typ}")


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:  # noqa: BLE001
        reset_client()
        return False


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
        # Every verdict/tag read funnels through here, so stamping the display
        # name once covers /eval/findings, /eval/verdicts, /eval/conformance and
        # the per-session reads without each one remembering to do it. Derived
        # at READ time, never stored - see contract.FAMILY_LABELS for why the
        # `family` column itself must never be renamed.
        if "family" in d and "family_label" not in d:
            d["family_label"] = family_label(str(d["family"]))
        out.append(d)
    return out


def one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = rows(sql, params)
    return r[0] if r else {}


# --- reading spans (read-only) --------------------------------------------

SPANS_T = f"{config.CLICKHOUSE_DB}.spans FINAL"

_SPAN_COLS = (
    "trace_id, span_id, parent_span_id, name, kind, service, "
    "toString(start_time) AS start_time, toString(end_time) AS end_time, "
    "status, attributes, input_value, output_value, tool_name, "
    "session_id, user_id, user_role, channel, intent_name"
)


def session_spans(session_id: str) -> list[dict[str, Any]]:
    return rows(f"SELECT {_SPAN_COLS} FROM {SPANS_T} WHERE session_id = %(s)s ORDER BY start_time, span_id",
               {"s": session_id})


def session_shapes(scenario_id: str) -> list[dict[str, Any]]:
    """Population material (Hard Rule 13: aggregates only, never raw payloads
    across sessions): one row per session sharing `scenario_id`, collapsed to
    its action shape - never a tool arg or result value."""
    return rows(
        f"""
        SELECT session_id,
               anyIf(attributes['app.variant'], attributes['app.variant'] != '') AS variant,
               arrayStringConcat(arraySort(groupArrayIf(tool_name, kind = 'TOOL')), ',') AS shape,
               countIf(kind = 'TOOL') AS tool_calls,
               countIf(kind = 'TOOL' AND attributes['app.side_effect'] = 'write') AS writes,
               countIf(status = 'ERROR') AS errors,
               min(start_time) AS t_start
        FROM {SPANS_T}
        WHERE session_id != ''
          -- scenario_id is stamped on the session ROOT span only (a harness
          -- attribute, not per-tool), so filter by session membership - a
          -- direct `WHERE scenario_id = X` keeps only the root and yields an
          -- empty shape for every session (caught live: every population
          -- check read one empty shape and reported "consistent").
          AND session_id IN (SELECT DISTINCT session_id FROM {SPANS_T} WHERE scenario_id = %(sid)s)
        GROUP BY session_id ORDER BY t_start
        """,
        {"sid": scenario_id},
    )


def verdict_history(rule_id: str = "", check_id: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Population material for verdict_trend: prior verdicts for one rule
    (or check), newest first - status + evaluated_at only, never evidence."""
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if rule_id:
        where.append("rule_id = %(rule_id)s")
        params["rule_id"] = rule_id
    if check_id:
        where.append("check_id = %(check_id)s")
        params["check_id"] = check_id
    params["limit"] = max(1, min(int(limit), 2000))
    return rows(
        f"SELECT session_id, status, evaluated_at FROM {VERDICTS_T} WHERE {' AND '.join(where)} "
        f"ORDER BY evaluated_at DESC LIMIT %(limit)s",
        params,
    )


def rule_fire_counts(family: str = "family1") -> dict[str, int]:
    """How many verdicts (any status) each rule_id has produced, for one family.
    A rule declared in the pack but absent from this map has never had matching
    traffic - "never hit". Reads all verdicts (satisfied included), not the
    violated-only findings slice, so the coverage answer is authoritative."""
    where = "rule_id != ''"
    params: dict[str, Any] = {}
    if family:
        where += " AND family = %(f)s"
        params["f"] = family
    result = rows(f"SELECT rule_id, count() AS n FROM {VERDICTS_T} WHERE {where} GROUP BY rule_id", params)
    return {str(r["rule_id"]): int(r["n"]) for r in result}


def candidate_sessions(quiet_seconds: float, limit: int = 200) -> list[dict[str, Any]]:
    """session_ids with at least one span, whose most recent span is older
    than `quiet_seconds` ago - i.e. the session looks closed."""
    return rows(
        f"""
        SELECT session_id, max(end_time) AS last_span_at, count() AS span_count
        FROM {SPANS_T}
        WHERE session_id != ''
        GROUP BY session_id
        HAVING last_span_at <= now() - INTERVAL %(quiet)s SECOND
        ORDER BY last_span_at DESC
        LIMIT %(limit)s
        """,
        {"quiet": max(0, int(quiet_seconds)), "limit": limit},
    )


# --- evaluated-session bookkeeping -----------------------------------------

def is_evaluated(session_id: str, version_key: str) -> bool:
    r = one(
        f"SELECT count() AS n FROM {config.CLICKHOUSE_DB}.eval_evaluated_sessions FINAL "
        f"WHERE session_id = %(s)s AND version_key = %(v)s",
        {"s": session_id, "v": version_key},
    )
    return bool(r.get("n"))


def mark_evaluated(session_id: str, version_key: str) -> None:
    client().insert(
        f"{config.CLICKHOUSE_DB}.eval_evaluated_sessions",
        [[session_id, version_key]],
        column_names=["session_id", "version_key"],
    )


# --- writing verdicts / conformance tags -----------------------------------

# event_id: a process-local monotonic counter, not a ClickHouse-side
# auto-increment (this ClickHouse version predates generateSerialID(), and a
# MergeTree has no native sequence type). Safe against this service's actual
# concurrency profile: eval-engine is a SINGLE uvicorn process (no
# --workers, see the Dockerfile) with two callers that can race each
# other - the background Worker's asyncio task and a manual POST /eval/run,
# both landing in insert_verdicts via anyio's threadpool - so a plain
# threading.Lock is sufficient; there is no second process or pod to
# coordinate with. Seeded lazily from the current max on first use (old rows
# with a uuid4-shaped event_id parse to 0 via toUInt64OrZero, so they never
# collide with or influence the new sequence - it just starts at 1).
_event_seq_lock = threading.Lock()
_event_seq: Optional[int] = None


def _next_event_ids(n: int) -> list[str]:
    global _event_seq
    with _event_seq_lock:
        if _event_seq is None:
            _event_seq = int(one(f"SELECT max(toUInt64OrZero(event_id)) AS m FROM {config.CLICKHOUSE_DB}.eval_verdicts").get("m") or 0)
        out = [str(_event_seq + i) for i in range(1, n + 1)]
        _event_seq += n
        return out


_VERDICT_COLS = [
    "session_id", "check_id", "family", "rule_id", "status", "effect",
    "indeterminate_reason", "detail", "evidence_span_ids", "evidence_excerpt",
    "source", "mode", "engine_version", "binding_profile_version",
    "visibility_profile_version", "rule_pack_version", "catalog_version",
    "evaluated_at", "event_id",
]

_TAG_COLS = [
    "session_id", "check_id", "rule_id", "policy_document", "clause_id",
    "section", "page", "clause_text", "evidence_span_ids", "engine_version",
    "rule_pack_version", "catalog_version", "evaluated_at",
]


def insert_verdicts(findings: Iterable) -> int:
    findings = list(findings)
    if not findings:
        return 0
    event_ids = _next_event_ids(len(findings))
    data = []
    for f, event_id in zip(findings, event_ids):
        v = f.verdict
        data.append([
            v.session_id, v.check_id, v.family, v.rule_id, v.status, v.effect,
            f.indeterminate_reason or "", v.detail, list(v.evidence.span_ids), v.evidence.excerpt,
            json.dumps(v.source) if v.source else "", f.mode,
            f.versions.engine_version, f.versions.binding_profile_version,
            f.versions.visibility_profile_version, f.versions.rule_pack_version,
            f.versions.catalog_version, f.evaluated_at, event_id,
        ])
    if not data:
        return 0
    client().insert(f"{config.CLICKHOUSE_DB}.eval_verdicts", data, column_names=_VERDICT_COLS)
    return len(data)


def insert_conformance_tags(tags: Iterable) -> int:
    data = []
    for t in tags:
        src = t.source or {}
        data.append([
            t.session_id, t.check_id, t.rule_id,
            str(src.get("document", "")), str(src.get("clause_id", "")),
            str(src.get("section", "")), int(src.get("page", 0) or 0), str(src.get("text", "")),
            list(t.evidence.span_ids), t.versions.engine_version, t.versions.rule_pack_version,
            t.versions.catalog_version, t.evaluated_at,
        ])
    if not data:
        return 0
    client().insert(f"{config.CLICKHOUSE_DB}.eval_conformance_tags", data, column_names=_TAG_COLS)
    return len(data)


# --- query API ---------------------------------------------------------------

VERDICTS_T = f"{config.CLICKHOUSE_DB}.eval_verdicts FINAL"
TAGS_T = f"{config.CLICKHOUSE_DB}.eval_conformance_tags FINAL"


def list_verdicts(session_id: str = "", status: str = "", check_id: str = "", family: str = "",
                  limit: int = 100, offset: int = 0) -> dict[str, Any]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if session_id:
        where.append("session_id = %(session_id)s")
        params["session_id"] = session_id
    if status:
        where.append("status = %(status)s")
        params["status"] = status
    if check_id:
        where.append("check_id = %(check_id)s")
        params["check_id"] = check_id
    if family:
        where.append("family = %(family)s")
        params["family"] = family
    where_sql = " AND ".join(where)
    params["limit"] = max(1, min(int(limit), 500))
    params["offset"] = max(0, int(offset))
    total = one(f"SELECT count() AS n FROM {VERDICTS_T} WHERE {where_sql}", params).get("n", 0)
    data = rows(
        f"SELECT * FROM {VERDICTS_T} WHERE {where_sql} ORDER BY evaluated_at DESC LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    return {"verdicts": data, "total": total, "limit": params["limit"], "offset": params["offset"]}


def _first_user_messages(session_ids: list[str]) -> dict[str, str]:
    """The first user turn's text per session_id, read from the shared
    `spans` table (this service's own read-only access, same as
    session_spans above) - what the Findings UI shows as "the user query
    the agent received" alongside each finding. Same `name LIKE 'turn %'` +
    argMinIf(..., start_time, ...) pattern oob-ingest's own `first_input`
    column uses (ch.py there), kept independent rather than shared (separate
    Docker build context, no shared code between the two services - the
    established convention in this repo)."""
    if not session_ids:
        return {}
    data = rows(
        f"""
        SELECT session_id,
               argMinIf(substring(input_value, 1, 240), start_time, name LIKE 'turn %%') AS user_query
        FROM {SPANS_T}
        WHERE session_id IN %(ids)s
        GROUP BY session_id
        """,
        {"ids": session_ids},
    )
    return {r["session_id"]: r["user_query"] for r in data if r.get("user_query")}


def list_findings(check_id: str = "", family: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    result = list_verdicts(status="violated", check_id=check_id, family=family, limit=limit, offset=offset)
    result["findings"] = result.pop("verdicts")
    queries = _first_user_messages(sorted({f["session_id"] for f in result["findings"]}))
    for f in result["findings"]:
        f["user_query"] = queries.get(f["session_id"], "")
    return result


def session_conformance(session_id: str) -> list[dict[str, Any]]:
    return rows(f"SELECT * FROM {TAGS_T} WHERE session_id = %(s)s ORDER BY check_id", {"s": session_id})


def list_conformance(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Cross-session conformance tags, newest first - the POSITIVE evidence
    (a rule was applied and satisfied, with its policy clause cited) as a
    single list, mirroring list_findings' shape/cap for the violated side.
    Before this the only read was per-session (session_conformance), so a
    UI wanting "latest N satisfied rules across the deployment" had to fan
    out one call per session."""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500)), "offset": max(0, int(offset))}
    total = one(f"SELECT count() AS n FROM {TAGS_T}").get("n", 0)
    data = rows(
        f"SELECT * FROM {TAGS_T} ORDER BY evaluated_at DESC LIMIT %(limit)s OFFSET %(offset)s", params,
    )
    return {"conformance_tags": data, "total": total, "limit": params["limit"], "offset": params["offset"]}


def truncate() -> None:
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_verdicts")
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_conformance_tags")
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_evaluated_sessions")


def totals() -> dict[str, Any]:
    return one(
        f"""
        SELECT
          (SELECT count() FROM {VERDICTS_T}) AS verdicts,
          (SELECT count() FROM {VERDICTS_T} WHERE status = 'violated') AS findings,
          (SELECT count() FROM {TAGS_T}) AS conformance_tags,
          (SELECT count() FROM {config.CLICKHOUSE_DB}.eval_evaluated_sessions FINAL) AS sessions_evaluated
        """
    )
