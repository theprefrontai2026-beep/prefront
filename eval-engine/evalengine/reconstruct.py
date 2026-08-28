"""spans (for one session_id) -> canonical Session.

Pure function: no I/O (the caller already fetched the rows), no clock (all
ordering comes from the spans' own start_time), no randomness.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .binding import BindingProfile
from .contract import Session, Step, Turn


def _parse(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _text(value: Any) -> str:
    """Best-effort plain text out of a parsed payload that may be a dict/list."""
    parsed = _parse(value)
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        for key in ("content", "answer", "message", "text"):
            v = parsed.get(key)
            if isinstance(v, str):
                return v
    return "" if parsed is None else str(parsed)


def _first_nonempty(rows: list[dict[str, Any]], col: str) -> str:
    for r in rows:
        v = r.get(col)
        if v:
            return str(v)
    return ""


def _first_nonempty_field(rows: list[dict[str, Any]], binding: BindingProfile, field: str) -> str:
    for r in rows:
        v = binding.field(field, r)
        if v:
            return str(v)
    return ""


def _nearest_ancestor_turn(row: dict[str, Any], by_id: dict[str, dict[str, Any]],
                          binding: BindingProfile, turn_index: dict[str, int]) -> Optional[int]:
    seen: set[str] = set()
    pid = row.get("parent_span_id") or ""
    depth = 0
    while pid and pid not in seen and depth < 64:
        seen.add(pid)
        parent = by_id.get(pid)
        if parent is None:
            return None
        if binding.turn_span.matches(parent):
            return turn_index.get(parent["span_id"])
        pid = parent.get("parent_span_id") or ""
        depth += 1
    return None


def reconstruct(session_id: str, rows: list[dict[str, Any]], binding: BindingProfile) -> Session:
    ordered = sorted(rows, key=lambda r: (r.get("start_time") or "", r.get("span_id") or ""))
    by_id = {r["span_id"]: r for r in ordered if r.get("span_id")}

    turn_rows = [r for r in ordered if binding.turn_span.matches(r)]
    turn_index = {r["span_id"]: i for i, r in enumerate(turn_rows)}
    turns = tuple(
        Turn(
            span_id=r["span_id"],
            seq=i,
            start_time=str(r.get("start_time") or ""),
            end_time=str(r.get("end_time") or ""),
            user_message=_text(r.get("input_value")),
            assistant_message=_text(r.get("output_value")),
        )
        for i, r in enumerate(turn_rows)
    )

    tool_rows = [r for r in ordered if binding.tool_span.matches(r)]
    steps = []
    for i, r in enumerate(tool_rows):
        args = _parse(r.get("input_value"))
        if not isinstance(args, dict):
            args = {} if args in (None, "") else {"_value": args}
        row_count = binding.field("row_count", r)
        try:
            row_count = int(row_count) if row_count not in (None, "") else None
        except (TypeError, ValueError):
            row_count = None
        columns_raw = binding.field("columns", r)
        if isinstance(columns_raw, str):
            columns_parsed = _parse(columns_raw)
            columns = tuple(columns_parsed) if isinstance(columns_parsed, list) else tuple(
                c.strip() for c in columns_raw.split(",") if c.strip()
            )
        elif isinstance(columns_raw, list):
            columns = tuple(columns_raw)
        else:
            columns = ()
        steps.append(
            Step(
                span_id=r["span_id"],
                trace_id=str(r.get("trace_id") or ""),
                seq=i,
                start_time=str(r.get("start_time") or ""),
                end_time=str(r.get("end_time") or ""),
                tool_name=str(r.get("tool_name") or (r.get("name") or "").removeprefix(binding.tool_span.name_prefix)),
                intent=str(binding.field("intent", r) or ""),
                args=args,
                result=_parse(r.get("output_value")),
                status=str(r.get("status") or "UNSET"),
                row_count=row_count,
                columns=columns,
                side_effect=str(binding.field("side_effect", r) or ""),
                trust_class=str(binding.field("trust_class", r) or ""),
                turn_seq=_nearest_ancestor_turn(r, by_id, binding, turn_index),
            )
        )

    final_answer = turns[-1].assistant_message if turns else ""

    return Session(
        session_id=session_id,
        trace_ids=tuple(sorted({str(r.get("trace_id") or "") for r in ordered if r.get("trace_id")})),
        user_id=_first_nonempty(ordered, "user_id"),
        caller_role=_first_nonempty_field(ordered, binding, "caller_role") or _first_nonempty(ordered, "user_role"),
        channel=_first_nonempty_field(ordered, binding, "channel") or _first_nonempty(ordered, "channel"),
        turns=turns,
        steps=tuple(steps),
        final_answer=final_answer,
        raw_span_count=len(ordered),
    )
