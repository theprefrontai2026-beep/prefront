/*
 * Observability — the OOB (out-of-band) AI-engineering observability surface.
 *
 * Everything here reads from the `oob-ingest` service (/oob/*), which tails
 * Arize Phoenix (plus a direct OTLP fan-out from the app agents) into
 * ClickHouse. Strictly out-of-band: nothing inline — no Prefront engine span,
 * no governed-agent branch — is ingested; what you see is the raw app agent:
 * its runs, LLM calls (model, tokens, cost, latency) and tool calls.
 *
 * Sub-views mirror what any AEOP platform shows:
 *   Overview    KPIs, throughput/latency/error time series, breakdowns
 *   Sessions    one row per session.id → ordered step stream (turn → LLM →
 *               tool calls → answer): the unit the OOB checks evaluate
 *   Traces      filterable trace list → waterfall → span inspector
 *   LLM         per-model usage, tokens, cost, tool-call rate, recent calls
 *   Ingestion   pipeline health: ClickHouse, Phoenix poller, OTLP receiver
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/* ── types (mirror oobingest/ch.py) ─────────────────────────────────────── */

type Kpis = {
  traces: number; spans: number; error_spans: number; error_traces: number; error_rate: number;
  llm_calls: number; tokens_prompt: number; tokens_completion: number; tokens_total: number;
  tool_calls: number;
  services: number; models: number; p50_ms: number; p95_ms: number; max_ms: number; avg_ms: number;
  cost_usd: number;
};
type SeriesPoint = {
  bucket: string; traces: number; spans: number; errors: number; llm_calls: number; tokens: number;
  tool_calls: number; p95_ms: number;
};
type Overview = {
  kpis: Partial<Kpis>;
  by_service: { service: string; spans: number; traces: number; errors: number; p50_ms: number; p95_ms: number }[];
  by_kind: { kind: string; spans: number; errors: number; p50_ms: number }[];
  by_model: ModelRow[];
  by_tool: { tool: string; calls: number; errors: number; p50_ms: number }[];
  series: SeriesPoint[];
  bucket_seconds: number;
};
type ModelRow = {
  model: string; provider: string; calls: number; tokens_prompt: number; tokens_completion: number;
  tokens_total: number; avg_tokens?: number; p50_ms: number; p95_ms: number; errors: number;
  cost_usd?: number; tool_call_turns?: number;
};
type TraceRow = {
  trace_id: string; start_time: string; end_time: string; duration_ms: number; span_count: number;
  errors: number; llm_calls: number; tokens_total: number; root_name: string; root_service: string;
  root_kind: string; root_status: string; input_preview: string; output_preview: string;
  services: string[]; kinds: string[]; tools: string[]; tool_calls: number; models: string[];
  scenario_id: string; project: string;
};
type Span = {
  trace_id: string; span_id: string; parent_span_id: string; name: string; kind: string; otel_kind: string;
  service: string; project: string; source: string; start_time: string; end_time: string; duration_ms: number;
  status: string; status_message: string; attributes: Record<string, string>; events: string;
  input_value: string; output_value: string; llm_model: string; llm_provider: string;
  tokens_prompt: number; tokens_completion: number; tokens_total: number;
  scenario_id: string; tool_name: string;
  session_id?: string; user_id?: string; user_role?: string; channel?: string; intent_name?: string;
};
type SessionRow = {
  session_id: string; start_time: string; end_time: string; duration_ms: number; user_id: string; role: string;
  channel: string; scenario_id: string; variant: string; family: string; checks: string; turns: number;
  tool_calls: number; llm_calls: number; off_catalog_calls: number; writes: number; errors: number;
  tokens_total: number; span_count: number; trace_ids: string[]; tools: string[]; intents: string[];
  first_input: string; last_output: string; project: string;
};
type PopulationRow = {
  scenario_id: string; variant: string; sessions: number; distinct_shapes: number; avg_tool_calls: number;
  avg_writes: number; avg_errors: number; last_run: string;
};
type Facets = { services: string[]; kinds: string[]; tools: string[]; scenarios: string[]; projects: string[]; models: string[]; roles?: string[]; channels?: string[]; intents?: string[] };
type Status = {
  clickhouse: { ok: boolean; url: string; database: string; spans?: number; traces?: number; from_phoenix?: number; from_otlp?: number; oldest?: string; newest?: string };
  phoenix: { enabled: boolean; endpoint: string; projects: string[]; poll_seconds: number; lookback_seconds: number; polls: number; ingested_total: number; excluded_inline_total: number; unprovable_dropped: number; held_for_parent: number; exclude: { attr_prefixes: string[]; span_names: string[]; services: string[]; strip_attr_prefixes: string[] }; last_sync: string | null; last_error: string; watermarks: Record<string, string | null> };
  otlp: { endpoint: string; api_key_required: boolean; requests: number; spans: number; excluded_inline: number; last: string | null; last_error: string };
  started_at: string;
};
type LlmView = {
  by_model: ModelRow[];
  by_service: { service: string; calls: number; tokens_total: number; p50_ms: number }[];
  tools_requested: { tool: string; n: number }[];
  recent: { trace_id: string; span_id: string; name: string; service: string; start_time: string; duration_ms: number; status: string; model: string; tokens_prompt: number; tokens_completion: number; tokens_total: number; finish_reason: string; tool_called: string; output_preview: string }[];
};
type Scenario = { scenario_id: string; runs: number; capability: string; role: string; p50_ms: number; last_run: string };

// eval-engine (/eval/*) — verdicts are family2/family1/family3 checks over a
// session; findings are the violated subset; conformance tags are the
// satisfied-and-exercised subset with a policy citation when one exists.
type EvalVerdict = {
  session_id: string; check_id: string; family: string; rule_id: string;
  status: "satisfied" | "violated" | "indeterminate"; effect: string;
  indeterminate_reason: string; detail: string; evidence_span_ids: string[];
  evidence_excerpt: string; source: string; mode: string; engine_version: string;
  binding_profile_version: string; visibility_profile_version: string;
  rule_pack_version: string; catalog_version: string; evaluated_at: string;
};
type ConformanceTag = {
  session_id: string; check_id: string; rule_id: string; policy_document: string;
  clause_id: string; section: string; page: number; clause_text: string;
  evidence_span_ids: string[]; engine_version: string; rule_pack_version: string;
  catalog_version: string; evaluated_at: string;
};
type FindingsPage = { findings: EvalVerdict[]; total: number; limit: number; offset: number };

/* ── helpers ────────────────────────────────────────────────────────────── */

const RANGES: { label: string; seconds: number }[] = [
  { label: "15m", seconds: 900 }, { label: "1h", seconds: 3600 }, { label: "6h", seconds: 6 * 3600 },
  { label: "24h", seconds: 86400 }, { label: "7d", seconds: 7 * 86400 }, { label: "All", seconds: 0 },
];
const VIEWS = ["overview", "sessions", "traces", "llm", "ingestion", "findings"] as const;
type View = typeof VIEWS[number];
const VIEW_LABEL: Record<View, string> = {
  overview: "Overview", sessions: "Sessions", traces: "Traces", llm: "LLM", ingestion: "Ingestion",
  findings: "Findings",
};

