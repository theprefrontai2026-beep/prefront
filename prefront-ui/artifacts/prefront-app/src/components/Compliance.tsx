/*
 * Compliance — framework evidence over the verdicts eval-engine already
 * produced (compliance_design.md). Reads GET /eval/compliance: every control
 * of every selected framework pack resolved to one of six honest states —
 * evidenced / violated / indeterminate / no_evidence / unbound /
 * not_configured — plus the deployment's own regime bindings from its
 * overlay. Nothing is evaluated here and nothing is stored: it is a VIEW.
 *
 * Truthfulness: the bundled deployment is ungoverned, so every count is
 * shadow evidence ("would have") — the banner says so whenever the engine
 * reports mode != inline. "no_evidence" is never rendered as a pass.
 *
 * Domain-neutral: this file names no demo's tables, roles or fields. Column
 * names appear only inside the drafted overlay text, which comes from the
 * connected schema's PII scan and the semantic-layer suggester.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { DemoConfig } from "../demos";
import { suggestComplianceOverlay } from "../api";
import { getJSON, qs, num } from "./Observability";
import { findingHref, sessionHref, navTo } from "../routes";
import CopyLink from "./CopyLink";

type Counts = { satisfied: number; violated: number; indeterminate: number; sessions: number };
type Sample = {
  session_id: string; check_id: string; rule_id: string; status: string; effect: string; event_id: string;
  evidence_span_ids: string[]; evidence_excerpt: string; detail: string; evaluated_at: string;
};
type ControlRow = {
  id: string; title: string; control_class: string; data_class: string; basis: "checks" | "store";
  check_ids: string[]; runnable_check_ids: string[]; scoping: string; state: string;
  counts: Counts; samples: Sample[]; note: string; pack_note?: string;
};
type Summary = Record<string, number>;
type FrameworkReport = {
  framework: string; title: string; version: string; out_of_scope: string[]; summary: Summary; controls: ControlRow[];
};
type RegimeBinding = ControlRow & { policy_section: string; binding_note?: string; cited: Counts };
type RegimeReport = { regime: string; summary: Summary; bindings: RegimeBinding[] };
export type ComplianceReport = {
  configured: boolean;
  overlay: { path: string; deployment: string; policy_document: string; frameworks: string[];
             data_classes: Record<string, number>; unknown_frameworks: string[] };
  packs: { framework: string; title: string; version: string; controls: number }[];
  window: { since: number; verdict_rows: number; truncated: boolean };
  frameworks: FrameworkReport[];
  domain_regime: RegimeReport[];
  facts: {
    clickhouse_ok: boolean; rule_pack_configured: boolean; catalog_configured: boolean;
    retention: Record<string, string>; retention_days: number; engine_version: string; mode: string; shadow: boolean;
    worker: { polls: number; evaluated_total: number; last_error: string }; row_cap: number;
  };
  states: string[];
};

const WINDOWS: { label: string; secs: number }[] = [
  { label: "24 hours", secs: 86400 },
  { label: "7 days", secs: 604800 },
  { label: "30 days", secs: 2592000 },
  { label: "90 days", secs: 7776000 },
];

// Display order + copy for the six states. Wording is deliberately the
// design doc's: "no evidence" is a gap, not a pass.
const STATE_META: Record<string, { label: string; hint: string }> = {
  violated:       { label: "Violated",       hint: "at least one violated verdict in the window" },
  evidenced:      { label: "Evidenced",      hint: "satisfied verdicts, none violated" },
  indeterminate:  { label: "Indeterminate",  hint: "only indeterminate verdicts, or a store fact half-present" },
  no_evidence:    { label: "No evidence",    hint: "checks could run; nothing exercised them" },
  unbound:        { label: "Unbound",        hint: "the overlay binds no columns to this data class" },
  not_configured: { label: "Not configured", hint: "every check behind it belongs to a family with no artifact loaded" },
};
const STATE_ORDER = ["violated", "evidenced", "indeterminate", "no_evidence", "unbound", "not_configured"];

function StateBadge({ state }: { state: string }) {
  const m = STATE_META[state] ?? { label: state, hint: "" };
  return <span className={`pf-cmp-state ${state}`} title={m.hint}>{m.label}</span>;
}

function SummaryChips({ s }: { s: Summary }) {
  return (
    <span className="pf-cmp-chips">
      {STATE_ORDER.filter((k) => (s[k] ?? 0) > 0).map((k) => (
        <span key={k} className={`pf-cmp-chip ${k}`} title={STATE_META[k]?.hint}>{s[k]} {STATE_META[k]?.label.toLowerCase()}</span>
      ))}
    </span>
  );
}

function sampleHref(x: Sample): string {
  const span = x.evidence_span_ids?.[0] ?? null;
  return x.status === "violated" ? findingHref(x.session_id, x.event_id || null, span) : sessionHref(x.session_id);
}

function Evidence({ samples }: { samples: Sample[] }) {
  if (!samples.length) return <span className="pf-muted">—</span>;
  const [first, ...rest] = samples;
  return (
    <span className="pf-cmp-evidence">
      <a href={sampleHref(first)} onClick={(e) => { e.preventDefault(); navTo(sampleHref(first)); }}
         title={`${first.check_id} · ${first.status} · ${first.detail}`}>
        {first.check_id} <span className={`pf-cmp-dot ${first.status}`} /> {first.session_id.slice(0, 14)}
      </a>
      {rest.length > 0 && <span className="pf-muted"> +{rest.length}</span>}
    </span>
  );
}

function ControlTable({ rows, sectionCol }: { rows: (ControlRow & Partial<RegimeBinding>)[]; sectionCol?: boolean }) {
  return (
    <div className="pf-cmp-tablewrap">
      <table className="pf-cmp-table">
        <thead>
          <tr>
            {sectionCol ? <th>Policy §</th> : <th>Control</th>}
            <th>Class</th>
            <th>Data class</th>
            <th>State</th>
            <th className="num">Satisfied</th>
            <th className="num">Violated</th>
            <th className="num">Sessions</th>
            {sectionCol && <th className="num">Citing verdicts</th>}
            <th>Evidence</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={(r.policy_section ?? r.id) + i} className={`st-${r.state}`}>
              {sectionCol
                ? <td className="pf-cmp-id">§{r.policy_section}</td>
                : <td className="pf-cmp-id"><div>{r.title}</div><div className="pf-muted mono">{r.id}</div></td>}
              <td>
                <div className="mono">{r.control_class}</div>
                <div className="pf-muted small" title={r.check_ids.join(", ")}>
                  {r.basis === "store" ? "store fact" : `${r.runnable_check_ids.length}/${r.check_ids.length} checks · ${r.scoping}`}
                </div>
              </td>
              <td className="mono">{r.data_class || <span className="pf-muted">—</span>}</td>
              <td><StateBadge state={r.state} /></td>
              <td className="num">{num(r.counts.satisfied)}</td>
              <td className={`num ${r.counts.violated ? "bad" : ""}`}>{num(r.counts.violated)}</td>
              <td className="num">{num(r.counts.sessions)}</td>
              {sectionCol && <td className="num">{r.cited ? `${r.cited.satisfied + r.cited.violated}` : "0"}</td>}
              <td><Evidence samples={r.samples} /></td>
              <td className="pf-cmp-note">{[r.pack_note, r.binding_note, r.note].filter(Boolean).join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Compliance({ demo, active = true, schema }: { demo: DemoConfig; active?: boolean; schema?: any }) {
  const [since, setSince] = useState(604800);
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [onlyFramework, setOnlyFramework] = useState("");
  const [draft, setDraft] = useState<{ yaml: string; bound: number; unmapped: { column: string; entity: string }[] } | null>(null);
  const [drafting, setDrafting] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    getJSON<ComplianceReport>(`/eval/compliance${qs({ since })}`)
      .then((r) => { setReport(r); setError(""); })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [since]);

  useEffect(() => { if (active) load(); }, [active, load]);

  const frameworks = useMemo(
    () => (report?.frameworks ?? []).filter((f) => !onlyFramework || f.framework === onlyFramework),
    [report, onlyFramework],
  );
  const overall = useMemo(() => {
    const s: Summary = {};
    for (const f of report?.frameworks ?? []) for (const k of Object.keys(f.summary)) if (k !== "total") s[k] = (s[k] ?? 0) + f.summary[k];
    return s;
  }, [report]);

  // In-app JSON export of the whole report (span references + excerpts only —
  // the report never carries a payload, so neither does the file).
  const exportJSON = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), demo: demo.id, ...report }, null, 2)],
                          { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `compliance-evidence-${demo.id}-${since}s.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  // Draft a Layer B overlay from the connected schema's PII scan (DataConnector
  // stores it as "table.column" -> {label, score}; the analyser's entity id is
  // recoverable from the label). Candidate output — the user copies it into the
  // deployment's artifacts; nothing here publishes.
  const piiMap: Record<string, { label: string; score: number }> = schema?.pii || {};
  const piiCount = Object.keys(piiMap).length;
  const LABEL_TO_ENTITY: Record<string, string> = {
    "Email": "EMAIL_ADDRESS", "Phone": "PHONE_NUMBER", "SSN": "US_SSN", "Credit card": "CREDIT_CARD",
    "Bank account": "US_BANK_NUMBER", "Name": "PERSON", "Date of birth": "DATE_TIME", "IP address": "IP_ADDRESS",
    "Address": "LOCATION", "Driver license": "US_DRIVER_LICENSE", "Passport": "US_PASSPORT", "Demographic": "NRP",
  };
  const draftOverlay = async () => {
    setDrafting(true);
    try {
      const fields = Object.entries(piiMap).map(([k, v]) => {
        const [table, ...rest] = k.split(".");
        return { table, column: rest.join("."), entity: LABEL_TO_ENTITY[v.label] ?? v.label };
      });
      const r = await suggestComplianceOverlay({
        deployment: demo.id, policy_document: "", fields,
        frameworks: report?.overlay.frameworks?.length ? report.overlay.frameworks : ["gdpr", "soc2"],
      });
      setDraft(r);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally { setDrafting(false); }
  };

  const facts = report?.facts;
  const retention = facts?.retention ?? {};
  const ttlTables = Object.keys(retention);

  return (
    <div className="pf-cmp">
      <div className="pf-cmp-toolbar">
        <label className="pf-cmp-window">Window
          <select className="pf-tr-select" value={since} onChange={(e) => setSince(Number(e.target.value))}>
            {WINDOWS.map((w) => <option key={w.secs} value={w.secs}>{w.label}</option>)}
          </select>
        </label>
        <label className="pf-cmp-window">Framework
          <select className="pf-tr-select" value={onlyFramework} onChange={(e) => setOnlyFramework(e.target.value)}>
            <option value="">All selected</option>
            {(report?.frameworks ?? []).map((f) => <option key={f.framework} value={f.framework}>{f.title}</option>)}
          </select>
        </label>
        <button className="pf-btn sm" onClick={load} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
        <button className="pf-btn sm" onClick={exportJSON} disabled={!report}>Export evidence (JSON)</button>
        <span className="pf-cmp-spacer" />
        <CopyLink href="/compliance" label="Copy link" />
      </div>

      {error && <div className="pf-cmp-error">{error}</div>}

      {report && (
        <>
          {facts?.shadow && (
            <div className="pf-cmp-banner shadow">
              <strong>Shadow evidence.</strong> The evaluated deployment runs out of band: every count below is what
              Prefront <em>would have</em> decided before execution. Nothing here was enforced.
            </div>
          )}
          <div className={`pf-cmp-banner ${report.configured ? "ok" : "warn"}`}>
            {report.configured ? (
              <>
                <strong>Overlay:</strong> <span className="mono">{report.overlay.deployment || "(unnamed)"}</span>
                {" · "}frameworks <span className="mono">{report.overlay.frameworks.join(", ") || "(none selected)"}</span>
                {" · "}data classes{" "}
                {Object.entries(report.overlay.data_classes).map(([k, n]) => (
                  <span key={k} className={`pf-cmp-dc ${n ? "bound" : "unbound"}`} title={n ? `${n} column(s)` : "unbound"}>{k}{n ? ` ${n}` : ""}</span>
                ))}
                {report.overlay.unknown_frameworks.length > 0 && (
                  <span className="bad"> · unknown pack(s): {report.overlay.unknown_frameworks.join(", ")}</span>
                )}
                <span className="pf-muted"> · {report.overlay.path}</span>
              </>
            ) : (
              <>
                <strong>No compliance overlay configured</strong> — every shipped pack is reported and every data-class-keyed
                control is <em>unbound</em>. Point <span className="mono">EVAL_COMPLIANCE_OVERLAY_PATH</span> at the
                deployment's overlay, or draft one below from the connected schema's PII scan.
              </>
            )}
          </div>

          <div className="pf-cmp-kpis">
            {STATE_ORDER.map((k) => (
              <div key={k} className={`pf-cmp-kpi ${k}`} title={STATE_META[k].hint}>
                <div className="pf-cmp-kpi-value">{num(overall[k] ?? 0)}</div>
                <div className="pf-cmp-kpi-label">{STATE_META[k].label}</div>
              </div>
            ))}
            <div className="pf-cmp-kpi plain">
              <div className="pf-cmp-kpi-value">{num(report.window.verdict_rows)}{report.window.truncated ? "+" : ""}</div>
              <div className="pf-cmp-kpi-label">verdicts in window{report.window.truncated ? " (truncated)" : ""}</div>
            </div>
          </div>

          {frameworks.map((f) => (
            <section key={f.framework} className="pf-panel pf-cmp-fw">
              <div className="pf-cmp-fw-head">
                <h2>{f.title} <span className="pf-muted small">pack v{f.version} · {f.controls.length} controls</span></h2>
                <SummaryChips s={f.summary} />
              </div>
              <ControlTable rows={f.controls} />
              {f.out_of_scope.length > 0 && (
                <details className="pf-cmp-oos">
                  <summary>Out of scope for this report ({f.out_of_scope.length})</summary>
                  <ul>{f.out_of_scope.map((s, i) => <li key={i}>{s}</li>)}</ul>
                </details>
              )}
            </section>
          ))}

          {report.domain_regime.length > 0 && (
            <section className="pf-panel pf-cmp-fw">
              <div className="pf-cmp-fw-head">
                <h2>Deployment regime <span className="pf-muted small">from the overlay · sections of {report.overlay.policy_document || "the policy document"}</span></h2>
              </div>
              {report.domain_regime.map((r) => (
                <div key={r.regime} className="pf-cmp-regime">
                  <div className="pf-cmp-fw-head sub"><h3>{r.regime}</h3><SummaryChips s={r.summary} /></div>
                  <ControlTable rows={r.bindings} sectionCol />
                </div>
              ))}
              <p className="pf-hint">A section bound to a store-based class (retention, audit logging) with no fact behind it is a
                <em> stated obligation</em> — the roadmap, not a finding. "Citing verdicts" counts verdicts whose own policy citation names that section.</p>
            </section>
          )}

          <section className="pf-panel pf-cmp-fw">
            <div className="pf-cmp-fw-head"><h2>Store facts <span className="pf-muted small">what the report's store-based rows read</span></h2></div>
            <div className="pf-cmp-facts">
              <div>
                <h3>Retention (ClickHouse TTL as enforced)</h3>
                <ul>
                  {ttlTables.map((t) => (
                    <li key={t}><span className="mono">{t}</span>: {retention[t]
                      ? <span className="mono ok">{retention[t]}</span>
                      : <span className="bad">no TTL</span>}</li>
                  ))}
                </ul>
                <div className="pf-muted small">EVAL_RETENTION_DAYS={facts?.retention_days ?? 0}; set OOB_RETENTION_DAYS for <span className="mono">spans</span>.</div>
              </div>
              <div>
                <h3>Artifacts</h3>
                <ul>
                  <li>rule pack: {facts?.rule_pack_configured ? <span className="ok">loaded</span> : <span className="bad">not configured</span>} (Policy family)</li>
                  <li>intent catalog: {facts?.catalog_configured ? <span className="ok">loaded</span> : <span className="bad">not configured</span>} (Conformance family)</li>
                  <li>packs: {report.packs.map((p) => `${p.framework} v${p.version} (${p.controls})`).join(", ")}</li>
                  <li>engine {facts?.engine_version} · mode <span className="mono">{facts?.mode}</span> · worker polls {num(facts?.worker.polls)}, evaluated {num(facts?.worker.evaluated_total)}</li>
                </ul>
              </div>
            </div>
          </section>

          <section className="pf-panel pf-cmp-fw">
            <div className="pf-cmp-fw-head">
              <h2>Draft an overlay <span className="pf-muted small">Layer B, from the connected schema's PII scan</span></h2>
            </div>
            <p className="pf-hint">
              The Data Connector's PII scan flags columns by <em>name</em>. This drafts a candidate
              <span className="mono"> compliance_overlay.yaml</span> binding those columns to abstract data classes — deterministic, no LLM.
              Review it (a name-based guess is wrong in both directions), add the deployment's own regime, publish it beside the
              other artifacts, then point <span className="mono">EVAL_COMPLIANCE_OVERLAY_PATH</span> at it. Nothing here publishes.
            </p>
            <div className="pf-cmp-toolbar">
              <button className="pf-btn sm primary" onClick={draftOverlay} disabled={!piiCount || drafting}>
                {drafting ? "Drafting…" : piiCount ? `Draft from ${piiCount} PII field${piiCount === 1 ? "" : "s"}` : "No PII scan on the connected schema"}
              </button>
              {draft && (
                <>
                  <span className="pf-muted small">{draft.bound} binding{draft.bound === 1 ? "" : "s"}{draft.unmapped.length ? ` · ${draft.unmapped.length} unmapped` : ""}</span>
                  <button className="pf-btn sm" onClick={() => { navigator.clipboard?.writeText(draft.yaml).catch(() => {}); }}>Copy YAML</button>
                </>
              )}
            </div>
            {draft && <textarea className="pf-cmp-yaml" readOnly value={draft.yaml} rows={Math.min(30, draft.yaml.split("\n").length + 1)} />}
          </section>
        </>
      )}
    </div>
  );
}
