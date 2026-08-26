"""Optional OpenTelemetry / Arize-Phoenix tracing for the Prefront services.

CANONICAL COPY. This file is vendored verbatim into every service package
(each has its own Docker build context, so they cannot share an import).
Edit this file and run ``tracing/sync.sh`` — never edit a vendored copy.

Three constraints shape everything here:

* **Off unless configured.** With no collector endpoint (or ``PREFRONT_TRACING=0``)
  nothing is imported and every call is a cheap no-op. A developer running a
  service from a bare venv never needs the tracing packages installed.
* **Never fatal.** A missing package, an unreachable collector, a bad env var —
  all degrade to "no traces", never to a failed request. Observability must not
  be able to change a governed decision.
* **Domain-neutral.** Span names and attribute keys are engine vocabulary
  (``prefront.*``); the values come from the call being traced. Nothing here
  knows a table, a role, or a tenant.

Configuration (env):
    PHOENIX_COLLECTOR_ENDPOINT   Phoenix base URL, e.g. http://phoenix:6006
    OTEL_EXPORTER_OTLP_ENDPOINT  fallback if the Phoenix var is unset
    PHOENIX_PROJECT_NAME         Phoenix project (default: the service name)
    PREFRONT_TRACING=0           hard off-switch, whatever else is set
    PREFRONT_TRACE_FANOUT        comma-separated extra OTLP/HTTP endpoints that receive
                                 a copy of every span (out-of-band tap, e.g. the
                                 oob-ingest service); failures never affect Phoenix
    PREFRONT_TRACE_EXCLUDE_SPANS comma-separated span-name prefixes that are NOT
                                 recorded — the span AND everything it calls
                                 (auto-instrumented LLM/MCP spans included), so a
                                 deployment can keep one code path out of tracing
                                 entirely without turning tracing off process-wide
    PREFRONT_TRACE_MAX_VALUE     truncate input/output attribute values (default 4000)

Note on data: the OpenInference OpenAI instrumentor records prompts and
completions, which for the demo agents include governed query results. Set
``OPENINFERENCE_HIDE_INPUTS=true`` / ``OPENINFERENCE_HIDE_OUTPUTS=true`` to
suppress them. Prefront's own spans deliberately record the *decision*
(outcome, reasons, fired rules, masked field names, row count) and never rows.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any, Iterator, Mapping, Optional

log = logging.getLogger(__name__)

# --- OpenInference semantic conventions ------------------------------------
# String literals rather than an import, so the semconv package stays optional.
SPAN_KIND = "openinference.span.kind"
INPUT_VALUE = "input.value"
INPUT_MIME = "input.mime_type"
OUTPUT_VALUE = "output.value"
OUTPUT_MIME = "output.mime_type"
JSON_MIME = "application/json"

# Auto-instrumentors applied when tracing is on, as (module, class, target).
# Listed explicitly rather than discovered from entry points, so "what gets
# traced" is answerable by reading this tuple. ``target`` is the library the
# instrumentor patches; when it is absent the instrumentor is skipped quietly
# (the MCP runtime has no openai, the design-time services have no mcp client).
_INSTRUMENTORS = (
    ("openinference.instrumentation.openai", "OpenAIInstrumentor", "openai"),
    ("openinference.instrumentation.mcp", "MCPInstrumentor", "mcp"),
)

_ENABLED: Optional[bool] = None      # tri-state: None => setup() has not run
_PROVIDER: Any = None
_FALSEY = ("0", "off", "false", "no", "")


def excluded_spans() -> list[str]:
    """Span-name prefixes this deployment does not record (with their subtrees)."""
    raw = os.environ.get("PREFRONT_TRACE_EXCLUDE_SPANS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_excluded(name: str) -> bool:
    return any(name.startswith(p) for p in excluded_spans())


def collector_endpoint() -> str:
    """The configured collector base URL, or "" when tracing is unconfigured."""
    return (
        os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ""
    ).strip().rstrip("/")


def configured() -> bool:
    if os.environ.get("PREFRONT_TRACING", "1").strip().lower() in _FALSEY:
        return False
    return bool(collector_endpoint())


def enabled() -> bool:
    """True once setup() has successfully installed a tracer provider."""
    return bool(_ENABLED)


def setup(service_name: str, *, project_name: Optional[str] = None) -> bool:
    """Install a tracer provider + auto-instrumentors. Idempotent per process.

    Returns True when tracing is live. Safe to call from any entry point; the
    first call wins and later ones are free.
    """
    global _ENABLED, _PROVIDER
    if _ENABLED is not None:
        return _ENABLED
    _ENABLED = False

    if not configured():
        log.debug("tracing off (no collector endpoint / PREFRONT_TRACING disabled)")
        return False

    endpoint = collector_endpoint()
    project = project_name or os.environ.get("PHOENIX_PROJECT_NAME") or service_name
    # The exporter's resource is built by the SDK's env detector; this is how the
    # span gets a service.name so one project's traces stay attributable per
    # service. setdefault, so an explicit OTEL_SERVICE_NAME still wins.
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    try:
        _PROVIDER = _register(service_name, project, endpoint)
    except Exception as e:  # noqa: BLE001 - tracing must never break the service
        log.warning("tracing disabled: could not initialise exporter (%s: %s)",
                    type(e).__name__, e)
        return False

    _ENABLED = True
    fanout = _fan_out(_PROVIDER)
    applied = _auto_instrument(_PROVIDER)
    log.info("tracing on: service=%s project=%s endpoint=%s fanout=%s instrumentors=%s excluded=%s",
             service_name, project, endpoint, ", ".join(fanout) or "none",
             ", ".join(applied) or "none", ", ".join(excluded_spans()) or "none")
    return True


def fanout_endpoints() -> list[str]:
    raw = os.environ.get("PREFRONT_TRACE_FANOUT", "")
    return [e.strip().rstrip("/") for e in raw.split(",") if e.strip()]


def _fan_out(provider: Any) -> list[str]:
    """Attach one extra OTLP/HTTP exporter per PREFRONT_TRACE_FANOUT endpoint.

    This is the out-of-band tap: the same spans that go to Phoenix are also
    delivered to e.g. the oob-ingest service. Each exporter has its own batch
    processor, so a slow or dead fan-out target can only drop its own copy.
    """
    attached: list[str] = []
    endpoints = fanout_endpoints()
    if not endpoints:
        return attached
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as e:  # noqa: BLE001
        log.warning("trace fan-out unavailable (%s: %s)", type(e).__name__, e)
        return attached
    add = getattr(provider, "add_span_processor", None)
    if add is None:
        log.warning("trace fan-out unavailable: provider has no add_span_processor")
        return attached
    for ep in endpoints:
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_url(ep), timeout=5))
        try:
            # phoenix.otel's TracerProvider.add_span_processor() defaults to
            # replace_default_processor=True, which would silently DROP the
            # Phoenix exporter and turn the tap into a redirect. Say no.
            try:
                add(processor, replace_default_processor=False)
            except TypeError:
                add(processor)  # plain SDK provider: additive by nature
            attached.append(ep)
        except Exception as e:  # noqa: BLE001
            log.warning("trace fan-out to %s failed (%s: %s)", ep, type(e).__name__, e)
    return attached


def _register(service_name: str, project: str, endpoint: str) -> Any:
    """Prefer arize-phoenix-otel; fall back to a plain OTLP/HTTP SDK setup."""
    try:
        from phoenix.otel import register
    except ImportError:
        return _register_otlp(service_name, endpoint)

    kwargs = dict(project_name=project, endpoint=_traces_url(endpoint),
                  batch=True, auto_instrument=False, verbose=False)
    try:
        return register(**kwargs)
    except TypeError:  # older/newer signature — retry with the stable subset
        return register(project_name=project, endpoint=_traces_url(endpoint))


def _register_otlp(service_name: str, endpoint: str) -> Any:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_traces_url(endpoint))))
    trace.set_tracer_provider(provider)
    return provider


def _traces_url(endpoint: str) -> str:
    """Accept either a base URL or a full OTLP/HTTP traces URL."""
    return endpoint if endpoint.endswith("/v1/traces") else f"{endpoint}/v1/traces"


def _auto_instrument(provider: Any) -> list[str]:
    import importlib.util

    applied: list[str] = []
    for module_name, class_name, target in _INSTRUMENTORS:
        if importlib.util.find_spec(target) is None:
            log.debug("instrumentor %s skipped (%s not installed)", class_name, target)
            continue
        try:
            module = __import__(module_name, fromlist=[class_name])
            instrumentor = getattr(module, class_name)()
            instrumentor.instrument(tracer_provider=provider)
            # instrument() swallows a version conflict and returns; this flag is
            # the only honest signal that the patch actually went in.
            if getattr(instrumentor, "is_instrumented_by_opentelemetry", True):
                applied.append(class_name)
            else:
                log.warning("instrumentor %s did not attach (version conflict with %s?)",
                            class_name, target)
        except Exception as e:  # noqa: BLE001 - an absent instrumentor is fine
            log.debug("instrumentor %s unavailable (%s: %s)", class_name, type(e).__name__, e)
    return applied


# --- tracer handle ----------------------------------------------------------


class _NoopSpan:
    """Stands in for a span when tracing is off, so call sites need no branch."""

    def set_attribute(self, *_a: Any, **_k: Any) -> None: ...
    def set_attributes(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...
    def record_exception(self, *_a: Any, **_k: Any) -> None: ...
    def add_event(self, *_a: Any, **_k: Any) -> None: ...
    def update_name(self, *_a: Any, **_k: Any) -> None: ...

    def is_recording(self) -> bool:
        return False


@contextlib.contextmanager
def _suppressed() -> Iterator[None]:
    """Suppress ALL span creation for the duration of the block.

    Skipping only the excluded span itself would not be enough: the OpenAI and
    MCP auto-instrumentors would still emit their own spans, which would then
    re-parent onto whatever span is above it. Both the OTel and OpenInference
    suppression flags are set, so every instrumentor that honours either one
    stays quiet — that is what makes the exclusion cover the whole subtree.
    """
    stack = contextlib.ExitStack()
    try:
        from opentelemetry import context as otel_context

        keys = []
        try:
            from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY

            keys.append(_SUPPRESS_INSTRUMENTATION_KEY)
        except Exception:  # noqa: BLE001 - opentelemetry-instrumentation is optional
            pass
        keys.append("suppress_instrumentation")  # older/plain key some libs check

        ctx = otel_context.get_current()
        for key in keys:
            ctx = otel_context.set_value(key, True, ctx)
        token = otel_context.attach(ctx)
        stack.callback(otel_context.detach, token)
    except Exception as e:  # noqa: BLE001 - suppression is best-effort, never fatal
        log.debug("span suppression unavailable (%s: %s)", type(e).__name__, e)
    try:
        from openinference.instrumentation import suppress_tracing

        stack.enter_context(suppress_tracing())
    except Exception:  # noqa: BLE001 - openinference is optional
        pass
    try:
        yield
    finally:
        stack.close()


class _Tracer:
    """Lazy tracer: resolves on first span so module-level handles are safe.

    Call sites do ``_tracer = prefront_tracing.get_tracer(__name__)`` at import
    time, before setup() has run; resolution is deferred to the first span.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._real: Any = None

    @contextlib.contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Iterator[Any]:
        if _is_excluded(name):
            with _suppressed():
                yield _NoopSpan()
            return
        tracer = self._resolve()
        if tracer is None:
            yield _NoopSpan()
            return
        try:
            with tracer.start_as_current_span(name, **kwargs) as span:
                yield span
        except Exception as e:  # noqa: BLE001 - never let the SDK break the caller
            log.debug("span %r failed to start (%s: %s)", name, type(e).__name__, e)
            yield _NoopSpan()

    def _resolve(self) -> Any:
        if self._real is None:
            if not _ENABLED:
                return None
            from opentelemetry import trace
            self._real = trace.get_tracer(self._name)
        return self._real


