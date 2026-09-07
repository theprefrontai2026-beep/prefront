"""Per-tool behavioural profiles, mined from the spans table by counting.

One `ToolProfile` per observed tool: who called it, through which channel, with
which arguments, what came back, how much of it, and what tended to follow. See
the package docstring for the two constraints these functions are written
under; the important one in the code below is that `app.intent` is never read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .. import ch, config

# A tool CALL, which is narrower than "a span mentioning a tool".
#
# `kind='TOOL'` is the OpenInference span kind and is the real thing. The
# `tool_name != ''` fallback exists for an exporter that lifts the name without
# setting a kind — but taken alone it also matches an AGENT turn span, which
# carries the tool name of a call made INSIDE it. On the bundled corpus that is
# 1011 turn spans against 1644 real calls: a 62% over-count, and worse for
# sequence mining, where each turn injects a phantom step that made every mined
# workflow start and end with the same tool.
#
# So the fallback is admitted only when the exporter set no kind at all.
# oob-ingest uses the looser predicate deliberately (there, the agent's own span
# IS the unit it counts); this is a different question about the same table.
_TOOL_PRED = ("(kind = 'TOOL' OR (tool_name != '' AND kind = '')) "
              "AND tool_name != ''")


@dataclass(frozen=True)
class Counted:
    """A distinct observed value and how often it was seen.

    Counts travel WITH the value everywhere in this module rather than being
    summarised away, because the reviewer's question is never "which roles
    called this" but "which roles called this, how often, and is the long tail
    one accident or a real pattern". A bare set cannot answer that, and it is
    precisely the distinction between a legitimate caller and a leak.
    """
    value: str
    sessions: int
    calls: int


@dataclass(frozen=True)
class ToolProfile:
    tool_name: str
    calls: int
    sessions: int
    first_seen: str
    last_seen: str
    # Deterministic, directly observed (design §3's "yes" rows).
    side_effects: tuple[Counted, ...] = ()
    roles: tuple[Counted, ...] = ()
    channels: tuple[Counted, ...] = ()
    params: tuple[Counted, ...] = ()
    fields: tuple[Counted, ...] = ()
    row_count_p50: Optional[int] = None
    row_count_p99: Optional[int] = None
    row_count_max: Optional[int] = None
    error_calls: int = 0
    # Hypotheses — counted, but they mean "always so far", not "must".
    caller_invariants: tuple[dict[str, Any], ...] = ()
    followed_by: tuple[dict[str, Any], ...] = ()
    # Defence #1: the Family 2 verdicts on the sessions supporting this profile.
    contested: tuple[dict[str, Any], ...] = ()
    example_sessions: tuple[str, ...] = ()

    @property
    def is_contested(self) -> bool:
        return bool(self.contested)


def _counted(rows_: list[dict[str, Any]], key: str = "value") -> tuple[Counted, ...]:
    return tuple(
        Counted(value=str(r[key]), sessions=int(r["sessions"]), calls=int(r["calls"]))
        for r in rows_ if str(r.get(key) or "") != ""
    )


def _window(since: int, app: str) -> tuple[str, dict[str, Any]]:
    """The WHERE tail every query here shares: tool spans, in window, one app."""
    params: dict[str, Any] = {"since": int(since)}
    where = (f"WHERE {_TOOL_PRED} AND start_time >= now() - INTERVAL %(since)s SECOND")
    project = _project_for(app)
    if project:
        params["proj"] = project
        where += " AND project = %(proj)s"
    return where, params


def _project_for(app: str) -> str:
    """The Phoenix project that carries this application's spans.

    `spans` predates app_id and is partitioned by Phoenix project, so an
    application is selected here by project — the inverse of ch.session_app's
    mapping. An application with no configured project maps to itself, which is
    true for every bundled deployment.
    """
    if not app:
        return ""
    for proj, mapped in config.PROJECT_APP_MAP.items():
        if mapped == app:
            return proj
    return app


def tool_profiles(since: int = 7 * 86400, app: str = "", min_sessions: int = 1) -> list[ToolProfile]:
    """One profile per tool observed in the window.

    `min_sessions` is the credibility floor (design §7 open question 1): a tool
    seen in one session is an anecdote, not a pattern. Deliberately defaulted to
    1 — surfacing everything and letting the caller raise the bar — because a
    default that silently hides rare tools would hide exactly the ones worth
    looking at on a small corpus.
    """
    where, params = _window(since, app)

    base = ch.rows(f"""
        SELECT tool_name,
               count() AS calls,
               uniqExact(session_id) AS sessions,
               toString(min(start_time)) AS first_seen,
               toString(max(start_time)) AS last_seen,
               countIf(status = 'ERROR') AS error_calls,
               groupUniqArray(10)(session_id) AS examples
        FROM {ch.SPANS_T} {where}
        GROUP BY tool_name
        HAVING sessions >= %(minsess)s
        ORDER BY calls DESC
    """, {**params, "minsess": int(min_sessions)})

    # Row-count distribution, skipping calls that returned nothing measurable:
    # a tool with no app.row_count attribute has no distribution, and folding
    # its absence in as 0 would understate p99 and make a volume check fire on
    # normal traffic.
    vol = {r["tool_name"]: r for r in ch.rows(f"""
        SELECT tool_name,
               toUInt32(quantileExact(0.50)(toUInt32OrZero(attributes['app.row_count']))) AS p50,
               toUInt32(quantileExact(0.99)(toUInt32OrZero(attributes['app.row_count']))) AS p99,
               toUInt32(max(toUInt32OrZero(attributes['app.row_count']))) AS mx
        FROM {ch.SPANS_T} {where} AND has(mapKeys(attributes), 'app.row_count')
        GROUP BY tool_name
    """, params)}

    by_attr = {
        "side_effects": _attr_counts(where, params, "app.side_effect"),
        "roles": _attr_counts(where, params, "app.user.role"),
        "channels": _attr_counts(where, params, "app.channel"),
    }
    params_seen = _json_key_counts(where, params, "input_value")
    fields_seen = _list_attr_counts(where, params, "app.columns")
    invariants = caller_invariants(since, app)
    sequences = following_tools(since, app)
    contested = _contested_by_tool(since, app)

    out: list[ToolProfile] = []
    for r in base:
        t = str(r["tool_name"])
        v = vol.get(t) or {}
        out.append(ToolProfile(
            tool_name=t,
            calls=int(r["calls"]), sessions=int(r["sessions"]),
            first_seen=str(r["first_seen"]), last_seen=str(r["last_seen"]),
            error_calls=int(r["error_calls"]),
            side_effects=by_attr["side_effects"].get(t, ()),
            roles=by_attr["roles"].get(t, ()),
            channels=by_attr["channels"].get(t, ()),
            params=params_seen.get(t, ()),
            fields=fields_seen.get(t, ()),
            row_count_p50=_int_or_none(v.get("p50")),
            row_count_p99=_int_or_none(v.get("p99")),
            row_count_max=_int_or_none(v.get("mx")),
            caller_invariants=tuple(invariants.get(t, ())),
            followed_by=tuple(sequences.get(t, ())),
            contested=tuple(contested.get(t, ())),
            example_sessions=tuple(str(s) for s in (r.get("examples") or []))[:10],
        ))
    return out


def _int_or_none(v: Any) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _attr_counts(where: str, params: dict[str, Any], attr: str) -> dict[str, tuple[Counted, ...]]:
    """Distinct values of one span attribute, per tool, with support."""
    rows_ = ch.rows(f"""
        SELECT tool_name, attributes[%(attr)s] AS value,
               uniqExact(session_id) AS sessions, count() AS calls
        FROM {ch.SPANS_T} {where} AND attributes[%(attr)s] != ''
        GROUP BY tool_name, value ORDER BY calls DESC
    """, {**params, "attr": attr})
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows_:
        out.setdefault(str(r["tool_name"]), []).append(r)
    return {k: _counted(v) for k, v in out.items()}


def _list_attr_counts(where: str, params: dict[str, Any], attr: str) -> dict[str, tuple[Counted, ...]]:
    """Same, for an attribute holding a comma-or-JSON list (e.g. result columns).

    Split client-side rather than in SQL: deployments serialise a column list as
    a JSON array, a comma string or a Python repr, and one tolerant split here
    beats three ClickHouse expressions that each work for one exporter.
    """
    rows_ = ch.rows(f"""
        SELECT tool_name, attributes[%(attr)s] AS raw, session_id
        FROM {ch.SPANS_T} {where} AND attributes[%(attr)s] != ''
    """, {**params, "attr": attr})
    agg: dict[str, dict[str, dict[str, set | int]]] = {}
    for r in rows_:
        tool, sid = str(r["tool_name"]), str(r["session_id"])
        for name in _split_list(str(r["raw"])):
            slot = agg.setdefault(tool, {}).setdefault(name, {"s": set(), "c": 0})
            slot["s"].add(sid)          # type: ignore[union-attr]
            slot["c"] = int(slot["c"]) + 1
    return {
        tool: tuple(sorted(
            (Counted(value=n, sessions=len(d["s"]), calls=int(d["c"])) for n, d in names.items()),
            key=lambda c: (-c.calls, c.value)))
        for tool, names in agg.items()
    }


def _split_list(raw: str) -> list[str]:
    import json
    raw = raw.strip()
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(raw) if str(x).strip()]
        except Exception:  # noqa: BLE001 - a Python repr ("['a', 'b']") is not JSON
            raw = raw.strip("[]")
    return [p.strip().strip("'\"") for p in raw.split(",") if p.strip().strip("'\"")]


def _json_key_counts(where: str, params: dict[str, Any], col: str) -> dict[str, tuple[Counted, ...]]:
    """Argument NAMES per tool, from the recorded call input.

    Keys only, never values: a param name is schema and safe to aggregate,
    whereas a value is the customer's data and has no business in a profile
    that a reviewer reads and an LLM later summarises.
    """
    import json
    rows_ = ch.rows(f"SELECT tool_name, {col} AS raw, session_id FROM {ch.SPANS_T} {where} AND {col} != ''",
                    params)
    agg: dict[str, dict[str, dict[str, set | int]]] = {}
    for r in rows_:
        try:
            obj = json.loads(str(r["raw"]))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        tool, sid = str(r["tool_name"]), str(r["session_id"])
        for k in obj:
            slot = agg.setdefault(tool, {}).setdefault(str(k), {"s": set(), "c": 0})
            slot["s"].add(sid)          # type: ignore[union-attr]
            slot["c"] = int(slot["c"]) + 1
    return {
        tool: tuple(sorted(
            (Counted(value=n, sessions=len(d["s"]), calls=int(d["c"])) for n, d in ks.items()),
            key=lambda c: (-c.calls, c.value)))
        for tool, ks in agg.items()
    }


def caller_invariants(since: int = 7 * 86400, app: str = "",
                      min_calls: int = 3) -> dict[str, list[dict[str, Any]]]:
    """Arguments that ALWAYS equalled a caller attribute — candidate scoping.

    This is the mandatory-filter hypothesis, and there is a convergence worth
    knowing about: Family 3's `filter_scope` only recognises the exact shape
    `<field> = caller`, which is precisely the shape this test produces. So a
    hypothesis confirmed here is directly enforceable, with no translation.

    Reported as `holds`/`observed` rather than a boolean. "Held on 40 of 40
    calls" and "held on 3 of 3" are both 100%, and only the reviewer can say
    whether the second is a rule or a coincidence — collapsing them to True
    would destroy the only information that distinguishes them. `min_calls`
    keeps the very smallest coincidences out entirely.
    """
    import json
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT tool_name, input_value AS raw, user_id, user_role, session_id
        FROM {ch.SPANS_T} {where} AND input_value != ''
    """, params)

    # (tool, arg, caller-attr) -> [matched, total]
    tally: dict[tuple[str, str, str], list[int]] = {}
    for r in rows_:
        try:
            args = json.loads(str(r["raw"]))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(args, dict):
            continue
        caller = {"caller.user_id": str(r.get("user_id") or ""),
                  "caller.role": str(r.get("user_role") or "")}
        tool = str(r["tool_name"])
        for arg, val in args.items():
            for attr, cval in caller.items():
                if not cval:
                    continue
                slot = tally.setdefault((tool, str(arg), attr), [0, 0])
                slot[1] += 1
                if str(val) == cval:
                    slot[0] += 1

    out: dict[str, list[dict[str, Any]]] = {}
    for (tool, arg, attr), (hit, total) in tally.items():
        if total >= min_calls and hit == total:
            out.setdefault(tool, []).append(
                {"param": arg, "equals": attr, "holds": hit, "observed": total})
    for v in out.values():
        v.sort(key=lambda d: (-d["holds"], d["param"]))
    return out


