import { useRef, useState } from "react";
import { introspect, parseSchema } from "../api";
import SchemaDiagram from "./SchemaDiagram";

interface Props {
  active: boolean;
  onSchema: (s: any) => void;
  onDisconnect: () => void;
  restored: any;
}

const DSN_PLACEHOLDER = "postgresql://user:pass@host:5432/db";

export default function DataConnector({ onSchema, onDisconnect, restored }: Props) {
  const [mode, setMode] = useState<"dsn" | "ddl" | "catalog">("dsn");
  const [dsn, setDsn] = useState("");
  const [dbSchema, setDbSchema] = useState("public");
  const [datasourceId, setDatasourceId] = useState("securebank");
  const [ddl, setDdl] = useState("");
  const [ddlFileName, setDdlFileName] = useState("");
  const [catalogJson, setCatalogJson] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<any>(restored?.catalog || null);
  const [resultId, setResultId] = useState<string>(restored?.datasourceId || "");
  const [dragOver, setDragOver] = useState(false);
  const sqlInputRef = useRef<HTMLInputElement>(null);

  function handleDisconnect() {
    setCatalog(null);
    setResultId("");
    setStatus("");
    setError("");
    onDisconnect();
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
    try {
      let result: any;
      if (mode === "ddl") {
        if (!ddl.trim()) throw new Error("Upload a .sql file or paste CREATE TABLE statements first");
        setStatus("Parsing DDL…");
        result = await parseSchema(ddl.trim(), datasourceId);
      } else if (mode === "dsn") {
        if (!dsn.trim()) throw new Error("Enter a connection string");
        setStatus("Introspecting schema…");
        result = await introspect(dsn.trim(), { datasourceId, schema: dbSchema || undefined });
      } else {
        if (!catalogJson.trim()) throw new Error("Paste catalog JSON");
        try { result = { catalog: JSON.parse(catalogJson), datasource_id: datasourceId }; }
        catch { throw new Error("Invalid JSON — check the catalog"); }
      }
      const cat = result.catalog || result;
      const dsId = result.datasource_id || datasourceId;
      const tbl = cat.tables?.length ?? 0;
      const intents = cat.suggestedIntents || [];
      setCatalog(cat);
      setResultId(dsId);
      onSchema({ catalog: cat, datasourceId: dsId, suggestedIntents: intents });
      setStatus(`Connected — ${tbl} table${tbl !== 1 ? "s" : ""} found`);
    } catch (e: any) {
      setError(String(e.message || e));
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
          or drop in a catalog JSON. The schema is cached in your browser.
        </p>

        {/* Mode tabs */}
        <div className="pf-tabs">
          <button className={`pf-tab ${mode === "dsn" ? "active" : ""}`} onClick={() => setMode("dsn")}>Live database</button>
          <button className={`pf-tab ${mode === "ddl" ? "active" : ""}`} onClick={() => setMode("ddl")}>SQL / DDL</button>
          <button className={`pf-tab ${mode === "catalog" ? "active" : ""}`} onClick={() => setMode("catalog")}>Upload catalog</button>
        </div>

        {mode === "dsn" && (
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

        {mode === "ddl" && (
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
                placeholder={"CREATE TABLE customers (\n  id SERIAL PRIMARY KEY,\n  email TEXT,\n  credit_limit NUMERIC\n);\n\nCREATE TABLE accounts (\n  id SERIAL PRIMARY KEY,\n  customer_id INTEGER REFERENCES customers(id),\n  balance NUMERIC\n);"}
                rows={10}
              />
            </label>
            <label>
              Datasource ID
              <input value={datasourceId} onChange={(e) => setDatasourceId(e.target.value)} />
            </label>
          </div>
        )}

        {mode === "catalog" && (
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
      </div>

      {catalog && (
        <div className="pf-panel">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Schema — <code style={{ fontSize: 15, fontWeight: 500 }}>{resultId}</code></h2>
            <button className="pf-btn" onClick={handleDisconnect} title="Clear the connected datasource (browser-only state)">
              Disconnect
            </button>
          </div>
          <div className="pf-readiness" style={{ marginBottom: 16 }}>
            <span className="pf-ready-item ok">{catalog.tables?.length ?? 0} tables</span>
            {(catalog.tables || []).flatMap((t: any) => t.columns || []).filter((c: any) => c.markers?.includes("SENSITIVE")).length > 0 && (
              <span className="pf-ready-item ok">sensitive columns detected</span>
            )}
            {catalog.suggestedIntents?.length > 0 && (
              <span className="pf-ready-item ok">{catalog.suggestedIntents.length} intent suggestions</span>
            )}
          </div>
          {catalog.suggestedIntents?.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <p className="pf-hint" style={{ marginBottom: 6 }}>Suggested intents from schema:</p>
              <div className="pf-intents">
                {catalog.suggestedIntents.map((i: string) => (
                  <span key={i} className="pf-chip">{i}</span>
                ))}
              </div>
            </div>
          )}
          <SchemaDiagram catalog={catalog} />
        </div>
      )}
    </main>
  );
}
