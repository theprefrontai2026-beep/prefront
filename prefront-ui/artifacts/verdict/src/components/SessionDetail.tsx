/*
 * SessionDetail — the ordered step stream (turn -> LLM -> tool calls -> answer)
 * for one session.id, read from oob-ingest's OOB (out-of-band) store. Extracted
 * from the main Prefront app's Observability.tsx, which bundles this together
 * with the Overview/Traces/LLM/Ingestion views Verdict doesn't need; only this
 * component and its private helpers came along. Behavior is unchanged except
 * `onOpenTrace` is optional here — Verdict has no trace-waterfall view to jump
 * to, so the "trace <id>" buttons simply don't render without a handler.
 */
import { useEffect, useMemo, useState } from "react";

type Span = {
  trace_id: string; span_id: string; parent_span_id: string; name: string; kind: string; otel_kind: string;
  service: string; project: string; source: string; start_time: string; end_time: string; duration_ms: number;
  status: string; status_message: string; attributes: Record<string, string>; events: string;
  input_value: string; output_value: string; llm_model: string; llm_provider: string;
  tokens_prompt: number; tokens_completion: number; tokens_total: number;
  scenario_id: string; tool_name: string;
  session_id?: string; user_id?: string; user_role?: string; channel?: string; intent_name?: string;
};

type EvalVerdict = {
  // `family_label` is eval-engine's display name for `family` (Policy /
  // Integrity / Conformance). Carried for shape parity with the API; this app
  // renders check_id, not the family. NB SessionRunner's FAMILY_TONE is the
  // demo SCENARIO group (F1/F2/F3/POP/BASE) - a different vocabulary.
  session_id: string; check_id: string; family: string; family_label?: string; rule_id: string;
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
const STATUS_TONE: Record<string, string> = { violated: "red", satisfied: "green", indeterminate: "amber" };

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

const num = (n: number | undefined | null) => (n ?? 0).toLocaleString();
const ms = (n: number | undefined | null) => {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${Math.round(v)}ms`;
};
const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
function pretty(v: string): string {
  if (!v) return "";
  try { return JSON.stringify(JSON.parse(v), null, 2); } catch { return v; }
}

function KindBadge({ kind }: { kind: string }) {
  const k = kind || "SPAN";
  return <span className={`pf-oob-kind ${kindTone(k)}`}>{k}</span>;
}

function Empty({ text }: { text: string }) {
  return <div className="pf-oob-empty">{text}</div>;
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

export function SessionDetail({ sessionId, refreshKey, onClose, onOpenTrace }: {
  sessionId: string; refreshKey: number; onClose: () => void; onOpenTrace?: (id: string) => void;
}) {
  const [spans, setSpans] = useState<Span[]>([]);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [verdicts, setVerdicts] = useState<EvalVerdict[]>([]);
  const [tags, setTags] = useState<ConformanceTag[]>([]);

  useEffect(() => { setSpans([]); setSelected(null); setErr(""); setVerdicts([]); setTags([]); }, [sessionId]);
  // A session handed over right after "Run" may not be ingested yet (the
  // OTLP tap batches for a few seconds; the orchestrator's root arrives via
  // the Phoenix poll). Re-fetch on every refresh tick until it lands.
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
          {onOpenTrace && traces.map((t) => <button key={t} className="pf-btn sm" onClick={() => onOpenTrace(t)}>trace {short(t)}</button>)}
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