const num = (n: number | undefined | null) => (n ?? 0).toLocaleString();
const ms = (n: number | undefined | null) => {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${Math.round(v)}ms`;
};
const usd = (n: number | undefined) => (n ? `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}` : "$0.00");
const pct = (n: number | undefined) => `${Math.round((n ?? 0) * 100)}%`;
const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
};
const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
const short = (id: string, n = 8) => (id || "").slice(0, n);
const parseList = (s: string): string[] => {
  if (!s) return [];
  try { const v = JSON.parse(s); return Array.isArray(v) ? v.map(String) : [String(v)]; } catch { return [s]; }
};
const kindTone = (k: string) => (
  k === "LLM" ? "llm" : k === "AGENT" ? "agent" : k === "TOOL" ? "tool" : k === "CHAIN" ? "chain" :
  k === "RETRIEVER" ? "retriever" : "span"
);

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const text = await res.text();
  let body: any = {};
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!res.ok) {
    const detail = typeof body?.detail === "string" && !/^\s*</.test(body.detail) ? body.detail : "";
    throw new Error(detail || `${res.status} ${res.statusText || "upstream unavailable"}`);
  }
  return body as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" ) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/* ── small presentational bits ──────────────────────────────────────────── */

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="pf-oob-kpi">
      <div className={`pf-oob-kpi-value ${tone ?? ""}`}>{value}</div>
      <div className="pf-oob-kpi-label">{label}</div>
      {sub && <div className="pf-oob-kpi-sub">{sub}</div>}
    </div>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const k = kind || "SPAN";
  return <span className={`pf-oob-kind ${kindTone(k)}`}>{k}</span>;
}

function Empty({ text }: { text: string }) {
  return <div className="pf-oob-empty">{text}</div>;
}

function Bars({ rows, label, value, max, tone }: {
  rows: any[]; label: (r: any) => string; value: (r: any) => number; max?: number; tone?: (r: any) => string;
}) {
  const m = max ?? Math.max(1, ...rows.map(value));
  if (!rows.length) return <Empty text="No data in range." />;
  return (
    <div className="pf-oob-bars">
      {rows.map((r, i) => (
        <div key={i} className="pf-oob-bar-row">
          <div className="pf-oob-bar-label" title={label(r)}>{label(r)}</div>
          <div className="pf-oob-bar-track">
            <div className={`pf-oob-bar-fill ${tone ? tone(r) : ""}`} style={{ width: `${Math.max(2, (value(r) / m) * 100)}%` }} />
          </div>
          <div className="pf-oob-bar-val">{num(value(r))}</div>
        </div>
      ))}
    </div>
  );
}

/* Time-series: bars (throughput) + line (p95 latency) + error ticks, inline SVG */
function Series({ points, bucket }: { points: SeriesPoint[]; bucket: number }) {
  const W = 900, H = 180, PAD = { l: 44, r: 44, t: 12, b: 26 };
  if (!points.length) return <Empty text="No spans in range yet — run a scenario against a demo agent (e.g. via Verdict)." />;
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const maxT = Math.max(1, ...points.map((p) => p.spans));
  const maxL = Math.max(1, ...points.map((p) => p.p95_ms));
  const bw = Math.max(2, iw / points.length - 2);
  const x = (i: number) => PAD.l + (i / points.length) * iw;
  const line = points.map((p, i) => `${(x(i) + bw / 2).toFixed(1)},${(PAD.t + ih - (p.p95_ms / maxL) * ih).toFixed(1)}`).join(" ");
  const fmtBucket = (iso: string) => {
    const d = new Date(iso);
    return bucket >= 86400 ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };
  const ticks = Math.min(6, points.length);
  return (
    <div className="pf-oob-series">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pf-oob-series-svg">
        {[0, 0.5, 1].map((f) => (
          <line key={f} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + ih - f * ih} y2={PAD.t + ih - f * ih} className="pf-oob-grid" />
        ))}
        {points.map((p, i) => {
          const h = (p.spans / maxT) * ih;
          const eh = (p.errors / maxT) * ih;
          return (
            <g key={p.bucket}>
              <rect x={x(i)} y={PAD.t + ih - h} width={bw} height={h} className="pf-oob-bar-spans">
                <title>{`${fmtBucket(p.bucket)}\n${p.traces} traces · ${p.spans} spans · ${p.llm_calls} LLM calls · ${p.tokens} tokens\n${p.tool_calls} tool calls\np95 ${ms(p.p95_ms)} · ${p.errors} errors`}</title>
              </rect>
              {p.errors > 0 && <rect x={x(i)} y={PAD.t + ih - eh} width={bw} height={eh} className="pf-oob-bar-errors" />}
            </g>
          );
        })}
        <polyline points={line} className="pf-oob-line-p95" />
        <text x={PAD.l - 6} y={PAD.t + 8} className="pf-oob-axis" textAnchor="end">{num(maxT)}</text>
        <text x={PAD.l - 6} y={PAD.t + ih} className="pf-oob-axis" textAnchor="end">0</text>
        <text x={W - PAD.r + 6} y={PAD.t + 8} className="pf-oob-axis p95">{ms(maxL)}</text>
        <text x={W - PAD.r + 6} y={PAD.t + ih} className="pf-oob-axis p95">0</text>
        {Array.from({ length: ticks }, (_, k) => {
          const i = Math.floor((k / Math.max(1, ticks - 1)) * (points.length - 1));
          return <text key={k} x={x(i) + bw / 2} y={H - 8} className="pf-oob-axis" textAnchor="middle">{fmtBucket(points[i].bucket)}</text>;
        })}
      </svg>
      <div className="pf-oob-legend">
        <span><i className="sw spans" /> spans / {bucket >= 3600 ? `${bucket / 3600}h` : `${bucket}s`}</span>
        <span><i className="sw errors" /> error spans</span>
        <span><i className="sw p95" /> p95 root latency</span>
      </div>
    </div>
  );
}

/* ── Overview ───────────────────────────────────────────────────────────── */

function OverviewView({ data }: { data: Overview | null }) {
  if (!data) return <Empty text="Loading…" />;
  const k = data.kpis;
  return (
    <div className="pf-oob-stack">
      <div className="pf-oob-kpis">
        <Kpi label="Traces" value={num(k.traces)} sub={`${num(k.spans)} spans`} />
        <Kpi label="Error rate" value={pct(k.error_rate)} sub={`${num(k.error_traces)} traces with errors`} tone={(k.error_traces ?? 0) > 0 ? "red" : "green"} />
        <Kpi label="p50 latency" value={ms(k.p50_ms)} sub={`p95 ${ms(k.p95_ms)} · max ${ms(k.max_ms)}`} />
        <Kpi label="LLM calls" value={num(k.llm_calls)} sub={`${num(k.models)} model${(k.models ?? 0) === 1 ? "" : "s"}`} />
        <Kpi label="Tokens" value={num(k.tokens_total)} sub={`${num(k.tokens_prompt)} in · ${num(k.tokens_completion)} out`} />
        <Kpi label="Est. cost" value={usd(k.cost_usd)} sub="list price, per model" />
        <Kpi label="Tool calls" value={num(k.tool_calls)} sub={`${num(data.by_tool.length)} distinct tools`} tone="teal" />
        <Kpi label="Services" value={num(k.services)} sub="app agents reporting" />
      </div>

      <section className="pf-panel">
        <div className="pf-oob-panel-head"><h3>Throughput & latency</h3><span className="pf-oob-subtle">{num(k.services)} services reporting</span></div>
        <Series points={data.series} bucket={data.bucket_seconds} />
      </section>

      <div className="pf-oob-grid-3">
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>Services</h3></div>
          {data.by_service.length ? (
            <table className="pf-oob-table">
              <thead><tr><th>Service</th><th>Traces</th><th>Spans</th><th>Err</th><th>p50</th><th>p95</th></tr></thead>
              <tbody>
                {data.by_service.map((s) => (
                  <tr key={s.service}>
                    <td className="mono">{s.service}</td><td>{num(s.traces)}</td><td>{num(s.spans)}</td>
                    <td className={s.errors ? "red" : ""}>{num(s.errors)}</td><td>{ms(s.p50_ms)}</td><td>{ms(s.p95_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No services in range." />}
        </section>
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>Span kinds</h3></div>
          <Bars rows={data.by_kind} label={(r) => r.kind} value={(r) => r.spans} tone={(r) => kindTone(r.kind)} />
        </section>
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>Tools called</h3></div>
          <Bars rows={data.by_tool} label={(r) => r.tool} value={(r) => r.calls} tone={() => "tool"} />
        </section>
      </div>

      <section className="pf-panel">
        <div className="pf-oob-panel-head"><h3>Models</h3></div>
        <ModelTable rows={data.by_model} />
      </section>
    </div>
  );
}

function ModelTable({ rows }: { rows: ModelRow[] }) {
  if (!rows.length) return <Empty text="No LLM calls in range." />;
  return (
    <table className="pf-oob-table">
      <thead><tr><th>Model</th><th>Provider</th><th>Calls</th><th>Prompt tok</th><th>Completion tok</th><th>Total tok</th><th>p50</th><th>p95</th><th>Errors</th><th>Est. cost</th></tr></thead>
      <tbody>
        {rows.map((m) => (
          <tr key={`${m.model}|${m.provider}`}>
            <td className="mono">{m.model || "—"}</td><td>{m.provider || "—"}</td><td>{num(m.calls)}</td>
            <td>{num(m.tokens_prompt)}</td><td>{num(m.tokens_completion)}</td><td>{num(m.tokens_total)}</td>
            <td>{ms(m.p50_ms)}</td><td>{ms(m.p95_ms)}</td><td className={m.errors ? "red" : ""}>{num(m.errors)}</td><td>{usd(m.cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── Traces: list + waterfall + inspector ───────────────────────────────── */

function TracesView({ since, project, facets, refreshKey, initialTrace, onOpenTrace }: {
  since: number; project: string; facets: Facets | null; refreshKey: number;
  initialTrace: string | null; onOpenTrace: (id: string | null) => void;
}) {
  const [service, setService] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [scenario, setScenario] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState("");
  const limit = 50;

  useEffect(() => { setOffset(0); }, [since, project, service, kind, status, scenario, q]);

  useEffect(() => {
    let alive = true;
    getJSON<{ traces: TraceRow[]; total: number }>(
      `/oob/traces${qs({ since: since || undefined, project, service, kind, status, scenario, q, limit, offset })}`,
    ).then((d) => { if (alive) { setRows(d.traces); setTotal(d.total); setErr(""); } })
     .catch((e) => { if (alive) setErr(String(e?.message || e)); });
    return () => { alive = false; };
  }, [since, project, service, kind, status, scenario, q, offset, refreshKey]);

  const sel = (label: string, value: string, set: (v: string) => void, opts: string[]) => (
    <label className="pf-oob-filter">
      <span>{label}</span>
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">All</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <div className="pf-oob-stack">
      <section className="pf-panel">
        <div className="pf-oob-filters">
          {sel("Service", service, setService, facets?.services ?? [])}
          {sel("Kind", kind, setKind, facets?.kinds ?? [])}
          <label className="pf-oob-filter"><span>Status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option><option value="ok">OK</option><option value="error">Error</option>
            </select>
          </label>
          {sel("Scenario", scenario, setScenario, facets?.scenarios ?? [])}
          <label className="pf-oob-filter grow"><span>Search</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="root span, input/output text, tool, service, trace id…" />
          </label>
        </div>
        {err && <div className="pf-oob-error">{err}</div>}
        {rows.length ? (
          <table className="pf-oob-table pf-oob-traces">
            <thead><tr><th>When</th><th>Root span</th><th>Service</th><th>Duration</th><th>Spans</th><th>LLM</th><th>Tools</th><th>Tokens</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.trace_id} className={`clickable ${initialTrace === t.trace_id ? "selected" : ""}`} onClick={() => onOpenTrace(t.trace_id)}>
                  <td className="nowrap">{when(t.start_time)}</td>
                  <td>
                    <div className="pf-oob-root"><KindBadge kind={t.root_kind} /> <span className="pf-oob-root-name">{t.root_name || "(no root)"}</span>{t.scenario_id && <span className="pf-oob-chip">{t.scenario_id}</span>}</div>
                    {t.input_preview && <div className="pf-oob-preview" title={t.input_preview}>{t.input_preview}</div>}
                  </td>
                  <td className="mono">{t.services.join(", ")}</td>
                  <td className="nowrap">{ms(t.duration_ms)}</td>
                  <td>{num(t.span_count)}</td>
                  <td>{num(t.llm_calls)}</td>
                  <td title={t.tools.join(", ")}>{num(t.tool_calls)}</td>
                  <td>{num(t.tokens_total)}</td>
                  <td>{t.errors ? <span className="pf-oob-chip red">{t.errors} error{t.errors > 1 ? "s" : ""}</span> : <span className="pf-oob-chip green">ok</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text={err ? "" : "No traces match."} />}
        <div className="pf-oob-pager">
          <span>{total ? `${offset + 1}–${Math.min(total, offset + rows.length)} of ${num(total)}` : "0 traces"}</span>
          <button className="pf-btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Prev</button>
          <button className="pf-btn sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next ›</button>
        </div>
      </section>
      {initialTrace && <TraceDetail traceId={initialTrace} onClose={() => onOpenTrace(null)} />}
    </div>
  );
}

function TraceDetail({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [spans, setSpans] = useState<Span[]>([]);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    setSpans([]); setSelected(null); setErr("");
    getJSON<{ spans: Span[] }>(`/oob/traces/${encodeURIComponent(traceId)}`)
      .then((d) => { if (alive) { setSpans(d.spans); setSelected(d.spans.find((s) => !s.parent_span_id)?.span_id ?? d.spans[0]?.span_id ?? null); } })
      .catch((e) => { if (alive) setErr(String(e?.message || e)); });
    return () => { alive = false; };
  }, [traceId]);

  // Build the tree in start order; orphans (parent not in trace) become roots.
  const tree = useMemo(() => {
    const ids = new Set(spans.map((s) => s.span_id));
    const kids = new Map<string, Span[]>();
    const roots: Span[] = [];
    for (const s of spans) {
      const p = s.parent_span_id && ids.has(s.parent_span_id) ? s.parent_span_id : "";
      if (!p) roots.push(s); else kids.set(p, [...(kids.get(p) ?? []), s]);
    }
    const out: { span: Span; depth: number; hasKids: boolean }[] = [];
    const walk = (s: Span, depth: number) => {
      const c = kids.get(s.span_id) ?? [];
      out.push({ span: s, depth, hasKids: c.length > 0 });
      if (collapsed.has(s.span_id)) return;
      for (const k of c) walk(k, depth + 1);
    };
    for (const r of roots) walk(r, 0);
    return out;
  }, [spans, collapsed]);

  const t0 = useMemo(() => Math.min(...spans.map((s) => new Date(s.start_time).getTime())), [spans]);
  const t1 = useMemo(() => Math.max(...spans.map((s) => new Date(s.end_time).getTime())), [spans]);
  const total = Math.max(1, t1 - t0);
  const sel = spans.find((s) => s.span_id === selected) ?? null;
  const llm = spans.filter((s) => s.kind === "LLM");
  const tokens = llm.reduce((a, s) => a + (s.tokens_total || 0), 0);
  const errors = spans.filter((s) => s.status === "ERROR").length;
  const root = spans.find((s) => !s.parent_span_id) ?? spans[0];

  return (
    <section className="pf-panel pf-oob-detail">
      <div className="pf-oob-panel-head">
        <div>
          <h3>{root ? <><KindBadge kind={root.kind} /> {root.name}</> : "Trace"}</h3>
          <div className="pf-oob-subtle mono">trace {traceId} · {spans.length} spans · {ms(total)} · {llm.length} LLM calls · {num(tokens)} tokens{errors ? ` · ${errors} errors` : ""}</div>
        </div>
        <button className="pf-btn sm" onClick={onClose}>Close</button>
      </div>
      {err && <div className="pf-oob-error">{err}</div>}
      <div className="pf-oob-detail-body">
        <div className="pf-oob-waterfall">
          {tree.map(({ span, depth, hasKids }) => {
            const s0 = new Date(span.start_time).getTime() - t0;
            const w = Math.max(0.4, ((new Date(span.end_time).getTime() - new Date(span.start_time).getTime()) / total) * 100);
            const l = (s0 / total) * 100;
            return (
              <div key={span.span_id} className={`pf-oob-wf-row ${selected === span.span_id ? "selected" : ""} ${span.status === "ERROR" ? "error" : ""}`} onClick={() => setSelected(span.span_id)}>
                <div className="pf-oob-wf-name" style={{ paddingLeft: 8 + depth * 16 }}>
                  <button className={`pf-oob-wf-toggle ${hasKids ? "" : "hidden"}`} onClick={(e) => { e.stopPropagation(); setCollapsed((c) => { const n = new Set(c); n.has(span.span_id) ? n.delete(span.span_id) : n.add(span.span_id); return n; }); }}>
                    {collapsed.has(span.span_id) ? "▸" : "▾"}
                  </button>
                  <KindBadge kind={span.kind} />
                  <span className="pf-oob-wf-label" title={span.name}>{span.name}</span>
                  {span.llm_model && <span className="pf-oob-chip">{span.llm_model}</span>}
                </div>
                <div className="pf-oob-wf-svc mono">{span.service}</div>
                <div className="pf-oob-wf-track">
                  <div className={`pf-oob-wf-bar ${kindTone(span.kind)}`} style={{ left: `${l}%`, width: `${w}%` }} />
                </div>
                <div className="pf-oob-wf-dur">{ms(span.duration_ms)}</div>
              </div>
            );
          })}
        </div>
        {sel && <SpanInspector span={sel} />}
      </div>
    </section>
  );
}

function pretty(v: string): string {
  if (!v) return "";
  try { return JSON.stringify(JSON.parse(v), null, 2); } catch { return v; }
}

function SpanInspector({ span }: { span: Span }) {
  const [showAll, setShowAll] = useState(false);
  const attrs = Object.entries(span.attributes ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const primary = attrs.filter(([k]) => !k.startsWith("llm.input_messages") && !k.startsWith("llm.output_messages") && !k.startsWith("llm.tools.") && k !== "input.value" && k !== "output.value");
  const messages = attrs.filter(([k]) => k.startsWith("llm.input_messages") || k.startsWith("llm.output_messages"));
  const events = parseList(span.events);
  const meta: [string, string][] = [
    ["span id", span.span_id], ["parent", span.parent_span_id || "(root)"], ["service", span.service],
    ["kind", span.kind || "SPAN"], ["otel kind", span.otel_kind || "—"], ["status", span.status + (span.status_message ? ` — ${span.status_message}` : "")],
    ["start", when(span.start_time)], ["duration", ms(span.duration_ms)], ["source", `${span.source} (project ${span.project})`],
  ];
  return (
    <div className="pf-oob-inspector">
      <h4><KindBadge kind={span.kind} /> {span.name}</h4>
      <dl className="pf-oob-kv">
        {meta.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>)}
      </dl>

      {span.kind === "LLM" && (
        <div className="pf-oob-inline-stats">
          <span><b>{span.llm_model || "?"}</b> {span.llm_provider && <em>via {span.llm_provider}</em>}</span>
          <span>{num(span.tokens_prompt)} prompt · {num(span.tokens_completion)} completion · <b>{num(span.tokens_total)}</b> tokens</span>
          {span.attributes["llm.finish_reason"] && <span>finish: {span.attributes["llm.finish_reason"]}</span>}
        </div>
      )}

      {span.input_value && <div className="pf-oob-io"><div className="pf-oob-io-head">input</div><pre>{pretty(span.input_value)}</pre></div>}
      {span.output_value && <div className="pf-oob-io"><div className="pf-oob-io-head">output</div><pre>{pretty(span.output_value)}</pre></div>}

      {messages.length > 0 && (
        <details className="pf-oob-details">
          <summary>LLM messages ({messages.length} fields)</summary>
          <table className="pf-oob-table compact"><tbody>
            {messages.map(([k, v]) => <tr key={k}><td className="mono key">{k}</td><td><pre className="wrap">{v}</pre></td></tr>)}
          </tbody></table>
        </details>
      )}

      <details className="pf-oob-details" open>
        <summary>Attributes ({primary.length})</summary>
        <table className="pf-oob-table compact"><tbody>
          {(showAll ? primary : primary.slice(0, 30)).map(([k, v]) => (
            <tr key={k}><td className="mono key">{k}</td><td><pre className="wrap">{v.length > 600 ? v.slice(0, 600) + "…" : v}</pre></td></tr>
          ))}
        </tbody></table>
        {primary.length > 30 && <button className="pf-btn sm" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${primary.length}`}</button>}
      </details>

      {events.length > 0 && (
        <details className="pf-oob-details">
          <summary>Events ({events.length})</summary>
          <pre className="wrap">{pretty(span.events)}</pre>
        </details>
      )}
    </div>
  );
}

