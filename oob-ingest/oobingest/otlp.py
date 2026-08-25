"""OTLP/HTTP receiver: decode an ExportTraceServiceRequest into SpanRows.

This is the second, out-of-band tap: the tracing module can fan out every span
to Phoenix AND here (``PREFRONT_TRACE_FANOUT``). Unlike the Phoenix pull this
path sees resource attributes, so ``service`` is exact.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.protobuf.json_format import MessageToDict, Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from .model import VERSION_OTLP, SpanRow, lift, parse_ts, stringify_attrs

log = logging.getLogger(__name__)

_OTEL_KINDS = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


def _any_value(v: dict[str, Any]) -> Any:
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "arrayValue" in v:
        return [_any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _any_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _attrs(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {kv["key"]: _any_value(kv.get("value", {})) for kv in items or [] if "key" in kv}


def _hex(b: Any) -> str:
    # MessageToDict base64-encodes bytes; we want the hex id Phoenix uses.
    import base64
    if isinstance(b, str):
        try:
            return base64.b64decode(b).hex()
        except Exception:  # noqa: BLE001
            return b
    return ""


def decode(body: bytes, content_type: str, project: str) -> list[SpanRow]:
    req = ExportTraceServiceRequest()
    if "json" in (content_type or "").lower():
        Parse(body.decode("utf-8"), req, ignore_unknown_fields=True)
    else:
        req.ParseFromString(body)
    d = MessageToDict(req, preserving_proto_field_name=False)

    out: list[SpanRow] = []
    for rs in d.get("resourceSpans", []):
        res_attrs = _attrs((rs.get("resource") or {}).get("attributes", []))
        service = str(res_attrs.get("service.name") or "unknown")
        for ss in rs.get("scopeSpans", []):
            scope = (ss.get("scope") or {}).get("name", "")
            for s in ss.get("spans", []):
                attrs = _attrs(s.get("attributes", []))
                for k, v in res_attrs.items():
                    attrs.setdefault(f"resource.{k}", v)
                if scope:
                    attrs.setdefault("otel.scope.name", scope)
                status = s.get("status") or {}
                code = status.get("code", 0)
                if isinstance(code, str):
                    code = {"STATUS_CODE_UNSET": 0, "STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}.get(code, 0)
                kind = s.get("kind", 0)
                if isinstance(kind, str):
                    kind = {"SPAN_KIND_UNSPECIFIED": 0, "SPAN_KIND_INTERNAL": 1, "SPAN_KIND_SERVER": 2,
                            "SPAN_KIND_CLIENT": 3, "SPAN_KIND_PRODUCER": 4, "SPAN_KIND_CONSUMER": 5}.get(kind, 0)
                events = [
                    {"name": e.get("name"), "time": parse_ts(int(e.get("timeUnixNano", 0))).isoformat(),
                     "attributes": _attrs(e.get("attributes", []))}
                    for e in s.get("events", [])
                ]
                start = parse_ts(int(s.get("startTimeUnixNano", 0)))
                end = parse_ts(int(s.get("endTimeUnixNano", 0))) if s.get("endTimeUnixNano") else start
                out.append(SpanRow(
                    trace_id=_hex(s.get("traceId")),
                    span_id=_hex(s.get("spanId")),
                    parent_span_id=_hex(s.get("parentSpanId")) if s.get("parentSpanId") else "",
                    name=str(s.get("name", "")),
                    kind=str(attrs.get("openinference.span.kind", "")).upper(),
                    otel_kind=_OTEL_KINDS.get(int(kind), "INTERNAL"),
                    service=service,
                    project=project,
                    source="otlp",
                    start_time=start,
                    end_time=end,
                    status=_STATUS.get(int(code), "UNSET"),
                    status_message=str(status.get("message", "")),
                    attributes=stringify_attrs(attrs),
                    events=json.dumps(events, default=str),
                    version=VERSION_OTLP,
                    **lift(attrs),
                ))
    return out


def empty_response(as_json: bool) -> tuple[bytes, str]:
    resp = ExportTraceServiceResponse()
    if as_json:
        return b"{}", "application/json"
    return resp.SerializeToString(), "application/x-protobuf"