def get_tracer(name: str) -> _Tracer:
    return _Tracer(name)


# --- attribute helpers ------------------------------------------------------


def _max_value() -> int:
    try:
        return int(os.environ.get("PREFRONT_TRACE_MAX_VALUE", "4000"))
    except ValueError:
        return 4000


def _coerce(value: Any) -> Any:
    """Map a Python value onto something OTel accepts, or None to skip it."""
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, (bool, int, float, str)):
        return _truncate(value)
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if all(isinstance(i, (bool, int, float, str)) for i in items):
            return [_truncate(i) for i in items]
        return as_json(items)
    return as_json(value)


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        limit = _max_value()
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    return value


def as_json(value: Any) -> str:
    """JSON for an input/output attribute — truncated, never raising."""
    try:
        return _truncate(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        return _truncate(str(value))


def set_attributes(span: Any, attributes: Mapping[str, Any]) -> None:
    """Set the attributes that have a value; silently drop empties and errors."""
    if not span.is_recording():
        return
    for key, raw in attributes.items():
        coerced = _coerce(raw)
        if coerced is None:
            continue
        try:
            span.set_attribute(key, coerced)
        except Exception as e:  # noqa: BLE001
            log.debug("attribute %r rejected (%s: %s)", key, type(e).__name__, e)


def record_error(span: Any, exc: BaseException) -> None:
    """Mark a span as failed. Governance outcomes are NOT errors — only crashes."""
    if not span.is_recording():
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    except Exception:  # noqa: BLE001
        pass


def mark_error(span: Any, message: str) -> None:
    """Mark a span as failed from a message (an error result, not an exception)."""
    if not span.is_recording():
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, message))
    except Exception:  # noqa: BLE001
        pass


