"""One normalized span row, whatever the source.

Both sources (Phoenix REST, OTLP fan-out) are flattened into this shape before
they touch ClickHouse. Columns that the UI filters/aggregates on are lifted out
of the attribute bag (LLM model + token counts, the Prefront decision fields,
the demo scenario id); the full bag is kept as a Map for the span inspector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# Source priority: a ReplacingMergeTree keeps the highest version, so a span
# that arrives from BOTH sources resolves to the OTLP copy (it carries the real
# resource attributes, e.g. service.name, which Phoenix's REST does not expose).
VERSION_PHOENIX = 1
VERSION_OTLP = 2

COLUMNS = [
    "trace_id", "span_id", "parent_span_id", "name", "kind", "otel_kind",
    "service", "project", "source", "start_time", "end_time", "duration_ms",
    "status", "status_message", "attributes", "events", "input_value", "output_value",
    "llm_model", "llm_provider", "tokens_prompt", "tokens_completion", "tokens_total",
    "decision", "intent", "caller_role", "caller_key", "scenario_id", "tool_name",
    "version",
    # Session columns (added for the session-shaped checks): the agent stamps
    # session.id / user.id on every span, the app stamps role/channel/intent.
    "session_id", "user_id", "user_role", "channel", "intent_name",
]

_MAX_TEXT = 64_000


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        s = v
    elif isinstance(v, (bool, int, float)):
        s = str(v)
    else:
        try:
            s = json.dumps(v, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            s = str(v)
    return s if len(s) <= _MAX_TEXT else s[:_MAX_TEXT] + "…[truncated]"


def _i(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def parse_ts(v: Any) -> datetime:
    """ISO-8601 string, epoch nanoseconds (OTLP), or datetime -> aware UTC."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1e9, tz=timezone.utc)
    if isinstance(v, str) and v:
        s = v.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


@dataclass
class SpanRow:
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    kind: str                 # openinference kind (LLM/AGENT/TOOL/CHAIN/…), "" if none
    otel_kind: str            # OTel SpanKind (INTERNAL/SERVER/CLIENT/…)
    service: str
    project: str
    source: str               # "phoenix" | "otlp"
    start_time: datetime
    end_time: datetime
    status: str               # OK | ERROR | UNSET
    status_message: str
    attributes: dict[str, str]
    events: str               # JSON list
    version: int
    input_value: str = ""
    output_value: str = ""
    llm_model: str = ""
    llm_provider: str = ""
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    decision: str = ""
    intent: str = ""
    caller_role: str = ""
    caller_key: str = ""
    scenario_id: str = ""
    tool_name: str = ""
    session_id: str = ""
    user_id: str = ""
    user_role: str = ""
    channel: str = ""
    intent_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_time - self.start_time).total_seconds() * 1000.0)

    def as_tuple(self) -> list[Any]:
        return [
            self.trace_id, self.span_id, self.parent_span_id, self.name, self.kind, self.otel_kind,
            self.service, self.project, self.source, self.start_time, self.end_time, self.duration_ms,
            self.status, self.status_message, self.attributes, self.events, self.input_value,
            self.output_value, self.llm_model, self.llm_provider, self.tokens_prompt,
            self.tokens_completion, self.tokens_total, self.decision, self.intent, self.caller_role,
            self.caller_key, self.scenario_id, self.tool_name, self.version,
            self.session_id, self.user_id, self.user_role, self.channel, self.intent_name,
        ]


def lift(attrs: dict[str, Any]) -> dict[str, Any]:
    """Pull the columns the UI aggregates on out of a flat attribute bag."""
    return dict(
        input_value=_s(attrs.get("input.value")),
        output_value=_s(attrs.get("output.value")),
        llm_model=_s(attrs.get("llm.model_name")),
        llm_provider=_s(attrs.get("llm.provider") or attrs.get("llm.system")),
        tokens_prompt=_i(attrs.get("llm.token_count.prompt")),
        tokens_completion=_i(attrs.get("llm.token_count.completion")),
        tokens_total=_i(attrs.get("llm.token_count.total"))
        or (_i(attrs.get("llm.token_count.prompt")) + _i(attrs.get("llm.token_count.completion"))),
        scenario_id=_s(attrs.get("scenario.id")),
        tool_name=_s(attrs.get("tool.name") or attrs.get("app.tool")),
        session_id=_s(attrs.get("session.id")),
        user_id=_s(attrs.get("user.id")),
        user_role=_s(attrs.get("app.user.role") or attrs.get("user.role")),
        channel=_s(attrs.get("app.channel")),
        intent_name=_s(attrs.get("app.intent")),
    )


