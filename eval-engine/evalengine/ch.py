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
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import clickhouse_connect

from . import checks as checks_mod
from . import config
from .contract import family_label

log = logging.getLogger("evalengine.ch")

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

# Engine-level settings that outlive a restart. One row per key (currently
# only "checks", holding the disabled-check set as JSON) - a table rather
# than a file because this service already owns a database and owns no
# writable volume, and rather than an env var because it is edited from the
# UI at runtime, not fixed at deploy time.
DDL_SETTINGS = f"""
CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DB}.eval_settings
(
    key        String,
    value      String,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (key)
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


_RETENTION_TABLES = ("eval_verdicts", "eval_conformance_tags", "eval_evaluated_sessions")


def ensure_schema() -> None:
    c = client()
    c.command(f"CREATE DATABASE IF NOT EXISTS {config.CLICKHOUSE_DB}")
    c.command(DDL_VERDICTS)
    c.command(DDL_CONFORMANCE)
    c.command(DDL_EVALUATED)
    c.command(DDL_SETTINGS)
    for name, typ in _ADDED_VERDICT_COLUMNS:
        c.command(f"ALTER TABLE {config.CLICKHOUSE_DB}.eval_verdicts ADD COLUMN IF NOT EXISTS {name} {typ}")
    apply_retention(config.RETENTION_DAYS)


def apply_retention(days: int) -> dict[str, str]:
    """Set (days > 0) or clear (days == 0) the TTL on this service's tables.
    Idempotent and never fatal: a TTL failure is logged and the service still
    starts - the compliance report then shows the table without one, which
    is the truthful state. Returns table -> TTL expression after the call."""
    c = client()
    for t in _RETENTION_TABLES:
        full = f"{config.CLICKHOUSE_DB}.{t}"
        try:
            if days > 0:
                c.command(f"ALTER TABLE {full} MODIFY TTL toDateTime(evaluated_at) + toIntervalDay({int(days)})")
            else:
                c.command(f"ALTER TABLE {full} REMOVE TTL")
        except Exception as e:  # noqa: BLE001
            log.warning("retention: could not %s TTL on %s: %s", "set" if days > 0 else "clear", full, e)
    return table_ttls(_RETENTION_TABLES)


_TTL_RE = re.compile(r"\bTTL\s+(.+?)(?:\s+SETTINGS\b|$)", re.IGNORECASE | re.DOTALL)


def table_ttls(names: Iterable[str]) -> dict[str, str]:
    """table -> its TTL expression ("" when none), read from system.tables.
    Read-only; used by the compliance report's `retention` control so the
    claim is what ClickHouse actually enforces, not what config asked for."""
    names = list(names)
    if not names:
        return {}
    data = rows(
        "SELECT name, engine_full FROM system.tables WHERE database = %(db)s AND name IN %(names)s",
        {"db": config.CLICKHOUSE_DB, "names": names},
    )
    out = {n: "" for n in names}
    for r in data:
        m = _TTL_RE.search(str(r.get("engine_full") or ""))
        out[str(r["name"])] = m.group(1).strip() if m else ""
    return out


def spans_ttl() -> str:
    """The shared `spans` table's TTL expression ("" when none) - oob-ingest
    owns that table; this is a read of its state for the report."""
    return table_ttls(("spans",)).get("spans", "")


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


# --- disabled-check filtering ------------------------------------------------

def _disabled_clause(params: dict[str, Any]) -> str:
    """` AND check_id NOT IN (...)` for the deployment's disabled checks, or
    "" when everything is on.

    Disabling a check has to hide the rows it ALREADY wrote, not just stop
    new ones: a check turned off in Settings whose old findings kept showing
    would read as a broken toggle. Hiding rather than deleting is what makes
    the switch reversible - re-enabling brings the history straight back, and
    the version-key change (see checks.CheckSettings.version) re-evaluates
    the sessions that were evaluated while it was off, so the gap fills in
    too.

    Every read path that counts or lists verdicts goes through this. Adding a
    new one means adding this clause to it.
    """
    disabled = sorted(checks_mod.current().disabled)
    if not disabled:
        return ""
    params["disabled_checks"] = disabled
    return " AND check_id NOT IN %(disabled_checks)s"


# --- settings ----------------------------------------------------------------

def read_setting(key: str) -> str:
    r = one(
        f"SELECT value FROM {config.CLICKHOUSE_DB}.eval_settings FINAL WHERE key = %(k)s",
        {"k": key},
    )
    return str(r.get("value") or "")


def write_setting(key: str, value: str) -> None:
    client().insert(
        f"{config.CLICKHOUSE_DB}.eval_settings",
        [[key, value, datetime.now(timezone.utc)]],
        column_names=["key", "value", "updated_at"],
    )


def delete_setting(key: str) -> None:
    client().command(
        f"ALTER TABLE {config.CLICKHOUSE_DB}.eval_settings DELETE WHERE key = %(k)s",
        parameters={"k": key},
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
    where_sql = " AND ".join(where) + _disabled_clause(params)
    return rows(
        f"SELECT session_id, status, evaluated_at FROM {VERDICTS_T} WHERE {where_sql} "
        f"ORDER BY evaluated_at DESC LIMIT %(limit)s",
        params,
    )


def rule_fire_counts(family: str = "family1", since: int = 0) -> dict[str, int]:
    """How many verdicts (any status) each rule_id has produced, for one family.
    A rule declared in the pack but absent from this map has never had matching
    traffic - "never hit". Reads all verdicts (satisfied included), not the
    violated-only findings slice, so the coverage answer is authoritative.
    Optionally windowed to the last `since` seconds by evaluated_at."""
    where = "rule_id != ''"
    params: dict[str, Any] = {}
    if family:
        where += " AND family = %(f)s"
        params["f"] = family
    if since and int(since) > 0:
        where += " AND evaluated_at >= now() - INTERVAL %(since)s SECOND"
        params["since"] = int(since)
    where += _disabled_clause(params)
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
                  limit: int = 100, offset: int = 0, since: int = 0) -> dict[str, Any]:
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
    if since and int(since) > 0:
        where.append("evaluated_at >= now() - INTERVAL %(since)s SECOND")
        params["since"] = int(since)
    where_sql = " AND ".join(where) + _disabled_clause(params)
    params["limit"] = max(1, min(int(limit), 2000))
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


def list_findings(check_id: str = "", family: str = "", limit: int = 100, offset: int = 0, since: int = 0) -> dict[str, Any]:
    result = list_verdicts(status="violated", check_id=check_id, family=family, limit=limit, offset=offset, since=since)
    result["findings"] = result.pop("verdicts")
    queries = _first_user_messages(sorted({f["session_id"] for f in result["findings"]}))
    for f in result["findings"]:
        f["user_query"] = queries.get(f["session_id"], "")
    return result


def list_feed(status: str = "", check_id: str = "", family: str = "", limit: int = 100, offset: int = 0, since: int = 0) -> dict[str, Any]:
    """Cross-session verdicts of EVERY status (or one, when `status` is set),
    newest first, with each session's first user turn joined in - the unified
    Decision Traces feed. Unlike list_findings (violated only) this also returns
    the `satisfied` rows, so a clean session surfaces associated with the
    policy/rule it satisfied (its `source` citation carries the section/clause),
    not only the sessions that had a violation. Same shape/cap as list_findings,
    keyed `verdicts`."""
    result = list_verdicts(status=status, check_id=check_id, family=family, limit=limit, offset=offset, since=since)
    queries = _first_user_messages(sorted({v["session_id"] for v in result["verdicts"]}))
    for v in result["verdicts"]:
        v["user_query"] = queries.get(v["session_id"], "")
    return result


def session_conformance(session_id: str) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"s": session_id}
    return rows(
        f"SELECT * FROM {TAGS_T} WHERE session_id = %(s)s{_disabled_clause(params)} ORDER BY check_id",
        params,
    )


def list_conformance(limit: int = 100, offset: int = 0, since: int = 0) -> dict[str, Any]:
    """Cross-session conformance tags, newest first - the POSITIVE evidence
    (a rule was applied and satisfied, with its policy clause cited) as a
    single list, mirroring list_findings' shape/cap for the violated side.
    Before this the only read was per-session (session_conformance), so a
    UI wanting "latest N satisfied rules across the deployment" had to fan
    out one call per session."""
    where = "1 = 1"
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 2000)), "offset": max(0, int(offset))}
    if since and int(since) > 0:
        where += " AND evaluated_at >= now() - INTERVAL %(since)s SECOND"
        params["since"] = int(since)
    where += _disabled_clause(params)
    total = one(f"SELECT count() AS n FROM {TAGS_T} WHERE {where}", params).get("n", 0)
    data = rows(
        f"SELECT * FROM {TAGS_T} WHERE {where} ORDER BY evaluated_at DESC LIMIT %(limit)s OFFSET %(offset)s", params,
    )
    return {"conformance_tags": data, "total": total, "limit": params["limit"], "offset": params["offset"]}


_REPORT_COLS = ("session_id", "check_id", "family", "rule_id", "status", "effect", "detail",
                "evidence_span_ids", "evidence_excerpt", "source", "event_id", "evaluated_at",
                "rule_pack_version", "catalog_version")


def verdict_rows_for_report(since: int = 0, cap: int = 20000) -> tuple[list[dict[str, Any]], bool]:
    """Every verdict (any status) in the window, newest first, up to `cap`.
    Returns (rows, truncated). The compliance report folds these per control
    in Python because the data-class scoping matches field names inside
    `detail`, which no GROUP BY can do."""
    where = "1 = 1"
    params: dict[str, Any] = {"limit": max(1, int(cap)) + 1}
    if since and int(since) > 0:
        where += " AND evaluated_at >= now() - INTERVAL %(since)s SECOND"
        params["since"] = int(since)
    where += _disabled_clause(params)
    data = rows(
        f"SELECT {', '.join(_REPORT_COLS)} FROM {VERDICTS_T} WHERE {where} "
        f"ORDER BY evaluated_at DESC LIMIT %(limit)s",
        params,
    )
    truncated = len(data) > int(cap)
    return data[: int(cap)], truncated


def truncate() -> None:
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_verdicts")
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_conformance_tags")
    client().command(f"TRUNCATE TABLE {config.CLICKHOUSE_DB}.eval_evaluated_sessions")


def totals(since: int = 0) -> dict[str, Any]:
    """Table totals, optionally windowed to the last `since` seconds by
    evaluated_at (all three tables carry it). since=0 -> all time.

    The three verdict/tag counters exclude disabled checks, so the Overview's
    headline numbers agree with the lists underneath them. `sessions_evaluated`
    deliberately does NOT: a session was evaluated whichever checks were on,
    and that count is the denominator the others are read against.

    It counts DISTINCT session ids, not rows. `eval_evaluated_sessions` is
    keyed `(session_id, version_key)`, so one session re-evaluated under new
    artifact versions holds a row per version - a plain `count()` reported
    "183 sessions evaluated" for 122 sessions the first time a version key
    changed. Latent before (only an ENGINE_VERSION bump or a republished rule
    pack moved the key); routine now that toggling a check does.

    `sessions_clean` / `sessions_with_findings` split that denominator, so a
    surface can report how many sessions came back with nothing wrong instead
    of only ever reporting problems - the read-side counterpart of Hard Rule
    15 (satisfied verdicts are first-class output, never dropped as "no
    finding"). Computed as a NOT IN over the evaluated set rather than
    subtracted by the caller: the two live in different tables, and a client
    subtracting a CAPPED findings fetch from an uncapped session count can
    go negative. The violated subquery carries the same disabled-check filter
    as everything else, so a session whose only findings came from a check
    that is now off counts as clean - which is what "clean" means under the
    current settings."""
    sess_t = f"{config.CLICKHOUSE_DB}.eval_evaluated_sessions FINAL"
    params: dict[str, Any] = {}
    t = ""
    if since and int(since) > 0:
        t = " AND evaluated_at >= now() - INTERVAL %(since)s SECOND"
        params["since"] = int(since)
    d = _disabled_clause(params)
    return one(
        f"""
        SELECT
          (SELECT count() FROM {VERDICTS_T} WHERE 1=1{t}{d}) AS verdicts,
          (SELECT count() FROM {VERDICTS_T} WHERE status = 'violated'{t}{d}) AS findings,
          (SELECT count() FROM {TAGS_T} WHERE 1=1{t}{d}) AS conformance_tags,
          (SELECT uniqExact(session_id) FROM {sess_t} WHERE 1=1{t}) AS sessions_evaluated,
          (SELECT uniqExact(session_id) FROM {VERDICTS_T}
             WHERE status = 'violated'{t}{d}) AS sessions_with_findings,
          (SELECT uniqExact(session_id) FROM {sess_t} WHERE 1=1{t}
             AND session_id NOT IN (
               SELECT session_id FROM {VERDICTS_T} WHERE status = 'violated'{t}{d}
             )) AS sessions_clean
        """,
        params,
    )