def following_tools(since: int = 7 * 86400, app: str = "",
                    min_support: int = 3) -> dict[str, list[dict[str, Any]]]:
    """For each tool, what tended to be called next in the same session.

    The closing-obligation hypothesis ("B follows A in X% of sessions"). Ordered
    pairs within a session, adjacent in time — not all-pairs, because
    "eventually happened later in the session" is satisfied by almost anything
    on a long session and would propose obligations out of coincidence.

    `rate` is over SESSIONS, not calls: a tool called five times in one session
    and followed once should not read as 20% of a population.
    """
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT session_id, tool_name, toString(start_time) AS ts, span_id
        FROM {ch.SPANS_T} {where}
        ORDER BY session_id, start_time, span_id
    """, params)

    by_session: dict[str, list[str]] = {}
    for r in rows_:
        by_session.setdefault(str(r["session_id"]), []).append(str(r["tool_name"]))

    seen: dict[str, set[str]] = {}          # tool -> sessions containing it
    pair: dict[tuple[str, str], set[str]] = {}
    for sid, seq in by_session.items():
        for t in set(seq):
            seen.setdefault(t, set()).add(sid)
        for a, b in zip(seq, seq[1:]):
            if a != b:
                pair.setdefault((a, b), set()).add(sid)

    out: dict[str, list[dict[str, Any]]] = {}
    for (a, b), sids in pair.items():
        support = len(sids)
        if support < min_support:
            continue
        total = len(seen.get(a) or ())
        out.setdefault(a, []).append({
            "tool": b, "sessions": support, "of_sessions": total,
            "rate": round(support / total, 3) if total else 0.0,
        })
    for v in out.values():
        v.sort(key=lambda d: (-d["rate"], -d["sessions"], d["tool"]))
    return out


def _contested_by_tool(since: int = 7 * 86400, app: str = "") -> dict[str, list[dict[str, Any]]]:
    """Family 2 integrity violations on the sessions supporting each tool.

    Defence #1 from the design, and the reason this whole approach is not
    circular: Family 2 needs NO policy, so it is the one honest signal available
    on a deployment that has not been onboarded. A profile whose supporting
    sessions carry param_taint or entity_consistency violations is contested
    observed practice, and must never be presented as a clean pattern to bless.

    Scoped to family2 deliberately. Family 1 and Family 3 verdicts depend on the
    very artifacts a mining deployment does not have; counting them would make
    the overlay silently empty exactly where it is needed most.
    """
    where, params = _window(since, app)
    sess = ch.rows(f"SELECT DISTINCT tool_name, session_id FROM {ch.SPANS_T} {where}", params)
    if not sess:
        return {}
    tools_by_session: dict[str, set[str]] = {}
    for r in sess:
        tools_by_session.setdefault(str(r["session_id"]), set()).add(str(r["tool_name"]))

    vparams: dict[str, Any] = {"sids": list(tools_by_session)}
    vwhere = "WHERE family = 'family2' AND status = 'violated' AND session_id IN %(sids)s"
    vwhere += ch._app_clause(vparams, app)
    verdicts = ch.rows(f"""
        SELECT session_id, check_id, count() AS n
        FROM {ch.VERDICTS_T} {vwhere}
        GROUP BY session_id, check_id
    """, vparams)

    agg: dict[str, dict[str, dict[str, Any]]] = {}
    for v in verdicts:
        sid, check = str(v["session_id"]), str(v["check_id"])
        for tool in tools_by_session.get(sid, ()):
            slot = agg.setdefault(tool, {}).setdefault(check, {"check_id": check, "sessions": set(), "findings": 0})
            slot["sessions"].add(sid)
            slot["findings"] = int(slot["findings"]) + int(v["n"])
    return {
        tool: sorted(
            ({"check_id": d["check_id"], "sessions": len(d["sessions"]), "findings": d["findings"]}
             for d in checks.values()),
            key=lambda d: (-d["sessions"], d["check_id"]))
        for tool, checks in agg.items()
    }


def observed_intent_labels(since: int = 7 * 86400, app: str = "") -> dict[str, list[dict[str, Any]]]:
    """The `app.intent` a deployment stamped, per tool — THE ANSWER KEY.

    Deliberately not part of a profile and never read by the miner. A
    deployment that already stamps an approved intent name has, by definition,
    an approved intent catalog; mining is for the deployments that do not. Its
    only legitimate use is SCORING — diffing a mined catalog against a
    hand-authored one, which is how a deployment that HAS both finds out
    whether the miner works at all (intent_learning_design.md §6: hold the
    authored catalog out, mine from the same traces, diff per field).

    Kept in this module so the separation is visible at the call site: anything
    reading this is measuring the miner, not running it.
    """
    where, params = _window(since, app)
    rows_ = ch.rows(f"""
        SELECT tool_name, attributes['app.intent'] AS intent,
               uniqExact(session_id) AS sessions, count() AS calls
        FROM {ch.SPANS_T} {where}
        GROUP BY tool_name, intent ORDER BY tool_name, calls DESC
    """, params)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows_:
        out.setdefault(str(r["tool_name"]), []).append({
            "intent": str(r["intent"] or ""), "sessions": int(r["sessions"]), "calls": int(r["calls"])})
    return out
