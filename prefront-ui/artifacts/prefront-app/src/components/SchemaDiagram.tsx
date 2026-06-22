import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
} from "reactflow";
import dagre from "dagre";

const NODE_W = 240;
const HEADER_H = 40;
const ROW_H = 24;

function TableNode({ data }: { data: any }) {
  return (
    <div className="pf-erd-node">
      <Handle type="target" position={Position.Left} className="pf-erd-handle" />
      <div className="pf-erd-node-head">{data.name}</div>
      <div className="pf-erd-node-cols">
        {data.columns.map((c: any) => (
          <div key={c.name} className={`pf-erd-col${c.is_primary_key ? " pk" : ""}`}>
            <span className="pf-erd-col-name">
              {c.is_primary_key ? "🔑 " : c.fk ? "↗ " : ""}
              {c.name}
            </span>
            <span className="pf-erd-col-meta">
              {c.markers?.includes("SENSITIVE") && <em className="m-sens">SENS</em>}
              {c.markers?.includes("GOVERNED") && <em className="m-gov">GOV</em>}
              {c.enum_values?.length > 0 && <em className="m-enum">enum</em>}
              <span className="pf-erd-col-type">{shortType(c.type)}</span>
            </span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} className="pf-erd-handle" />
    </div>
  );
}

const NODE_TYPES = { table: TableNode };

function shortType(t: string) {
  return String(t || "")
    .replace(/character varying/i, "varchar")
    .replace(/\(.*\)/, "")
    .trim()
    .slice(0, 12);
}

function buildGraph(catalog: any) {
  const tables = catalog.tables || [];
  const fkByTableCol: Record<string, string> = {};
  for (const t of tables) {
    for (const fk of t.foreign_keys || []) {
      fkByTableCol[`${t.name}.${fk.from_columns[0]}`] = fk.to_table;
    }
  }

  const nodes = tables.map((t: any) => {
    const columns = (t.columns || []).map((c: any) => ({
      ...c,
      fk: fkByTableCol[`${t.name}.${c.name}`] || null,
    }));
    const h = HEADER_H + columns.length * ROW_H + 8;
    return {
      id: t.name,
      type: "table",
      data: { name: t.name, columns },
      position: { x: 0, y: 0 },
      __w: NODE_W,
      __h: h,
    };
  });

  const seen = new Set<string>();
  const edges: any[] = [];
  for (const t of tables) {
    for (const fk of t.foreign_keys || []) {
      const key = `${t.name}.${fk.from_columns[0]}->${fk.to_table}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({
        id: key,
        source: t.name,
        target: fk.to_table,
        label: `${fk.from_columns[0]} → ${fk.to_columns[0]}`,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "#7d5a38" },
        style: { stroke: "#7d5a38", strokeWidth: 1.5 },
        labelStyle: { fill: "#443d34", fontSize: 11 },
        labelBgStyle: { fill: "#f5f2ec" },
      });
    }
  }

  const g = new (dagre as any).graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 44, ranksep: 100, marginx: 20, marginy: 20 });
  nodes.forEach((n: any) => g.setNode(n.id, { width: n.__w, height: n.__h }));
  edges.forEach((e: any) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  nodes.forEach((n: any) => {
    const p = g.node(n.id);
    n.position = { x: p.x - n.__w / 2, y: p.y - n.__h / 2 };
  });

  return { nodes, edges };
}

export default function SchemaDiagram({ catalog }: { catalog: any }) {
  const { nodes, edges } = useMemo(() => buildGraph(catalog), [catalog]);
  return (
    <div className="pf-erd">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#e4ddd1" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