/* ── Sessions ───────────────────────────────────────────────────────────── */
// A session is every span sharing one `session.id` — the unit the session-
// level checks evaluate. The list is what an evaluator would iterate over; the
// detail is the ordered step stream (user turn → LLM → tool calls → answer)
// the value-provenance graph is built from. No verdicts here: those belong to
// the evaluator, and this surface is deliberately just the evidence.

function SessionsView({ since, project, facets, refreshKey, initialSession, onOpenSession, onOpenTrace }: {
  since: number; project: string; facets: Facets | null; refreshKey: number;
  initialSession: string | null; onOpenSession: (id: string | null) => void; onOpenTrace: (id: string) => void;
}) {
  const [role, setRole] = useState("");
  const [channel, setChannel] = useState("");
  const [scenario, setScenario] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [pop, setPop] = useState<PopulationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState("");
  const limit = 50;

  useEffect(() => { setOffset(0); }, [since, project, role, channel, scenario, q]);
  useEffect(() => {
    let alive = true;
    getJSON<{ sessions: SessionRow[]; total: number }>(
      `/oob/sessions${qs({ since: since || undefined, project, role, channel, scenario, q, limit, offset })}`,
    ).then((d) => { if (alive) { setRows(d.sessions); setTotal(d.total); setErr(""); } })
     .catch((e) => { if (alive) setErr(String(e?.message || e)); });
    getJSON<{ scenarios: PopulationRow[] }>(`/oob/sessions/population${qs({ since: since || undefined, project })}`)
      .then((d) => { if (alive) setPop(d.scenarios); }).catch(() => {});
    return () => { alive = false; };
  }, [since, project, role, channel, scenario, q, offset, refreshKey]);

  const sel = (label: string, value: string, set: (v: string) => void, opts: string[]) => (
    <label className="pf-oob-filter">
      <span>{label}</span>
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">All</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <div className="pf-oob-stack">
      <section className="pf-panel">
        <div className="pf-oob-filters">
          {sel("Role", role, setRole, facets?.roles ?? [])}
          {sel("Channel", channel, setChannel, facets?.channels ?? [])}
          {sel("Scenario", scenario, setScenario, facets?.scenarios ?? [])}
          <label className="pf-oob-filter grow"><span>Search</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="session id, first user message, tool, intent…" />
          </label>
        </div>
        {err && <div className="pf-oob-error">{err}</div>}
        {rows.length ? (
          <table className="pf-oob-table pf-oob-sessions">
            <thead><tr><th>When</th><th>Session</th><th>Caller</th><th>Channel</th><th>Turns</th><th>Tools</th><th>Writes</th><th>Off-catalog</th><th>LLM</th><th>Duration</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.session_id} className={`clickable ${initialSession === r.session_id ? "selected" : ""}`} onClick={() => onOpenSession(r.session_id)}>
                  <td className="nowrap">{when(r.start_time)}</td>
                  <td>
                    <div className="pf-oob-root"><span className="pf-oob-root-name mono">{r.session_id}</span>{r.scenario_id && <span className="pf-oob-chip">{r.scenario_id}</span>}{r.variant && r.variant !== "v1" && <span className="pf-oob-chip amber">{r.variant}</span>}</div>
                    {r.first_input && <div className="pf-oob-preview" title={r.first_input}>{r.first_input}</div>}
                  </td>
                  <td className="nowrap">{r.role || "—"}{r.user_id && <span className="dim"> · {r.user_id}</span>}</td>
                  <td className="mono">{r.channel || "—"}</td>
                  <td>{num(r.turns)}</td>
                  <td title={r.tools.join(", ")}>{num(r.tool_calls)}</td>
                  <td>{r.writes ? <span className="pf-oob-chip amber">{r.writes}</span> : <span className="dim">0</span>}</td>
                  <td>{r.off_catalog_calls ? <span className="pf-oob-chip red">{r.off_catalog_calls}</span> : <span className="dim">0</span>}</td>
                  <td>{num(r.llm_calls)}</td>
                  <td className="nowrap">{ms(r.duration_ms)}</td>
                  <td>{r.errors ? <span className="pf-oob-chip red">{r.errors} error{r.errors > 1 ? "s" : ""}</span> : <span className="pf-oob-chip green">ok</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text={err ? "" : "No sessions in range. Run a scenario against a demo agent (e.g. via Verdict)."} />}
        <div className="pf-oob-pager">
          <span>{total ? `${offset + 1}–${Math.min(total, offset + rows.length)} of ${num(total)}` : "0 sessions"}</span>
          <button className="pf-btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Prev</button>
          <button className="pf-btn sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next ›</button>
        </div>
      </section>
      {initialSession && <SessionDetail sessionId={initialSession} refreshKey={refreshKey} onClose={() => onOpenSession(null)} onOpenTrace={onOpenTrace} />}
      {pop.length > 0 && (
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><div><h3>Population · action shape per scenario</h3><div className="pf-oob-subtle">How many distinct tool-call shapes the same scenario produced — the raw material for outcome_consistency and invocation_drift (v1 vs v2).</div></div></div>
          <table className="pf-oob-table compact">
            <thead><tr><th>Scenario</th><th>Variant</th><th>Sessions</th><th>Distinct shapes</th><th>Avg tools</th><th>Avg writes</th><th>Avg errors</th><th>Last run</th></tr></thead>
            <tbody>
              {pop.map((p) => (
                <tr key={p.scenario_id + p.variant}>
                  <td className="mono">{p.scenario_id}</td><td className="mono">{p.variant || "—"}</td><td>{num(p.sessions)}</td>
                  <td>{p.distinct_shapes > 1 ? <span className="pf-oob-chip amber">{p.distinct_shapes}</span> : p.distinct_shapes}</td>
                  <td>{p.avg_tool_calls}</td><td>{p.avg_writes}</td><td>{p.avg_errors}</td><td className="nowrap">{when(p.last_run)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

type Step = { span: Span; turn: number; kind: "turn" | "llm" | "tool" | "answer" | "other"; title: string; text: string; mono: boolean };

function stepsOf(spans: Span[]): Step[] {
  // Order the session as an evaluator reads it: each turn's user message,
  // then its LLM calls and tool calls in time order, then the answer.
  const out: Step[] = [];
  const turns = spans.filter((s) => /^turn \d+/.test(s.name)).sort((a, b) => a.start_time.localeCompare(b.start_time));
  const byParent = new Map<string, Span[]>();
  for (const s of spans) byParent.set(s.parent_span_id, [...(byParent.get(s.parent_span_id) ?? []), s]);
  const descendants = (id: string): Span[] => (byParent.get(id) ?? []).flatMap((c) => [c, ...descendants(c.span_id)]);
  const seen = new Set<string>();
  turns.forEach((t, i) => {
    const n = i + 1;
    seen.add(t.span_id);
    out.push({ span: t, turn: n, kind: "turn", title: `user · turn ${n}`, text: t.input_value, mono: false });
    for (const s of descendants(t.span_id).sort((a, b) => a.start_time.localeCompare(b.start_time))) {
      seen.add(s.span_id);
      if (s.kind === "LLM") {
        const tc = s.attributes["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"];
        const content = s.attributes["llm.output_messages.0.message.content"];
        out.push({ span: s, turn: n, kind: "llm", title: `${s.llm_model || "LLM"}${s.attributes["app.replay"] ? " (scripted)" : ""}`,
          text: tc ? `→ ${tc} ${s.attributes["llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments"] || ""}` : (content || s.output_value), mono: !!tc });
      } else if (s.kind === "TOOL") {
        const intent = s.intent_name || s.attributes["app.intent"];
        out.push({ span: s, turn: n, kind: "tool", title: `${s.tool_name || s.name}${intent ? ` · ${intent}` : " · OFF-CATALOG"}${s.attributes["app.side_effect"] === "write" ? " · write" : ""}`,
          text: `args ${s.input_value}\n→ ${s.output_value.length > 400 ? s.output_value.slice(0, 400) + "…" : s.output_value}`, mono: true });
      }
    }
    if (t.output_value) out.push({ span: t, turn: n, kind: "answer", title: "agent", text: t.output_value, mono: false });
  });
  for (const s of spans) if (!seen.has(s.span_id) && s.kind !== "CHAIN") out.push({ span: s, turn: 0, kind: "other", title: s.name, text: s.input_value || s.output_value, mono: true });
  return out;
}

export function SessionDetail({ sessionId, refreshKey, initialSpanId, onClose, onOpenTrace }: { sessionId: string; refreshKey: number; initialSpanId?: string | null; onClose: () => void; onOpenTrace: (id: string) => void }) {
  const [spans, setSpans] = useState<Span[]>([]);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<string | null>(initialSpanId ?? null);
  const [verdicts, setVerdicts] = useState<EvalVerdict[]>([]);
  const [tags, setTags] = useState<ConformanceTag[]>([]);

  useEffect(() => { setSpans([]); setSelected(initialSpanId ?? null); setErr(""); setVerdicts([]); setTags([]); }, [sessionId, initialSpanId]);
  // A session just run (e.g. via Verdict) may not be ingested yet (the OTLP
  // tap batches for a few seconds; the orchestrator's root arrives via the
  // Phoenix poll). Re-fetch on every refresh tick until it lands.
  useEffect(() => {
    let alive = true;
    getJSON<{ spans: Span[] }>(`/oob/sessions/${encodeURIComponent(sessionId)}`)
      .then((d) => { if (alive) { setSpans(d.spans); setErr(""); } })
      .catch((e) => { if (alive) setErr(/404/.test(String(e?.message || e)) ? "Not ingested yet — waiting for the OTLP tap / Phoenix poll…" : String(e?.message || e)); });
    // Best-effort — eval-engine may not have evaluated this session yet, or
    // Family 1/3 may simply not be configured; never blocks the trace view.
    getJSON<{ verdicts: EvalVerdict[] }>(`/eval/sessions/${encodeURIComponent(sessionId)}/verdicts`)
      .then((d) => { if (alive) setVerdicts(d.verdicts); }).catch(() => {});
    getJSON<{ conformance_tags: ConformanceTag[] }>(`/eval/sessions/${encodeURIComponent(sessionId)}/conformance`)
      .then((d) => { if (alive) setTags(d.conformance_tags); }).catch(() => {});
    return () => { alive = false; };
  }, [sessionId, refreshKey]);

  const steps = useMemo(() => stepsOf(spans), [spans]);
  const root = spans.find((s) => s.name.startsWith("session ")) ?? spans.find((s) => /^turn /.test(s.name)) ?? spans[0];
  const sel = spans.find((s) => s.span_id === selected) ?? null;
  const tools = spans.filter((s) => s.kind === "TOOL");
  const traces = Array.from(new Set(spans.map((s) => s.trace_id)));
  const checks = parseList(root?.attributes["scenario.checks"] ?? "");
  const policy = parseList(root?.attributes["scenario.policy"] ?? "");
  return (
    <section className="pf-panel pf-oob-detail">
      <div className="pf-oob-panel-head">
        <div>
          <h3>session <span className="mono">{sessionId}</span></h3>
          <div className="pf-oob-subtle mono">
            {root?.user_role || root?.attributes["app.user.role"] || "?"} · user {root?.user_id || root?.attributes["user.id"] || "?"} · {root?.channel || root?.attributes["app.channel"] || "?"}
            {root?.scenario_id && <> · scenario {root.scenario_id}</>}{root?.attributes["app.variant"] && <> · {root.attributes["app.variant"]}</>}
            {" "}· {spans.filter((s) => /^turn /.test(s.name)).length} turns · {tools.length} tool calls · {traces.length} trace{traces.length === 1 ? "" : "s"}
          </div>
          {checks.length > 0 && <div style={{ marginTop: 4 }}>{checks.map((c) => <span key={c} className="pf-oob-chip amber">{c}</span>)}{policy.map((c) => <span key={c} className="pf-oob-chip">§{c}</span>)}<span className="pf-oob-subtle"> ← checks this scenario is built to trigger and the policy sections they attribute to (from the harness, not a verdict)</span></div>}
          {(verdicts.length > 0 || tags.length > 0) && (
            <div style={{ marginTop: 4 }}>
              {verdicts.filter((v) => v.status !== "satisfied").map((v, i) => (
                <span key={"v" + i} className={`pf-oob-chip ${STATUS_TONE[v.status] || ""}`} title={v.detail}>{v.check_id} · {v.status}</span>
              ))}
              {tags.map((t, i) => (
                <span key={"t" + i} className="pf-oob-chip green" title={t.clause_text || t.check_id}>{t.check_id}{t.section ? ` · §${t.section}` : ""} ✓</span>
              ))}
              <span className="pf-oob-subtle"> ← eval-engine's actual verdicts for this session (green = satisfied/conformance, red/amber = violated/indeterminate)</span>
            </div>
          )}
        </div>
        <div className="pf-oob-actions">
          {traces.map((t) => <button key={t} className="pf-btn sm" onClick={() => onOpenTrace(t)}>trace {short(t)}</button>)}
          <button className="pf-btn sm" onClick={onClose}>Close</button>
        </div>
      </div>
      {err && <div className="pf-oob-error">{err}</div>}
      <div className="pf-oob-detail-body">
        <div className="pf-oob-steps">
          {steps.map((st, i) => (
            <div key={st.span.span_id + st.kind + i}>
              {st.kind === "turn" && <div className="pf-oob-turn-sep">turn {st.turn}</div>}
              <div className={`pf-oob-step ${selected === st.span.span_id ? "selected" : ""} ${st.span.status === "ERROR" && st.kind === "tool" ? "error" : ""}`} onClick={() => setSelected(st.span.span_id)}>
                <div className="pf-oob-step-kind">
                  <KindBadge kind={st.kind === "turn" || st.kind === "answer" ? "AGENT" : st.span.kind} />
                  <span className="pf-oob-subtle">{ms(st.span.duration_ms)}</span>
                </div>
                <div className="pf-oob-step-body">
                  <div className="pf-oob-step-title">{st.title}{st.span.status === "ERROR" && st.kind === "tool" && <span className="pf-oob-chip red">error</span>}</div>
                  <div className={`pf-oob-step-text ${st.mono ? "mono" : ""}`}>{st.text}</div>
                </div>
              </div>
            </div>
          ))}
          {!steps.length && !err && <Empty text="Loading…" />}
        </div>
        {sel && <SpanInspector span={sel} />}
      </div>
    </section>
  );
}

/** Slide-out panel showing one session's full trace detail without leaving
 *  the current view - what a Findings row opens (autonomous_build.md step
 *  16's Findings view: click a finding, see the trace it came from). Same
 *  pattern as Verdict's SessionFlyout (artifacts/verdict/src/components/
 *  SessionRunner.tsx) - no shared code between the two apps, so this is a
 *  deliberate port, not an import. Reuses the parent's own refresh tick
 *  rather than a second poll timer. */
function SessionFlyout({ sessionId, initialSpanId, refreshKey, onClose, onOpenTrace }: {
  sessionId: string; initialSpanId?: string | null; refreshKey: number;
  onClose: () => void; onOpenTrace: (id: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="pf-flyout-backdrop" onClick={onClose} />
      <aside className="pf-flyout" role="dialog" aria-label={`Session ${sessionId}`}>
        <div className="pf-flyout-head">
          <div className="pf-flyout-title">Trace detail</div>
          <div className="pf-oob-actions">
            <button className="pf-btn sm" onClick={onClose}>Close ✕</button>
          </div>
        </div>
        <div className="pf-flyout-body">
          <SessionDetail sessionId={sessionId} refreshKey={refreshKey} initialSpanId={initialSpanId}
                        onClose={onClose} onOpenTrace={(id) => { onOpenTrace(id); onClose(); }} />
        </div>
      </aside>
    </>
  );
}

/* ── LLM ────────────────────────────────────────────────────────────────── */

function LlmView({ data, onOpenTrace }: { data: LlmView | null; onOpenTrace: (id: string) => void }) {
  if (!data) return <Empty text="Loading…" />;
  const totals = data.by_model.reduce((a, m) => ({ calls: a.calls + m.calls, tokens: a.tokens + m.tokens_total, cost: a.cost + (m.cost_usd ?? 0), tool: a.tool + (m.tool_call_turns ?? 0) }), { calls: 0, tokens: 0, cost: 0, tool: 0 });
  return (
    <div className="pf-oob-stack">
      <div className="pf-oob-kpis">
        <Kpi label="LLM calls" value={num(totals.calls)} />
        <Kpi label="Tokens" value={num(totals.tokens)} />
        <Kpi label="Est. cost" value={usd(totals.cost)} />
        <Kpi label="Tool-call turns" value={num(totals.tool)} sub={totals.calls ? `${Math.round((totals.tool / totals.calls) * 100)}% of calls` : ""} />
        <Kpi label="Models" value={num(data.by_model.length)} />
      </div>
      <section className="pf-panel"><div className="pf-oob-panel-head"><h3>By model</h3></div><ModelTable rows={data.by_model} /></section>
      <div className="pf-oob-grid-2">
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>Calls by service</h3></div>
          <Bars rows={data.by_service} label={(r) => r.service} value={(r) => r.calls} />
        </section>
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>Tools the model asked for</h3><span className="pf-oob-subtle">first tool call per turn</span></div>
          <Bars rows={data.tools_requested} label={(r) => r.tool} value={(r) => r.n} tone={() => "tool"} />
        </section>
      </div>
      <section className="pf-panel">
        <div className="pf-oob-panel-head"><h3>Recent calls</h3></div>
        {data.recent.length ? (
          <table className="pf-oob-table">
            <thead><tr><th>When</th><th>Service</th><th>Model</th><th>Tokens</th><th>Duration</th><th>Finish</th><th>Tool called</th><th>Output</th></tr></thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.span_id} className="clickable" onClick={() => onOpenTrace(r.trace_id)}>
                  <td className="nowrap">{when(r.start_time)}</td><td className="mono">{r.service}</td><td className="mono">{r.model}</td>
                  <td>{num(r.tokens_total)}</td><td>{ms(r.duration_ms)}</td><td>{r.finish_reason || "—"}</td>
                  <td className="mono">{r.tool_called || "—"}</td><td className="pf-oob-preview" title={r.output_preview}>{r.output_preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text="No LLM calls in range." />}
      </section>
    </div>
  );
}

/* ── Ingestion ──────────────────────────────────────────────────────────── */

function IngestionView({ status, scenarios, onSync, onClear, busy }: {
  status: Status | null; scenarios: Scenario[]; onSync: () => void; onClear: () => void; busy: boolean;
}) {
  if (!status) return <Empty text="Loading…" />;
  const ch = status.clickhouse, px = status.phoenix, ot = status.otlp;
  const dot = (ok: boolean) => <span className={`pf-oob-dot ${ok ? "ok" : "bad"}`} />;
  return (
    <div className="pf-oob-stack">
      <div className="pf-oob-grid-3">
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>{dot(ch.ok)} ClickHouse</h3></div>
          <dl className="pf-oob-kv">
            <div><dt>url</dt><dd className="mono">{ch.url}</dd></div>
            <div><dt>database</dt><dd className="mono">{ch.database}</dd></div>
            <div><dt>spans</dt><dd>{num(ch.spans)} ({num(ch.from_phoenix)} via Phoenix, {num(ch.from_otlp)} via OTLP tap)</dd></div>
            <div><dt>traces</dt><dd>{num(ch.traces)}</dd></div>
            <div><dt>range</dt><dd>{ch.oldest ? `${when(ch.oldest)} → ${when(ch.newest)}` : "empty"}</dd></div>
          </dl>
        </section>
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>{dot(px.enabled && !px.last_error)} Phoenix source (pull)</h3></div>
          <dl className="pf-oob-kv">
            <div><dt>endpoint</dt><dd className="mono">{px.endpoint || "(disabled)"}</dd></div>
            <div><dt>projects</dt><dd className="mono">{px.projects.join(", ") || "—"}</dd></div>
            <div><dt>poll</dt><dd>every {px.poll_seconds}s, {px.lookback_seconds}s lookback</dd></div>
            <div><dt>last sync</dt><dd>{ago(px.last_sync)} ({num(px.polls)} polls, {num(px.ingested_total)} spans this process)</dd></div>
            <div><dt>excluded</dt><dd>{num(px.excluded_inline_total)} inline · {num(px.unprovable_dropped)} unprovable parent · {num(px.held_for_parent)} held</dd></div>
            {Object.entries(px.watermarks).map(([p, w]) => <div key={p}><dt>watermark {p}</dt><dd>{when(w)}</dd></div>)}
            {px.last_error && <div><dt>error</dt><dd className="red">{px.last_error}</dd></div>}
          </dl>
        </section>
        <section className="pf-panel">
          <div className="pf-oob-panel-head"><h3>{dot(!ot.last_error)} OTLP tap (push)</h3></div>
          <dl className="pf-oob-kv">
            <div><dt>endpoint</dt><dd className="mono">POST {ot.endpoint}</dd></div>
            <div><dt>auth</dt><dd>{ot.api_key_required ? "bearer key required" : "open (internal network)"}</dd></div>
            <div><dt>received</dt><dd>{num(ot.requests)} batches · {num(ot.spans)} spans · {num(ot.excluded_inline)} inline dropped</dd></div>
            <div><dt>last batch</dt><dd>{ago(ot.last)}</dd></div>
            {ot.last_error && <div><dt>error</dt><dd className="red">{ot.last_error}</dd></div>}
          </dl>
          <p className="pf-oob-subtle">Only the app agents fan out here (<code>PREFRONT_TRACE_FANOUT</code>); a span seen by both sources resolves to the OTLP copy (it carries <code>service.name</code>).</p>
        </section>
      </div>
      <section className="pf-panel">
        <div className="pf-oob-panel-head"><h3>Inline exclusions (never ingested)</h3><span className="pf-oob-subtle">span + its whole subtree</span></div>
        <dl className="pf-oob-kv">
          <div><dt>attribute prefixes</dt><dd className="mono">{px.exclude?.attr_prefixes.join(", ") || "—"}</dd></div>
          <div><dt>span names</dt><dd className="mono">{px.exclude?.span_names.join(", ") || "—"}</dd></div>
          <div><dt>services</dt><dd className="mono">{px.exclude?.services.join(", ") || "—"}</dd></div>
          <div><dt>attrs scrubbed</dt><dd className="mono">{px.exclude?.strip_attr_prefixes?.join(", ") || "—"}</dd></div>
        </dl>
      </section>
      <section className="pf-panel">
        <div className="pf-oob-panel-head">
          <h3>Scenario coverage</h3>
          <div className="pf-oob-actions">
            <button className="pf-btn sm" onClick={onSync} disabled={busy}>Sync from Phoenix now</button>
            <button className="pf-btn sm reject" onClick={onClear} disabled={busy}>Clear ClickHouse</button>
          </div>
        </div>
        {scenarios.length ? (
          <table className="pf-oob-table">
            <thead><tr><th>Scenario</th><th>Capability</th><th>Role</th><th>Runs</th><th>p50</th><th>Last run</th></tr></thead>
            <tbody>
              {scenarios.map((s) => (
                <tr key={s.scenario_id}><td className="mono">{s.scenario_id}</td><td>{s.capability}</td><td>{s.role}</td><td>{num(s.runs)}</td><td>{ms(s.p50_ms)}</td><td>{when(s.last_run)}</td></tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text="No scenario traces in range." />}
      </section>
    </div>
  );
}

/* ── findings (eval-engine) ────────────────────────────────────────────── */

const STATUS_TONE: Record<string, string> = { violated: "red", satisfied: "green", indeterminate: "amber" };

function StatusChip({ status }: { status: string }) {
  return <span className={`pf-oob-chip ${STATUS_TONE[status] || ""}`}>{status}</span>;
}

function FindingsView({ refreshKey, onOpenFinding }: { refreshKey: number; onOpenFinding: (sessionId: string, spanId: string | null) => void }) {
  const [family, setFamily] = useState("");
  const [checkId, setCheckId] = useState("");
  const [rows, setRows] = useState<EvalVerdict[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState("");
  const limit = 50;

  useEffect(() => { setOffset(0); }, [family, checkId]);
  useEffect(() => {
    let alive = true;
    getJSON<FindingsPage>(`/eval/findings${qs({ family, check_id: checkId, limit, offset })}`)
      .then((d) => { if (alive) { setRows(d.findings); setTotal(d.total); setErr(""); } })
      .catch((e) => { if (alive) setErr(String(e?.message || e)); });
    return () => { alive = false; };
  }, [family, checkId, offset, refreshKey]);

  const checkIds = useMemo(() => Array.from(new Set(rows.map((r) => r.check_id))).sort(), [rows]);
  const sel = (label: string, value: string, set: (v: string) => void, opts: string[]) => (
    <label className="pf-oob-filter">
      <span>{label}</span>
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">All</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <div className="pf-oob-stack">
      <section className="pf-panel">
        <div className="pf-oob-filters">
          {sel("Family", family, setFamily, ["family1", "family2", "family3"])}
          {sel("Check", checkId, setCheckId, checkIds)}
          <span className="pf-oob-subtle">Violations only — eval-engine's shadow evaluation of every ingested session (see eval-engine/CLAUDE.md). A clean stack shows none.</span>
        </div>
        {err && <div className="pf-oob-error">Findings backend: {err}</div>}
        {rows.length ? (
          <table className="pf-oob-table">
            <thead><tr><th>When</th><th>Session</th><th>Family</th><th>Check</th><th>Effect</th><th>Rule</th><th>Detail</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.session_id + r.check_id + r.evidence_excerpt + i} className="clickable"
                    onClick={() => onOpenFinding(r.session_id, r.evidence_span_ids?.[0] ?? null)}>
                  <td className="nowrap">{when(r.evaluated_at)}</td>
                  <td className="mono">{r.session_id}</td>
                  <td className="mono">{r.family}</td>
                  <td><StatusChip status={r.status} />{" "}{r.check_id}</td>
                  <td className="mono">{r.effect}</td>
                  <td className="mono">{r.rule_id || "—"}</td>
                  <td className="pf-oob-preview" title={r.detail}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text={err ? "" : "No findings in range — either nothing violated, or Family 1/3 have no rule_pack/intent_catalog configured."} />}
        <div className="pf-oob-pager">
          <span>{total ? `${offset + 1}–${Math.min(total, offset + rows.length)} of ${num(total)}` : "0 findings"}</span>
          <button className="pf-btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Prev</button>
          <button className="pf-btn sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next ›</button>
        </div>
      </section>
    </div>
  );
}

/* ── root ───────────────────────────────────────────────────────────────── */

export default function Observability({ active = true }: { active?: boolean }) {
  const [view, setView] = useState<View>("overview");
  const [openSess, setOpenSess] = useState<string | null>(null);
  const [flyout, setFlyout] = useState<{ sessionId: string; spanId: string | null } | null>(null);
  const [since, setSince] = useState<number>(86400);
  const [project, setProject] = useState("");
  const [auto, setAuto] = useState(true);
  const [tick, setTick] = useState(0);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [llm, setLlm] = useState<LlmView | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [openTrace, setOpenTrace] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const p = qs({ since: since || undefined, project });
    const jobs: Promise<any>[] = [
      getJSON<Overview>(`/oob/overview${p}`).then((d) => alive && setOverview(d)),
      getJSON<Facets>(`/oob/facets${p}`).then((d) => alive && setFacets(d)),
      getJSON<Status>("/oob/status").then((d) => alive && setStatus(d)),
    ];
    if (view === "llm") jobs.push(getJSON<LlmView>(`/oob/llm${p}`).then((d) => alive && setLlm(d)));
    if (view === "ingestion") jobs.push(getJSON<{ scenarios: Scenario[] }>(`/oob/scenarios${p}`).then((d) => alive && setScenarios(d.scenarios)));
    Promise.all(jobs).then(() => alive && setErr("")).catch((e) => alive && setErr(String(e?.message || e)));
    return () => { alive = false; };
  }, [active, since, project, view, tick]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (auto && active) timer.current = window.setInterval(refresh, 10_000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [auto, active, refresh]);

  const goTrace = (id: string | null) => { setOpenTrace(id); if (id) setView("traces"); };
  const goSession = (id: string | null) => { setOpenSess(id); if (id) setView("sessions"); };
  const openFinding = (sessionId: string, spanId: string | null) => setFlyout({ sessionId, spanId });

  const sync = async () => {
    setBusy(true);
    try { await fetch("/oob/sync", { method: "POST" }); refresh(); } finally { setBusy(false); }
  };
  const clear = async () => {
    if (!window.confirm("Delete every ClickHouse table — spans, ingest state, AND eval-engine's verdicts/conformance tags/evaluated-sessions? Phoenix keeps its own copy and will be re-pulled from scratch; eval-engine will re-evaluate every session from zero.")) return;
    setBusy(true);
    try {
      // /oob/spans truncates spans + ingest_state (oob-ingest's tables);
      // /eval/verdicts truncates eval_verdicts + eval_conformance_tags +
      // eval_evaluated_sessions (eval-engine's) - together, every ClickHouse
      // table in the stack. Both in parallel; both are dev-only truncates,
      // idempotent either order.
      await Promise.all([
        fetch("/oob/spans", { method: "DELETE" }),
        fetch("/eval/verdicts", { method: "DELETE" }),
      ]);
      setOpenTrace(null);
      refresh();
    } finally { setBusy(false); }
  };

  const ok = status?.clickhouse.ok ?? false;
  return (
    <div className="pf-oob">
      <div className="pf-oob-toolbar">
        <div className="pf-oob-views">
          {VIEWS.map((v) => <button key={v} className={`pf-oob-view ${view === v ? "active" : ""}`} onClick={() => setView(v)}>{VIEW_LABEL[v]}</button>)}
        </div>
        <div className="pf-oob-controls">
          <span className={`pf-oob-health ${ok ? "ok" : "bad"}`} title={status ? `ClickHouse ${ok ? "connected" : "unreachable"} · Phoenix ${status.phoenix.last_error ? "error" : `synced ${ago(status.phoenix.last_sync)}`}` : "connecting…"}>
            <span className="pf-oob-dot" /> {status ? (ok ? `${num(status.clickhouse.spans)} spans` : "ClickHouse down") : "…"}
          </span>
          {facets && facets.projects.length > 1 && (
            <select value={project} onChange={(e) => setProject(e.target.value)} className="pf-oob-select">
              <option value="">All projects</option>
              {facets.projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
          <div className="pf-oob-ranges">
            {RANGES.map((r) => <button key={r.label} className={`pf-oob-range ${since === r.seconds ? "active" : ""}`} onClick={() => setSince(r.seconds)}>{r.label}</button>)}
          </div>
          <label className="pf-oob-auto"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> live</label>
          <button className="pf-btn sm" onClick={refresh}>Refresh</button>
        </div>
      </div>
      {err && <div className="pf-oob-error">Observability backend: {err}</div>}

      {view === "overview" && <OverviewView data={overview} />}
      {view === "sessions" && <SessionsView since={since} project={project} facets={facets} refreshKey={tick} initialSession={openSess} onOpenSession={goSession} onOpenTrace={goTrace} />}
      {view === "traces" && <TracesView since={since} project={project} facets={facets} refreshKey={tick} initialTrace={openTrace} onOpenTrace={goTrace} />}
      {view === "llm" && <LlmView data={llm} onOpenTrace={goTrace} />}
      {view === "ingestion" && <IngestionView status={status} scenarios={scenarios} onSync={sync} onClear={clear} busy={busy} />}
      {view === "findings" && <FindingsView refreshKey={tick} onOpenFinding={openFinding} />}
      {flyout && (
        <SessionFlyout sessionId={flyout.sessionId} initialSpanId={flyout.spanId} refreshKey={tick}
                       onClose={() => setFlyout(null)} onOpenTrace={goTrace} />
      )}
    </div>
  );
}
