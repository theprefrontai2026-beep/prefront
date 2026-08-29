import { useRef, useState } from "react";
import { introspect, introspectMcp, parseSchema, resetDatasources, analyzePii } from "../api";
import type { DemoConfig } from "../demos";

type Progress = { phase: "schema" | "pii" | "done"; tables: number; pii: number; unit?: "table" | "tool" };

// Live progress for the connect → PII-scan pipeline.
function ConnectProgress({ p }: { p: Progress }) {
  const schemaDone = p.phase === "pii" || p.phase === "done";
  const piiActive = p.phase === "pii";
  const piiDone = p.phase === "done";
  const pct = p.phase === "schema" ? 40 : p.phase === "pii" ? 80 : 100;
  const unit = p.unit || "table";
  return (
    <div className="pf-progress">
      <div className="pf-progressbar">
        <div className={`pf-progressbar-fill ${piiDone ? "done" : ""}`} style={{ width: `${pct}%` }} />
      </div>
      <div className={`pf-progress-step ${schemaDone ? "done" : "active"}`}>
        <span className="pf-progress-icon">{schemaDone ? "✓" : <span className="pf-spin" />}</span>
        <span>{schemaDone ? `Schema read — ${p.tables} ${unit}${p.tables !== 1 ? "s" : ""}` : "Reading schema…"}</span>
      </div>
      <div className={`pf-progress-step ${piiDone ? "done" : piiActive ? "active" : "pending"}`}>
        <span className="pf-progress-icon">{piiDone ? "✓" : piiActive ? <span className="pf-spin" /> : "○"}</span>
        <span>
          {piiDone
            ? `PII scan complete — ${p.pii} PII field${p.pii !== 1 ? "s" : ""}`
            : piiActive ? "Scanning fields for PII…" : "Scan for PII"}
        </span>
      </div>
    </div>
  );
}

interface Props {
  active: boolean;
  demo: DemoConfig;
  onSchema: (s: any) => void;
  onDisconnect: () => void;
  restored: any;
}

const DSN_PLACEHOLDER = "postgresql://user:pass@host:5432/db";

