import { useMemo, useState } from "react";
import * as api from "../api";
import { parseKV } from "../util";

interface Props {
  rules: any[];
  domain: string;
  schema: any;
  metricsText: string;
  setMetricsText: (v: string) => void;
  callerScopeText: string;
  setCallerScopeText: (v: string) => void;
  intents: string;
  setIntents: (v: string) => void;
}

const TEMPLATE_TABS = ["pending", "approved", "rejected", "all"] as const;

export default function Semantic({
  rules, domain, schema, metricsText, setMetricsText,
  callerScopeText, setCallerScopeText, intents, setIntents,
}: Props) {
  const [tab, setTab] = useState<"build" | "dbt" | "templates" | "publish">("build");
  const [modelId, setModelId] = useState("semantic_model");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [buildResult, setBuildResult] = useState<any>(null);
  const [dbtInput, setDbtInput] = useState("");
  const [dbtBusy, setDbtBusy] = useState(false);
  const [dbtResult, setDbtResult] = useState<any>(null);
  const [dbtStatus, setDbtStatus] = useState("");
  const [dbtError, setDbtError] = useState("");
  const [templates, setTemplates] = useState<any[]>([]);
  const [tmplTab, setTmplTab] = useState<(typeof TEMPLATE_TABS)[number]>("pending");
  const [tmplBusy, setTmplBusy] = useState(false);
  const [tmplStatus, setTmplStatus] = useState("");
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishResult, setPublishResult] = useState<any>(null);
  const [publishPolicyBusy, setPublishPolicyBusy] = useState(false);
  const [publishPolicyResult, setPublishPolicyResult] = useState<any>(null);

  const metrics = useMemo(() => parseKV(metricsText), [metricsText]);
  const callerContext = useMemo(() => parseKV(callerScopeText), [callerScopeText]);
  const intentList = useMemo(() => intents.split(/[,\n]+/).map(s => s.trim()).filter(Boolean), [intents]);

  const catalog = schema?.catalog || null;
  const datasourceId = schema?.datasourceId || "ds_primary";

  const approvedRules = useMemo(() => rules.filter(r => r.review_status === "approved").map(r => r.rule), [rules]);

  const readiness: Array<[string, boolean, string]> = [
    ["Schema connected", !!catalog, "connect datasource in step 1"],
    ["Approved rules", approvedRules.length > 0, "approve at least one rule in step 2"],
    ["Intents defined", intentList.length > 0, "add intents below"],
  ];

  async function handleBuild() {
    setError(""); setStatus(""); setBusy(true); setBuildResult(null);
    try {
      const ddl = catalog?.ddl || null;
      setStatus("Building semantic interfaces…");
      const res = await api.buildInterfaces({
        rules: approvedRules,
        ddl,
        dsn: null,
        domain: domain || schema?.catalog?.domain || undefined,
        datasourceId,
        intents: intentList,
        metrics,
        callerContext,
        modelId,
      });
      setBuildResult(res);
      setStatus("Build complete");
      if (res.semantic_model_id) {
        await loadTemplates(res.semantic_model_id);
        setTab("templates");
      }
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function loadTemplates(mid?: string) {
    setTmplBusy(true);
    try {
      const res = await api.listTemplates(mid || buildResult?.semantic_model_id);
      const tmpl = Array.isArray(res) ? res : (res.templates || []);
      setTemplates(tmpl);
    } catch { /* empty */ } finally {
      setTmplBusy(false);
    }
  }

  async function handleApproveTemplate(id: string) {
    try {
      const res = await api.approveTemplate(id);
      setTemplates(prev => prev.map(t => t.template_id === id ? (res.template || { ...t, status: "approved" }) : t));
      setTmplStatus("Template approved");
    } catch (e: any) {
      setTmplStatus("Error: " + e.message);
    }
  }

  async function handleRejectTemplate(id: string) {
    try {
      const res = await api.rejectTemplate(id);
      setTemplates(prev => prev.map(t => t.template_id === id ? (res.template || { ...t, status: "rejected" }) : t));
      setTmplStatus("Template rejected");
    } catch (e: any) {
      setTmplStatus("Error: " + e.message);
    }
  }

  async function handlePublishTemplates() {
    setPublishBusy(true); setPublishResult(null);
    try {
      const res = await api.publishTemplates(buildResult?.semantic_model_id);
      setPublishResult(res);
    } catch (e: any) {
      setPublishResult({ error: String(e.message || e) });
    } finally {
      setPublishBusy(false);
    }
  }

  async function handlePublishPolicy() {
    if (!catalog) return;
    setPublishPolicyBusy(true); setPublishPolicyResult(null);
    try {
      const res = await api.publishPolicy({
        rules: approvedRules,
        ddl: catalog.ddl || null,
        domain: domain || undefined,
        datasourceId,
        metrics,
      });
      setPublishPolicyResult(res);
    } catch (e: any) {
      setPublishPolicyResult({ error: String(e.message || e) });
    } finally {
      setPublishPolicyBusy(false);
    }
  }

  async function handleDbtImport() {
    setDbtError(""); setDbtStatus(""); setDbtBusy(true); setDbtResult(null);
    try {
      const raw = dbtInput.trim();
      if (!raw) throw new Error("Paste dbt model YAML or JSON");
      let parsed: any;
      try { parsed = JSON.parse(raw); } catch { parsed = raw; }
      setDbtStatus("Importing dbt model…");
      const res = await api.importDbtModel({
        dbtModel: typeof parsed === "object" ? parsed : null,
        overlay: typeof parsed === "string" ? parsed : undefined,
        ddl: catalog?.ddl || null,
        domain: domain || undefined,
        modelId,
        datasourceId,
      });
      setDbtResult(res);
      setDbtStatus("Import complete");
      if (res.semantic_model_id) {
        await loadTemplates(res.semantic_model_id);
        setTab("templates");
      }
    } catch (e: any) {
      setDbtError(String(e.message || e));
    } finally {
      setDbtBusy(false);
    }
  }

  const filteredTemplates = useMemo(() =>
    tmplTab === "all" ? templates : templates.filter(t => t.status === tmplTab),
    [templates, tmplTab]);

  return (
    <main>
      {/* Readiness bar */}
      <div className="pf-panel" style={{ padding: "18px 28px", marginBottom: 0 }}>
        <div className="pf-readiness">
          {readiness.map(([label, ok, hint]) => (
            <div key={label} className={`pf-ready-item ${ok ? "ok" : "missing"}`} title={ok ? "" : `Missing: ${hint}`}>
              {label}
            </div>
          ))}
        </div>
      </div>

      {/* Config shared across tabs */}
      <div className="pf-panel">
        <h2><span className="pf-step-badge">2</span>Semantic layer configuration</h2>
        <div className="pf-fields">
          <label>
            Semantic model ID
            <input value={modelId} onChange={e => setModelId(e.target.value)} />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            Known intents (comma-separated)
            <input value={intents} onChange={e => setIntents(e.target.value)}
              placeholder="get_credit_score, get_account_balance, …" />
          </label>
          <label>
            Metrics (key = expression)
            <textarea value={metricsText} onChange={e => setMetricsText(e.target.value)} rows={4} />
          </label>
          <label>
            Caller scope template (key = field)
            <textarea value={callerScopeText} onChange={e => setCallerScopeText(e.target.value)} rows={4} />
          </label>
        </div>
      </div>

      {/* Tabs */}
      <div className="pf-panel" style={{ paddingTop: 0, paddingBottom: 0 }}>
        <div className="pf-sub-nav">
          <button className={`pf-sub-tab ${tab === "build" ? "active" : ""}`} onClick={() => setTab("build")}>Build interfaces</button>
          <button className={`pf-sub-tab ${tab === "dbt" ? "active" : ""}`} onClick={() => setTab("dbt")}>Import dbt model</button>
          <button className={`pf-sub-tab ${tab === "templates" ? "active" : ""}`} onClick={() => { setTab("templates"); loadTemplates(); }}>
            Intent templates {templates.filter(t => t.status === "pending").length > 0 &&
              <span className="pf-pill pending" style={{ marginLeft: 6 }}>{templates.filter(t => t.status === "pending").length}</span>}
          </button>
          <button className={`pf-sub-tab ${tab === "publish" ? "active" : ""}`} onClick={() => setTab("publish")}>Publish</button>
        </div>
      </div>

      {/* Build */}
      {tab === "build" && (
        <div className="pf-panel">
          <h2><span className="pf-step-badge">3</span>Build semantic interfaces</h2>
          <p className="pf-hint">
            Generate governed query templates for each intent based on {approvedRules.length} approved rule{approvedRules.length !== 1 ? "s" : ""}
            {" "}and {Object.keys(metrics).length} metric{Object.keys(metrics).length !== 1 ? "s" : ""}.
          </p>
          <div className="pf-publish-row">
            <button className="pf-btn primary" onClick={handleBuild} disabled={busy || !catalog || approvedRules.length === 0}>
              {busy ? "Building…" : "Build interfaces"}
            </button>
            {status && <span className="pf-status">✓ {status}</span>}
            {error && <span className="pf-error">{error}</span>}
          </div>
          {buildResult && (
            <div style={{ marginTop: 16 }}>
              <div className="pf-readiness">
                <span className="pf-ready-item ok">{buildResult.templates_generated ?? "?"} templates</span>
                {buildResult.semantic_model_id && <span className="pf-ready-item ok">model: {buildResult.semantic_model_id}</span>}
              </div>
              {buildResult.intents && (
                <div className="pf-intents" style={{ marginTop: 8 }}>
                  {buildResult.intents.map((i: string) => <span key={i} className="pf-chip">{i}</span>)}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* dbt import */}
      {tab === "dbt" && (
        <div className="pf-panel">
          <h2>Import dbt model</h2>
          <p className="pf-hint">Paste a dbt model YAML (or JSON) to generate a governed semantic overlay.</p>
          <textarea value={dbtInput} onChange={e => setDbtInput(e.target.value)} rows={12}
            placeholder={"models:\n  - name: customers\n    columns:\n      - name: credit_score\n        meta:\n          sensitive: true"} />
          <div className="pf-publish-row" style={{ marginTop: 12 }}>
            <button className="pf-btn primary" onClick={handleDbtImport} disabled={dbtBusy}>
              {dbtBusy ? "Importing…" : "Import"}
            </button>
            {dbtStatus && <span className="pf-status">✓ {dbtStatus}</span>}
            {dbtError && <span className="pf-error">{dbtError}</span>}
          </div>
          {dbtResult && (
            <pre className="pf-profile-json" style={{ marginTop: 12 }}>{JSON.stringify(dbtResult, null, 2)}</pre>
          )}
        </div>
      )}

      {/* Templates */}
      {tab === "templates" && (
        <div className="pf-panel">
          <h2>Intent templates</h2>
          <div className="pf-sub-nav" style={{ marginTop: 0, marginBottom: 16 }}>
            {TEMPLATE_TABS.map(t => (
              <button key={t} className={`pf-sub-tab ${tmplTab === t ? "active" : ""}`} onClick={() => setTmplTab(t)}>
                {t}
              </button>
            ))}
            <div className="pf-sub-nav-spacer" />
            <button className="pf-btn sm" onClick={() => loadTemplates()} disabled={tmplBusy}>
              {tmplBusy ? "Loading…" : "Refresh"}
            </button>
          </div>
          {tmplStatus && <div className="pf-status" style={{ marginBottom: 12 }}>{tmplStatus}</div>}
          {filteredTemplates.length === 0
            ? <p className="pf-hint">No templates in "{tmplTab}". Build interfaces first.</p>
            : filteredTemplates.map((tmpl: any) => <TemplateCard key={tmpl.template_id} tmpl={tmpl}
                onApprove={() => handleApproveTemplate(tmpl.template_id)}
                onReject={() => handleRejectTemplate(tmpl.template_id)} />)}
        </div>
      )}

      {/* Publish */}
      {tab === "publish" && (
        <div className="pf-panel">
          <h2>Publish</h2>
          <div className="pf-panel" style={{ background: "var(--paper)", border: "1px solid var(--line)" }}>
            <h3>Governed query templates</h3>
            <p className="pf-hint">
              Publishes approved intent templates to the semantic runtime.
              {" "}{templates.filter(t => t.status === "approved").length} approved out of {templates.length} templates.
            </p>
            <button className="pf-btn primary" onClick={handlePublishTemplates} disabled={publishBusy}>
              {publishBusy ? "Publishing…" : "Publish templates"}
            </button>
            {publishResult && (
              <div className="pf-publish-result">
                {publishResult.error ? <span className="pf-error">{publishResult.error}</span>
                  : <>✓ Published — model: <code>{publishResult.semantic_model_id}</code>
                    {publishResult.published_count !== undefined && <span> · {publishResult.published_count} templates</span>}
                  </>}
              </div>
            )}
          </div>
          <div className="pf-panel" style={{ marginTop: 16, background: "var(--paper)", border: "1px solid var(--line)" }}>
            <h3>Policy to semantic runtime</h3>
            <p className="pf-hint">
              Publishes the full policy (rules + schema) directly to the runtime policy store.
              {" "}{approvedRules.length} approved rule{approvedRules.length !== 1 ? "s" : ""}.
            </p>
            <button className="pf-btn primary" onClick={handlePublishPolicy} disabled={publishPolicyBusy || !catalog}>
              {publishPolicyBusy ? "Publishing…" : "Publish policy"}
            </button>
            {publishPolicyResult && (
              <div className="pf-publish-result">
                {publishPolicyResult.error ? <span className="pf-error">{publishPolicyResult.error}</span>
                  : <>✓ {publishPolicyResult.message || "Policy published"}</>}
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}

function TemplateCard({ tmpl, onApprove, onReject }: { tmpl: any; onApprove: () => void; onReject: () => void }) {
  const decided = tmpl.status === "approved" || tmpl.status === "rejected";
  const precheck = tmpl.precheck_sql || tmpl.query_template?.precheck_sql;
  const cmd = tmpl.command_template || tmpl.query_template?.command_template;
  return (
    <div className={`pf-rule-card status-${tmpl.status}`}>
      <div className="pf-rule-head">
        <div className="pf-rule-title">
          <code className="pf-rule-key">{tmpl.intent}</code>
          <span className={`pf-badge review-${tmpl.status}`}>{tmpl.status}</span>
        </div>
      </div>
      <div className="pf-rule-body">
        {tmpl.description && <p className="pf-message">{tmpl.description}</p>}
        {tmpl.parameters?.length > 0 && (
          <div><span className="pf-label">Parameters</span>
            {tmpl.parameters.map((p: string) => <span key={p} className="pf-chip" style={{ marginRight: 4 }}>{p}</span>)}
          </div>
        )}
        {precheck && (
          <>
            <div className="pf-phase-label">Precheck</div>
            <pre className="pf-sql">{precheck}</pre>
          </>
        )}
        {cmd && (
          <>
            <div className="pf-phase-label">Command template</div>
            {Array.isArray(cmd) ? cmd.map((c: any, i: number) => (
              <div key={i}>
                <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
                  <span className={`pf-badge write ${c.type === "delete" ? "delete" : ""}`}>{c.type}</span>
                </div>
                <pre className={`pf-sql cmd ${c.type === "delete" ? "cmd-delete" : ""}`}>{c.sql}</pre>
              </div>
            )) : <pre className="pf-sql cmd">{cmd}</pre>}
          </>
        )}
        {tmpl.applied_rules?.length > 0 && (
          <div>
            <span className="pf-label">Applied rules</span>
            <div className="pf-intents">
              {tmpl.applied_rules.map((r: string) => <span key={r} className="pf-chip">{r}</span>)}
            </div>
          </div>
        )}
      </div>
      <footer className="pf-rule-foot">
        <div />
        <div className="pf-actions">
          <button className="pf-btn approve sm" disabled={decided} onClick={onApprove}>Approve</button>
          <button className="pf-btn reject sm" disabled={decided} onClick={onReject}>Reject</button>
        </div>
      </footer>
    </div>
  );
}