# --- cross-boundary context -------------------------------------------------


def inject(headers: Optional[dict] = None) -> dict:
    """Add W3C traceparent headers so a plain HTTP hop stays in one trace."""
    carrier = dict(headers or {})
    if not _ENABLED:
        return carrier
    try:
        from opentelemetry.propagate import inject as _inject

        _inject(carrier)
    except Exception as e:  # noqa: BLE001
        log.debug("context inject failed (%s: %s)", type(e).__name__, e)
    return carrier


@contextlib.contextmanager
def remote_context(headers: Any) -> Iterator[None]:
    """Continue the caller's trace for the duration of the block.

    ``headers`` is any mapping of request headers (keys are lower-cased here,
    so an ``http.server`` message object works as-is).
    """
    if not _ENABLED:
        yield
        return
    token = None
    try:
        from opentelemetry import context as otel_context
        from opentelemetry.propagate import extract

        carrier = {str(k).lower(): v for k, v in dict(headers).items()}
        token = otel_context.attach(extract(carrier))
    except Exception as e:  # noqa: BLE001
        log.debug("context extract failed (%s: %s)", type(e).__name__, e)
    try:
        yield
    finally:
        if token is not None:
            try:
                from opentelemetry import context as otel_context

                otel_context.detach(token)
            except Exception:  # noqa: BLE001
                pass


def current_context() -> Any:
    """Snapshot the active context, for handing to a worker thread."""
    if not _ENABLED:
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def use_context(ctx: Any) -> Iterator[None]:
    """Re-attach a snapshot taken by current_context() (thread-pool workers).

    OTel context is thread-local, so work handed to a ThreadPoolExecutor loses
    its parent span unless the context is carried across explicitly.
    """
    if ctx is None:
        yield
        return
    token = None
    try:
        from opentelemetry import context as otel_context

        token = otel_context.attach(ctx)
    except Exception:  # noqa: BLE001
        pass
    try:
        yield
    finally:
        if token is not None:
            try:
                from opentelemetry import context as otel_context

                otel_context.detach(token)
            except Exception:  # noqa: BLE001
                pass
