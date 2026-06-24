import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from "reactflow";
import dagre from "dagre";
import { getPolicy } from "../api";
import {
  type AppliedPolicy,
  DECISION_LABEL,
  DECISION_SEV,
  deriveKind,
  buildPolicyIndex,
} from "./policyIndex";

// ── Data Graph: live database relationships + clickable node detail ──────────
// Nodes/edges come from the connected catalog (App's `schema.catalog`); applied
// policies are layered — sensitivity markers + in-app Policy Studio rules, and
// the authoritative bound policy bundle when one has been published.

const CATEGORY_COLOR: Record<string, string> = {
  identity:    "#4f46e5",
  financial:   "#0891b2",
  transaction: "#059669",
  credit:      "#d97706",
  audit:       "#7c3aed",
  _default:    "#64748b",
};

interface Column {
  name: string;
  type: string;
  pk?: boolean;
  fk?: string;          // target table (FK)
  sensitive?: boolean;
  gov?: boolean;
  restriction?: string; // derived from applied policies touching this column
  pii?: { label: string; score: number }; // Presidio PII guess (name-based)
}

// Map "table.col" -> PII guess, from the Presidio analyzer.
type PiiIndex = Map<string, { label: string; score: number }>;

interface TableDef {
  id: string;
  label: string;
  category: string;
  icon: string;
  columns: Column[];
  tags: string[];
  policies: AppliedPolicy[];
}

// ── Catalog -> nodes/edges (dagre auto-layout, mirrors SchemaDiagram) ────────

const NODE_W = 230;
const HEADER_H = 42;
const ROW_H = 22;

function shortType(t: string) {
  return String(t || "").replace(/character varying/i, "varchar").replace(/\(.*\)/, "").trim().slice(0, 10);
}