export default function DataConnector({ demo, onSchema, onDisconnect, restored }: Props) {
  const [sourceType, setSourceType] = useState<"postgres" | "mcp">(
    restored?.sourceType === "mcp" ? "mcp" : "postgres"
  );
  // Prefilled as a real, editable value (not just a placeholder) so the field is
  // ready to Connect immediately for a demo that ships a default target — the
  // user can still clear/replace it before connecting.
  const [mcpServerUrl, setMcpServerUrl] = useState(restored?.mcpServerUrl || demo.defaultMcpServerUrl || "");
  const [mcpHeadersText, setMcpHeadersText] = useState("");
  const [mode, setMode] = useState<"dsn" | "ddl" | "catalog">("dsn");
  const [dsn, setDsn] = useState("");
  const [dbSchema, setDbSchema] = useState("public");
  const [datasourceId, setDatasourceId] = useState(restored?.datasourceId || demo.datasourceId);
  const [ddl, setDdl] = useState("");
  const [ddlFileName, setDdlFileName] = useState("");
  const [catalogJson, setCatalogJson] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<any>(restored?.catalog || null);
  const [resultId, setResultId] = useState<string>(restored?.datasourceId || "");
  const [pii, setPii] = useState<Record<string, { label: string; score: number }> | null>(restored?.pii || null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const sqlInputRef = useRef<HTMLInputElement>(null);

  async function handleDisconnect() {
    if (!window.confirm(
      "Disconnect and forget everything?\n\n" +
      "This clears the connected datasource and its generated query templates " +
      "on the server (the bundled demo baselines are kept), and " +
      "clears the schema cached in this browser. This cannot be undone."
    )) return;
    setError(""); setStatus(""); setBusy(true);
    try {
      const r = await resetDatasources();
      const n = (r?.cleared?.datasources ?? 0) + (r?.cleared?.query_templates ?? 0);
      setCatalog(null);
      setResultId("");
      setPii(null);
      setProgress(null);
      onDisconnect();
      setStatus(`Disconnected — forgot ${n} server record${n !== 1 ? "s" : ""}; browser cache cleared`);
    } catch (e: any) {
      // Server wipe failed — still clear the browser so the UI reflects "disconnected".
      setCatalog(null);
      setResultId("");
      onDisconnect();
      setError(`Browser cache cleared, but server reset failed: ${String(e.message || e)}`);
    } finally {
      setBusy(false);
    }
  }

  function readSqlFile(file: File) {
    if (!file.name.match(/\.(sql|ddl|txt)$/i)) {
      setError("Please select a .sql, .ddl, or .txt file");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      setDdl(text);
      setDdlFileName(file.name);
      setError("");
    };
    reader.readAsText(file);
  }

  function handleSqlFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) readSqlFile(file);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) readSqlFile(file);
  }

  async function handleConnect() {
    setError(""); setStatus(""); setBusy(true);
    const unit = sourceType === "mcp" ? "tool" : "table";
    setProgress({ phase: "schema", tables: 0, pii: 0, unit });
    try {
      let result: any;
      if (sourceType === "mcp") {
        if (!mcpServerUrl.trim()) throw new Error("Enter the MCP server's URL");
        let headers: Record<string, string> = {};
        if (mcpHeadersText.trim()) {
          try { headers = JSON.parse(mcpHeadersText); }
          catch { throw new Error("Invalid JSON — check the headers"); }
        }
        result = await introspectMcp(mcpServerUrl.trim(), headers, datasourceId);
      } else if (mode === "ddl") {
        if (!ddl.trim()) throw new Error("Upload a .sql file or paste CREATE TABLE statements first");
        result = await parseSchema(ddl.trim(), datasourceId);
      } else if (mode === "dsn") {
        if (!dsn.trim()) throw new Error("Enter a connection string");
        result = await introspect(dsn.trim(), { datasourceId, schema: dbSchema || undefined });
      } else {
        if (!catalogJson.trim()) throw new Error("Paste catalog JSON");
        try { result = { catalog: JSON.parse(catalogJson), datasource_id: datasourceId }; }
        catch { throw new Error("Invalid JSON — check the catalog"); }
      }
      const cat = result.catalog || result;
      const dsId = result.datasource_id || datasourceId;
      const tbl = cat.tables?.length ?? 0;
      const intents = cat.suggestedIntents || cat.suggested_intents || [];
      cat.suggestedIntents = intents;   // normalize snake_case from the API onto the shape this component renders

      // Auto-scan for PII as soon as the schema is in — no manual step.
      setProgress({ phase: "pii", tables: tbl, pii: 0, unit });
      const piiMap: Record<string, { label: string; score: number }> = {};
      try {
        const fields = (cat.tables || []).flatMap((t: any) =>
          (t.columns || []).map((c: any) => ({ table: t.name, column: c.name, type: c.type })));
        const r = await analyzePii(fields);
        for (const x of r?.results || []) piiMap[`${x.table}.${x.column}`] = { label: x.label, score: x.score };
      } catch { /* PII is best-effort — never block a connection on it */ }
      const piiCount = Object.keys(piiMap).length;

      setCatalog(cat);
      setResultId(dsId);
      setPii(piiMap);
      onSchema({
        catalog: cat, datasourceId: dsId, suggestedIntents: intents, pii: piiMap,
        sourceType,
        // The full per-tool records (descriptions, parameter detail, declared
        // outputs, annotations, raw schemas). The catalog projection is lossy by
        // design — one table per tool, one column per INPUT property — so Data
        // Graph renders MCP from these instead.
        ...(sourceType === "mcp"
          ? { mcpServerUrl: mcpServerUrl.trim(), mcpTools: result.mcp_tools || [] }
          : {}),
      });
      setProgress({ phase: "done", tables: tbl, pii: piiCount, unit });
    } catch (e: any) {
      setError(String(e.message || e));
      setProgress(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <div className="pf-panel">
        <h2><span className="pf-step-badge">1</span>Connect your datasource</h2>
        <p className="pf-hint">
          Point Prefront at a Postgres connection string, upload or paste a <code>.sql</code> DDL file,
          drop in a catalog JSON, or connect any API-based MCP server and learn its tools directly.
          The schema is cached in your browser.
        </p>

        {/* Source-type tabs */}
        <div className="pf-tabs">
          <button className={`pf-tab ${sourceType === "postgres" ? "active" : ""}`} onClick={() => setSourceType("postgres")}>Postgres</button>
          <button className={`pf-tab ${sourceType === "mcp" ? "active" : ""}`} onClick={() => setSourceType("mcp")}>MCP Server</button>
        </div>

        {sourceType === "mcp" && (
          <div className="pf-fields">
            <label style={{ gridColumn: "1 / -1" }}>
              MCP server URL
              <input
                value={mcpServerUrl}
                onChange={(e) => setMcpServerUrl(e.target.value)}
                placeholder="http://localhost:8102/sse"
                type="text"
              />
            </label>
            <label style={{ gridColumn: "1 / -1" }}>
              Headers (JSON, optional — auth tokens, identity)
              <textarea
                value={mcpHeadersText}
                onChange={(e) => setMcpHeadersText(e.target.value)}
                placeholder='{"X-Api-Key": "..."}'
                rows={3}
              />
            </label>
            <label>
              Datasource ID
              <input value={datasourceId} onChange={(e) => setDatasourceId(e.target.value)} />
            </label>
          </div>
        )}

        {/* Mode tabs (Postgres only) */}
        {sourceType === "postgres" && (
        <div className="pf-tabs">
          <button className={`pf-tab ${mode === "dsn" ? "active" : ""}`} onClick={() => setMode("dsn")}>Live database</button>
          <button className={`pf-tab ${mode === "ddl" ? "active" : ""}`} onClick={() => setMode("ddl")}>SQL / DDL</button>
          <button className={`pf-tab ${mode === "catalog" ? "active" : ""}`} onClick={() => setMode("catalog")}>Upload catalog</button>
        </div>
        )}

        {sourceType === "postgres" && mode === "dsn" && (
          <div className="pf-fields">
            <label style={{ gridColumn: "1 / -1" }}>
              Connection string
              <input
                value={dsn}
                onChange={(e) => setDsn(e.target.value)}
                placeholder={DSN_PLACEHOLDER}
                type="text"
              />
            </label>
            <label>
              Schema
              <input value={dbSchema} onChange={(e) => setDbSchema(e.target.value)} placeholder="public" />
            </label>
            <label>
              Datasource ID
              <input value={datasourceId} onChange={(e) => setDatasourceId(e.target.value)} />
            </label>
          </div>
        )}

        {sourceType === "postgres" && mode === "ddl" && (
          <div className="pf-fields">
            {/* Drop zone */}
            <div style={{ gridColumn: "1 / -1" }}>
              <div
                className={`pf-drop-zone ${dragOver ? "drag-over" : ""} ${ddlFileName ? "has-file" : ""}`}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => sqlInputRef.current?.click()}
              >
                <input
                  ref={sqlInputRef}
                  type="file"
                  accept=".sql,.ddl,.txt"
                  style={{ display: "none" }}
                  onChange={handleSqlFileChange}
                />
                {ddlFileName ? (
                  <div className="pf-drop-zone-file">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <span className="pf-drop-zone-filename">{ddlFileName}</span>
                    <button
                      className="pf-drop-zone-clear"
                      onClick={(e) => { e.stopPropagation(); setDdl(""); setDdlFileName(""); }}
                      title="Remove file"
                    >×</button>
                  </div>
                ) : (
                  <div className="pf-drop-zone-empty">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: "var(--blue)", marginBottom: 8 }}>
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="17 8 12 3 7 8"/>
                      <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                    <span>Drop a <strong>.sql</strong> file here, or <span className="pf-drop-zone-link">browse</span></span>
                    <span className="pf-drop-zone-sub">Accepts .sql · .ddl · .txt</span>
                  </div>
                )}
              </div>
            </div>

            {/* Textarea — shown always so they can also just paste */}
            <label style={{ gridColumn: "1 / -1" }}>
              {ddlFileName ? "Parsed content (editable)" : "Or paste CREATE TABLE statements"}
              <textarea
                value={ddl}
                onChange={(e) => { setDdl(e.target.value); if (!e.target.value) setDdlFileName(""); }}
                placeholder={demo.ddlPlaceholder}
                rows={10}
              />
            </label>
            <label>
              Datasource ID
              <input value={datasourceId} onChange={(e) => setDatasourceId(e.target.value)} />
            </label>
          </div>
        )}

        {sourceType === "postgres" && mode === "catalog" && (
          <div className="pf-fields">
            <label style={{ gridColumn: "1 / -1" }}>
              Catalog JSON
              <textarea
                value={catalogJson}
                onChange={(e) => setCatalogJson(e.target.value)}
                placeholder='{"tables": [...]}'
                rows={12}
              />
            </label>
            <label>
              Datasource ID
              <input value={datasourceId} onChange={(e) => setDatasourceId(e.target.value)} />
            </label>
          </div>
        )}

        <div className="pf-publish-row" style={{ marginTop: 4 }}>
          <button className="pf-btn primary" onClick={handleConnect} disabled={busy}>
            {busy ? "Connecting…" : "Connect"}
          </button>
          {status && <span className="pf-status">✓ {status}</span>}
          {error && <span className="pf-error">{error}</span>}
        </div>

        {progress && <ConnectProgress p={progress} />}
      </div>

      {catalog && (
        <div className="pf-panel">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Connected — <code style={{ fontSize: 15, fontWeight: 500 }}>{resultId}</code></h2>
            <button className="pf-btn reject" onClick={handleDisconnect} disabled={busy}
              title="Forget everything: clear this datasource + its query templates on the server, and the browser cache">
              {busy ? "Disconnecting…" : "Disconnect"}
            </button>
          </div>
          <div className="pf-readiness" style={{ marginBottom: 16 }}>
            <span className="pf-ready-item ok">
              {catalog.tables?.length ?? 0} {sourceType === "mcp" ? "tools" : "tables"}
            </span>
            {(catalog.tables || []).flatMap((t: any) => t.columns || []).filter((c: any) => c.markers?.includes("SENSITIVE")).length > 0 && (
              <span className="pf-ready-item ok">sensitive columns detected</span>
            )}
            {sourceType === "mcp" && (catalog.tables || []).filter((t: any) => t.mcp_destructive).length > 0 && (
              <span className="pf-ready-item ok">
                {(catalog.tables || []).filter((t: any) => t.mcp_destructive).length} destructive tool
                {(catalog.tables || []).filter((t: any) => t.mcp_destructive).length !== 1 ? "s" : ""}
              </span>
            )}
            {pii && Object.keys(pii).length > 0 && (
              <span className="pf-ready-item ok">{Object.keys(pii).length} PII fields</span>
            )}
            {catalog.suggestedIntents?.length > 0 && (
              <span className="pf-ready-item ok">{catalog.suggestedIntents.length} intent suggestions</span>
            )}
          </div>
          {catalog.suggestedIntents?.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <p className="pf-hint" style={{ marginBottom: 6 }}>
                {sourceType === "mcp" ? "Tools learned from the server — each becomes a governed intent:" : "Suggested intents from schema:"}
              </p>
              <div className="pf-intents">
                {catalog.suggestedIntents.map((i: string) => {
                  const destructive = sourceType === "mcp"
                    && (catalog.tables || []).find((t: any) => t.name === i)?.mcp_destructive;
                  return (
                    <span key={i} className={`pf-chip ${destructive ? "destructive" : ""}`}
                      title={destructive ? "Marked destructive — defaults to approval_required" : "Read-only — defaults to allow"}>
                      {i}{destructive ? " ⚠" : ""}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          <p className="pf-hint" style={{ marginBottom: 0 }}>
            View the full schema — {sourceType === "mcp"
              ? "every tool's parameters, declared outputs, behaviour annotations and raw JSON Schema"
              : "tables, relationships, sensitive columns"},
            {" "}and applied policies — in the <strong>Data Graph</strong> tab.
          </p>
        </div>
      )}
    </main>
  );
}