def scrub(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop attributes that describe the inline path (config.STRIP_ATTR_PREFIXES)."""
    from . import config

    if not config.STRIP_ATTR_PREFIXES:
        return attrs
    return {k: v for k, v in attrs.items()
            if not any(str(k).startswith(p) for p in config.STRIP_ATTR_PREFIXES)}


def stringify_attrs(attrs: dict[str, Any]) -> dict[str, str]:
    return {str(k): _s(v) for k, v in scrub(attrs).items() if v is not None}


def is_inline(row: "SpanRow") -> bool:
    """True when a span belongs to the inline (Prefront / governed) path."""
    from . import config

    if row.service in config.EXCLUDE_SERVICES:
        return True
    for n in config.EXCLUDE_SPAN_NAMES:
        if row.name.startswith(n):
            return True
    for k in row.attributes:
        for pfx in config.EXCLUDE_ATTR_PREFIXES:
            if k.startswith(pfx):
                return True
    return False


def drop_inline(rows: list["SpanRow"], dropped: set[str]) -> list["SpanRow"]:
    """Remove inline spans and every descendant of one.

    ``dropped`` is the caller's memory of span ids already excluded (so a child
    that arrives in a later batch than its excluded parent is excluded too);
    it is updated in place.
    """
    by_id = {r.span_id: r for r in rows}
    verdict: dict[str, bool] = {}

    def excluded(r: "SpanRow", depth: int = 0) -> bool:
        if r.span_id in verdict:
            return verdict[r.span_id]
        if depth > 64:
            return False
        out = is_inline(r)
        if not out and r.parent_span_id:
            if r.parent_span_id in dropped:
                out = True
            else:
                parent = by_id.get(r.parent_span_id)
                out = excluded(parent, depth + 1) if parent is not None else False
        verdict[r.span_id] = out
        return out

    kept: list["SpanRow"] = []
    for r in rows:
        if excluded(r):
            dropped.add(r.span_id)
        else:
            kept.append(r)
    return kept


def infer_service(name: str, attrs: dict[str, Any]) -> str:
    """Best-effort service label for spans whose source carries no resource.

    Keyed on *engine* vocabulary (the ``prefront.*`` span attributes the runtime
    emits, the ``scenario.*`` ones the demo orchestrators emit), never on a
    domain's tables or roles. Children later inherit their parent's label
    (see ``inherit_services``), so only root/marker spans need to match.
    """
    if "prefront.template_id" in attrs or "prefront.intent" in attrs or name.startswith("govern "):
        return "semantic-mcp-server"
    if any(k.startswith("scenario.") for k in attrs) or name.startswith("scenario "):
        return "orchestrator"
    if name == "governed agent" or "prefront.mcp_url" in attrs:
        return "orchestrator"
    if name == "ungoverned agent" or any(k.startswith("app.") for k in attrs):
        return "ungoverned-agent"
    return ""


def inherit_services(rows: Iterable[SpanRow], known: Optional[dict[str, tuple[str, str]]] = None) -> None:
    """Fill blank ``service`` from the nearest labelled ancestor.

    ``known`` maps already-stored span ids -> (parent_span_id, service) so a
    child arriving after its parent can still inherit.
    """
    by_id = {r.span_id: r for r in rows}
    known = known or {}

    def resolve_known(span_id: str, depth: int) -> str:
        if depth > 64:
            return ""
        entry = known.get(span_id)
        if entry is None:
            return ""
        parent_id, service = entry
        if service and service != "unknown":
            return service
        return resolve_known(parent_id, depth + 1)

    def resolve(r: SpanRow, depth: int = 0) -> str:
        if r.service or depth > 64:
            return r.service
        parent = by_id.get(r.parent_span_id)
        if parent is None:
            r.service = resolve_known(r.parent_span_id, depth + 1) if r.parent_span_id else ""
            return r.service
        r.service = resolve(parent, depth + 1)
        return r.service

    for r in list(by_id.values()):
        resolve(r)
    for r in by_id.values():
        if not r.service:
            r.service = "unknown"


def from_phoenix(span: dict[str, Any], project: str) -> Optional[SpanRow]:
    ctx = span.get("context") or {}
    trace_id = _s(ctx.get("trace_id"))
    span_id = _s(ctx.get("span_id"))
    if not trace_id or not span_id:
        return None
    attrs = dict(span.get("attributes") or {})
    name = _s(span.get("name"))
    status = _s(span.get("status_code") or "UNSET").upper()
    return SpanRow(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=_s(span.get("parent_id")),
        name=name,
        kind=_s(span.get("span_kind")).upper(),
        otel_kind="",
        service=infer_service(name, attrs),
        project=project,
        source="phoenix",
        start_time=parse_ts(span.get("start_time")),
        end_time=parse_ts(span.get("end_time") or span.get("start_time")),
        status=status,
        status_message=_s(span.get("status_message")),
        attributes=stringify_attrs(attrs),
        events=_s(span.get("events") or []),
        version=VERSION_PHOENIX,
        **lift(attrs),
    )