function buildFromCatalog(catalog: any, policyIndex: Map<string, AppliedPolicy[]>, pii: PiiIndex) {
  const tables: any[] = catalog?.tables || [];
  // table.col -> target table (FK)
  const fkByCol: Record<string, string> = {};
  for (const t of tables) {
    for (const fk of t.foreign_keys || []) {
      fkByCol[`${t.name}.${(fk.from_columns || [])[0]}`] = fk.to_table;
    }
  }

  const defs: TableDef[] = tables.map((t: any) => {
    const policies = policyIndex.get(t.name) || [];
    // column -> short decision labels for the restriction hint
    const restrByCol = new Map<string, Set<string>>();
    for (const p of policies) {
      for (const c of p.columns) {
        if (!restrByCol.has(c)) restrByCol.set(c, new Set());
        restrByCol.get(c)!.add(DECISION_LABEL[p.decision || ""] || p.decision || "policy");
      }
    }
    const columns: Column[] = (t.columns || []).map((c: any) => {
      const markers: string[] = c.markers || [];
      const restr = restrByCol.get(String(c.name).toLowerCase());
      return {
        name: c.name,
        type: c.type,
        pk: !!c.is_primary_key,
        fk: fkByCol[`${t.name}.${c.name}`] || undefined,
        sensitive: markers.includes("SENSITIVE"),
        gov: markers.includes("GOVERNED"),
        restriction: restr && restr.size ? Array.from(restr).join(" · ") : undefined,
        pii: pii.get(`${t.name}.${c.name}`),
      };
    });
    const hasSens = columns.some((c) => c.sensitive);
    const hasGov = columns.some((c) => c.gov);
    const tags = [hasSens ? "Sensitive" : null, hasGov ? "Governed" : null].filter(Boolean) as string[];
    const { category, icon } = deriveKind(t.name);
    return { id: t.name, label: t.name, category, icon, columns, tags, policies };
  });

  const nodes = defs.map((t) => ({
    id: t.id,
    type: "graphTable",
    position: { x: 0, y: 0 },
    data: { table: t },
    selected: false,
    __h: HEADER_H + Math.min(t.columns.length, 8) * ROW_H + 30,
  }));

  const seen = new Set<string>();
  const edges: any[] = [];
  for (const t of tables) {
    for (const fk of t.foreign_keys || []) {
      const fromCol = (fk.from_columns || [])[0];
      const toCol = (fk.to_columns || [])[0];
      const key = `${t.name}.${fromCol}->${fk.to_table}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({
        id: key,
        source: t.name,
        target: fk.to_table,
        label: `${fromCol} → ${toCol}`,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "#6b7280" },
        style: { stroke: "#9ca3af", strokeWidth: 1.5 },
        labelStyle: { fill: "#6b7280", fontSize: 10 },
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
        labelBgPadding: [4, 3] as [number, number],
        labelBgBorderRadius: 4,
      });
    }
  }

  // dagre layout (LR), same approach as SchemaDiagram.
  const g = new (dagre as any).graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 50, ranksep: 110, marginx: 24, marginy: 24 });
  nodes.forEach((n: any) => g.setNode(n.id, { width: NODE_W, height: n.__h }));
  edges.forEach((e: any) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  nodes.forEach((n: any) => {
    const p = g.node(n.id);
    if (p) n.position = { x: p.x - NODE_W / 2, y: p.y - n.__h / 2 };
  });

  return { nodes, edges, defs };
}

// ── Graph node component ──────────────────────────────────────────────────────

function GraphTableNode({ data, selected }: { data: any; selected?: boolean }) {
  const t: TableDef = data.table;
  const color = CATEGORY_COLOR[t.category] || CATEGORY_COLOR._default;
  const sensitiveCount = t.columns.filter((c) => c.sensitive).length;
  const policyCount = t.policies.length;

  return (
    <div
      className="dg-node"
      style={{ "--node-color": color, boxShadow: selected ? `0 0 0 2px ${color}` : undefined } as any}
    >
      <Handle type="target" position={Position.Left}  style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top}   style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom}style={{ opacity: 0 }} />

      <div className="dg-node-head" style={{ background: color }}>
        <span className="dg-node-icon">{t.icon}</span>
        <span className="dg-node-title">{t.label}</span>
        <div className="dg-node-tags">
          {t.tags.slice(0, 2).map((tag) => (
            <span key={tag} className="dg-tag">{tag}</span>
          ))}
        </div>
      </div>

      <div className="dg-node-cols">
        {t.columns.slice(0, 8).map((col) => (
          <div key={col.name} className={`dg-col ${col.sensitive ? "sensitive" : ""} ${col.pk ? "pk" : ""}`}>
            <span className="dg-col-icon">
              {col.pk ? "🔑" : col.fk ? "↗" : col.sensitive ? "⚠" : col.gov ? "⚙" : "○"}
            </span>
            <span className="dg-col-name">{col.name}</span>
            {col.pii && <span className="dg-col-pii" title={`PII: ${col.pii.label}`}>PII</span>}
            <span className="dg-col-type">{shortType(col.type)}</span>
          </div>
        ))}
        {t.columns.length > 8 && (
          <div className="dg-col-more">+{t.columns.length - 8} more columns</div>
        )}
      </div>

      <div className="dg-node-foot">
        <span className="dg-stat" title="Applied policy rules">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
          {policyCount} rule{policyCount !== 1 ? "s" : ""}
        </span>
        {sensitiveCount > 0 && (
          <span className="dg-stat sensitive" title="Sensitive columns">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
            {sensitiveCount} sensitive
          </span>
        )}
        <span className="dg-stat rows">{t.columns.length} cols</span>
      </div>
    </div>
  );
}

const NODE_TYPES = { graphTable: GraphTableNode };

// ── Detail panel ──────────────────────────────────────────────────────────────

function DetailPanel({ table, onClose }: { table: TableDef; onClose: () => void }) {
  const color = CATEGORY_COLOR[table.category] || CATEGORY_COLOR._default;
  const sensitive = table.columns.filter((c) => c.sensitive);
  const fks = table.columns.filter((c) => c.fk);
  const piiCols = table.columns.filter((c) => c.pii);

  return (
    <div className="dg-detail">
      <div className="dg-detail-head" style={{ borderColor: color }}>
        <div>
          <div className="dg-detail-title" style={{ color }}>{table.icon} {table.label}</div>
          <div className="dg-detail-sub">{table.columns.length} columns · {table.policies.length} policies</div>
        </div>
        <button className="dg-detail-close" onClick={onClose}>×</button>
      </div>

      {table.tags.length > 0 && (
        <div className="dg-detail-tags">
          {table.tags.map((tag) => (
            <span key={tag} className="dg-detail-tag">{tag}</span>
          ))}
        </div>
      )}

      {/* All columns */}
      <div className="dg-detail-section">
        <div className="dg-detail-section-title">Columns</div>
        <table className="dg-col-table">
          <thead>
            <tr><th>Name</th><th>Type</th><th>Flags</th></tr>
          </thead>
          <tbody>
            {table.columns.map((col) => (
              <tr key={col.name} className={col.sensitive ? "sensitive-row" : ""}>
                <td className="dg-col-table-name">
                  {col.pk && <span className="dg-flag pk">PK</span>}
                  {col.fk && <span className="dg-flag fk">FK</span>}
                  {col.name}
                </td>
                <td className="dg-col-table-type">{col.type}</td>
                <td className="dg-flags-cell">
                  {col.sensitive && <span className="dg-flag sens">SENSITIVE</span>}
                  {col.gov && !col.sensitive && <span className="dg-flag gov">GOV</span>}
                  {col.pii && <span className="dg-flag pii" title={`${col.pii.label} · ${Math.round(col.pii.score * 100)}% confidence`}>PII: {col.pii.label}</span>}
                  {col.restriction && (
                    <span className="dg-restriction" title={col.restriction}>⚠ {col.restriction}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Relationships (foreign keys) */}
      {fks.length > 0 && (
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">Relationships</div>
          {fks.map((col) => (
            <div key={col.name} className="dg-fk-row">
              <span className="dg-fk-col">{col.name}</span>
              <span className="dg-fk-arrow">→</span>
              <span className="dg-fk-target">{col.fk}</span>
            </div>
          ))}
        </div>
      )}

      {/* Sensitive columns */}
      {sensitive.length > 0 && (
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">
            <span style={{ color: "var(--red)" }}>⚠</span> Sensitive Columns ({sensitive.length})
          </div>
          {sensitive.map((col) => (
            <div key={col.name} className="dg-sensitive-row">
              <span className="dg-sensitive-name">{col.name}</span>
              {col.restriction && <span className="dg-sensitive-note">{col.restriction}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Detected PII (Presidio, name-based guess) */}
      {piiCols.length > 0 && (
        <div className="dg-detail-section">
          <div className="dg-detail-section-title">
            <span style={{ color: "var(--amber, #d97706)" }}>◆</span> Detected PII ({piiCols.length})
          </div>
          {piiCols.map((col) => (
            <div key={col.name} className="dg-sensitive-row">
              <span className="dg-sensitive-name">{col.name}</span>
              <span className="dg-sensitive-note">{col.pii!.label} · {Math.round(col.pii!.score * 100)}%</span>
            </div>
          ))}
        </div>
      )}

      {/* Applied policy rules */}
      <div className="dg-detail-section">
        <div className="dg-detail-section-title">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
          Applied Policies ({table.policies.length})
        </div>
        {table.policies.length === 0 && (
          <div className="dg-policy-empty">No policy rules reference this table yet. Approve rules in Policy Studio.</div>
        )}
        {table.policies.map((p) => {
          const sev = DECISION_SEV[p.decision || ""] || "low";
          const pending = p.status !== "approved" && p.status !== "published";
          return (
            <div key={p.rule_key} className={`dg-policy-card sev-${sev}`} style={pending ? { opacity: 0.62, borderStyle: "dashed" } : undefined}>
              <div className="dg-policy-head">
                <span className={`dg-policy-badge sev-${sev}`}>{(DECISION_LABEL[p.decision || ""] || p.decision || "rule").toUpperCase()}</span>
                <span className="dg-policy-id">{p.status}{p.source === "bound" ? " · bound" : ""}</span>
              </div>
              <div className="dg-policy-title">{p.rule_key}</div>
              {p.message && <div className="dg-policy-desc">{p.message}</div>}
              {p.restricted_fields && p.restricted_fields.length > 0 && (
                <div className="dg-policy-desc">restricts: {p.restricted_fields.join(", ")}</div>
              )}
              {p.intents && p.intents.length > 0 && (
                <div className="dg-policy-desc" style={{ fontSize: 11, opacity: 0.8 }}>intents: {p.intents.join(", ")}</div>
              )}
              {p.roles && p.roles.length > 0 && (
                <div className="dg-policy-roles">
                  {p.roles.map((r) => <span key={r} className="dg-role-chip">{r}</span>)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Stats bar ─────────────────────────────────────────────────────────────────

function StatsBar({ defs, datasourceId, piiScanned }:
  { defs: TableDef[]; datasourceId?: string; piiScanned: boolean }) {
  const totalTables = defs.length;
  const totalCols = defs.reduce((s, t) => s + t.columns.length, 0);
  const sensitiveCols = defs.reduce((s, t) => s + t.columns.filter((c) => c.sensitive).length, 0);
  const totalPolicies = new Set(defs.flatMap((t) => t.policies.map((p) => p.rule_key))).size;
  const piiCols = defs.reduce((s, t) => s + t.columns.filter((c) => c.pii).length, 0);

  return (
    <div className="dg-stats-bar">
      <div className="dg-stat-item"><span className="dg-stat-value">{totalTables}</span><span className="dg-stat-label">Tables</span></div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item"><span className="dg-stat-value">{totalCols}</span><span className="dg-stat-label">Columns</span></div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item"><span className="dg-stat-value" style={{ color: "var(--red)" }}>{sensitiveCols}</span><span className="dg-stat-label">Sensitive</span></div>
      <div className="dg-stat-sep" />
      <div className="dg-stat-item"><span className="dg-stat-value" style={{ color: "var(--blue)" }}>{totalPolicies}</span><span className="dg-stat-label">Policy Rules</span></div>
      {piiScanned && (<>
        <div className="dg-stat-sep" />
        <div className="dg-stat-item"><span className="dg-stat-value" style={{ color: "var(--amber)" }}>{piiCols}</span><span className="dg-stat-label">PII</span></div>
      </>)}
      <div style={{ flex: 1 }} />
      {datasourceId && <span className="dg-source-badge">{datasourceId}</span>}
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="dg-legend">
      <div className="dg-legend-item"><span className="dg-legend-icon">🔑</span> Primary key</div>
      <div className="dg-legend-item"><span className="dg-legend-icon">↗</span> Foreign key</div>
      <div className="dg-legend-item"><span className="dg-legend-icon sens">⚠</span> Sensitive</div>
      <div className="dg-legend-item"><span className="dg-legend-icon">⚙</span> Governed</div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  catalog?: any;
  datasourceId?: string;
  rules?: any[];
  pii?: Record<string, { label: string; score: number }>;  // computed at connect (DataConnector)
}

export default function DataGraph({ catalog, datasourceId, rules = [], pii }: Props) {
  const [bound, setBound] = useState<any>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const piiMap = useMemo<PiiIndex>(() => new Map(Object.entries(pii || {})), [pii]);

  // Fetch the authoritative bound policy bundle for this datasource (best-effort).
  useEffect(() => {
    let live = true;
    setBound(null);
    if (!datasourceId) return;
    getPolicy(datasourceId)
      .then((r) => { if (live) setBound(r?.policy_bundle || null); })
      .catch(() => { if (live) setBound(null); });
    return () => { live = false; };
  }, [datasourceId]);

  const hasCatalog = !!(catalog && (catalog.tables?.length ?? 0) > 0);

  const policyIndex = useMemo(
    () => buildPolicyIndex(catalog, rules, bound),
    [catalog, rules, bound]
  );

  const built = useMemo(
    () => (hasCatalog ? buildFromCatalog(catalog, policyIndex, piiMap) : { nodes: [], edges: [], defs: [] as TableDef[] }),
    [catalog, policyIndex, piiMap, hasCatalog]
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(built.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(built.edges);

  useEffect(() => {
    setNodes(built.nodes);
    setEdges(built.edges);
    setSelectedId((prev) => (prev && built.defs.some((t) => t.id === prev) ? prev : null));
  }, [built, setNodes, setEdges]);

  const selectedTable = useMemo(
    () => (selectedId ? built.defs.find((t) => t.id === selectedId) ?? null : null),
    [selectedId, built]
  );

  const onNodeClick = useCallback((_: any, node: any) => {
    setSelectedId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const styledNodes = useMemo(
    () => nodes.map((n) => ({ ...n, selected: n.id === selectedId })),
    [nodes, selectedId]
  );

  if (!hasCatalog) {
    return (
      <div className="dg-shell">
        <div className="dg-empty-state">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" style={{ color: "var(--muted)", marginBottom: 14 }}>
            <ellipse cx="12" cy="5" rx="9" ry="3"/>
            <path d="M3 5v6c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
            <path d="M3 11v6c0 1.66 4.03 3 9 3s9-1.34 9-3v-6"/>
          </svg>
          <div style={{ fontWeight: 600, color: "var(--ink-soft)", marginBottom: 6, fontSize: 15 }}>No datasource connected</div>
          <div style={{ color: "var(--muted)", fontSize: 13, textAlign: "center", maxWidth: 360, lineHeight: 1.5 }}>
            Connect a datasource in the <strong>Data Connector</strong> tab to populate the graph with your
            tables, relationships, and applied policies.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dg-shell">
      <StatsBar defs={built.defs} datasourceId={datasourceId} piiScanned={pii != null} />

      <div className="dg-workspace">
        <div className="dg-canvas">
          <ReactFlow
            nodes={styledNodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.3}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#e4e7ed" gap={24} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
          <Legend />
        </div>

        {selectedTable ? (
          <DetailPanel table={selectedTable} onClose={() => setSelectedId(null)} />
        ) : (
          <div className="dg-empty-detail">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ color: "var(--muted)", marginBottom: 10 }}>
              <rect x="3" y="3" width="7" height="7" rx="1"/>
              <rect x="14" y="3" width="7" height="7" rx="1"/>
              <rect x="14" y="14" width="7" height="7" rx="1"/>
              <rect x="3" y="14" width="7" height="7" rx="1"/>
            </svg>
            <div style={{ fontWeight: 500, color: "var(--ink-soft)", marginBottom: 4 }}>Select a table</div>
            <div style={{ color: "var(--muted)", fontSize: 12, textAlign: "center", maxWidth: 160 }}>
              Click any node in the graph to see columns, relationships, and applied policies.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
