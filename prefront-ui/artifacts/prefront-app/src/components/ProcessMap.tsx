/*
 * The observed process map — what this system actually does, drawn.
 *
 * Every other surface on the Learned Intents page is a list, which is right
 * for reviewing one candidate and wrong for the question a reader asks first:
 * what does this system DO? That is a question about structure, and thirty
 * rows of sequences do not answer it.
 *
 * Nothing here is arranged for effect. A node exists because a tool was
 * called, an edge because that transition was observed, and both are sized by
 * how often. This is the corpus, drawn — which is the only reason it is worth
 * showing anyone: an impressive diagram of something inferred would be a
 * liability, and this one can be checked against the counts beside it.
 *
 * Layout is dagre LR through ReactFlow, the same idiom as DataGraph, so the
 * two graph surfaces in this app behave identically (pan, zoom, fit).
 */

import { useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background, Controls, Handle, MarkerType, Position, useEdgesState, useNodesState,
} from "reactflow";
import dagre from "dagre";
import type { DemoConfig } from "../demos";

type MapNode = {
  id: string; episodes: number; writes: boolean; starts: number; ends: number;
  roles: { value: string; episodes: number }[];
};
type MapEdge = { source: string; target: string; count: number };
type MapData = {
  nodes: MapNode[]; edges: MapEdge[]; episodes: number;
  pruned_edges: number; pruned_weight: number; max_edge: number; max_node: number;
};

const NODE_W = 190;
const NODE_H = 58;

function MapNodeBox({ data }: { data: any }) {
  return (
    <div className={`pf-pm-node${data.writes ? " writes" : ""}${data.entry ? " entry" : ""}`}
         title={`${data.episodes} episodes · starts ${data.starts} · ends ${data.ends}`
                + (data.roles.length ? `\n${data.roles.map((r: any) => `${r.value} ${r.episodes}`).join("\n")}` : "")}>
      <div className="pf-pm-name">{data.label}</div>
      <div className="pf-pm-meta">
        <span className="pf-pm-count">{data.episodes}</span>
        {/* Where operations BEGIN and END is most of what a reader wants and
            is invisible in a plain adjacency count, so both are on the node. */}
        {data.entry && <span className="pf-pm-tag">entry</span>}
        {data.writes && <span className="pf-pm-tag write">write</span>}
      </div>
      {/* Fill is the node's share of the busiest one — frequency legible
          without reading a single number. */}
      <div className="pf-pm-bar"><div style={{ width: `${data.share}%` }} /></div>
      {/* Invisible, but required: a custom node without handles gives edges
          nothing to attach to, so ReactFlow renders every node and silently
          draws no edges at all — a process map with no process in it. */}
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const NODE_TYPES = { pmNode: MapNodeBox };

function layout(d: MapData) {
  const g = new (dagre as any).graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 26, ranksep: 96, marginx: 20, marginy: 20 });
  d.nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  d.edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const nodes = d.nodes.map((n) => {
    const p = g.node(n.id);
    return {
      id: n.id, type: "pmNode",
      position: p ? { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } : { x: 0, y: 0 },
      data: {
        label: n.id, episodes: n.episodes, writes: n.writes, starts: n.starts,
        ends: n.ends, roles: n.roles,
        // "Where work begins" is a claim, so it needs a bar: a tool that
        // starts a third of the operations it appears in is an entry point;
        // one that starts a handful is just early sometimes.
        entry: n.episodes > 0 && n.starts / n.episodes > 0.5,
        share: d.max_node ? Math.round((n.episodes / d.max_node) * 100) : 0,
      },
    };
  });

  const edges = d.edges.map((e) => {
    const w = d.max_edge ? e.count / d.max_edge : 0;
    return {
      id: `${e.source}->${e.target}`, source: e.source, target: e.target,
      label: String(e.count), animated: false,
      // Width carries the weight; a uniform-width map draws a rare hop with
      // the same authority as the spine, which is how a process map turns
      // into a hairball that looks like insight.
      style: { strokeWidth: 1 + Math.round(w * 5), stroke: w > 0.4 ? "#0f766e" : "#94a3b8" },
      labelStyle: { fontSize: 10, fill: "#64748b" },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14,
                   color: w > 0.4 ? "#0f766e" : "#94a3b8" },
    };
  });
  return { nodes, edges };
}

export default function ProcessMap({ demo, days, active }: { demo: DemoConfig; days: number; active?: boolean }) {
  const [data, setData] = useState<MapData | null>(null);
  const [err, setErr] = useState("");
  const [minEdge, setMinEdge] = useState(3);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    fetch(`/eval/behavior/map?since=${days * 86400}&app=${encodeURIComponent(demo.id)}&min_edge=${minEdge}`)
      .then((r) => r.json())
      .then((j) => { if (alive) { setData(j); setErr(""); } })
      .catch((e) => alive && setErr(String(e?.message || e)));
    return () => { alive = false; };
  }, [demo.id, days, minEdge, active]);

  const laid = useMemo(() => (data && data.nodes.length ? layout(data) : null), [data]);
  useEffect(() => {
    if (laid) { setNodes(laid.nodes as any); setEdges(laid.edges as any); }
  }, [laid, setNodes, setEdges]);

  if (err) return <p className="pf-error">{err}</p>;
  if (!data) return <div className="pf-dash-feed-status">Loading the map…</div>;
  if (!data.nodes.length) {
    return <div className="pf-dash-feed-status">No tool calls in this window yet.</div>;
  }

  return (
    <>
      <div className="pf-pm-bar-row">
        <span className="pf-hint" style={{ margin: 0 }}>
          {data.nodes.length} tools · {data.edges.length} observed transitions · {data.episodes} operations
          {data.pruned_edges > 0 && (
            // Said out loud: a map that quietly drops its tail looks cleaner
            // than the system is, and the reader cannot tell.
            <> · {data.pruned_edges} rarer transition{data.pruned_edges === 1 ? "" : "s"} hidden
              ({data.pruned_weight} occurrence{data.pruned_weight === 1 ? "" : "s"})</>
          )}
        </span>
        <label className="pf-pm-min">
          hide transitions seen fewer than
          <input type="number" min={1} value={minEdge}
                 onChange={(e) => setMinEdge(Math.max(1, Number(e.target.value)))} />
          times
        </label>
      </div>
      <div className="pf-pm-canvas">
        <ReactFlow
          nodes={nodes} edges={edges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          fitView fitViewOptions={{ padding: 0.12 }}
          minZoom={0.2} maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} size={1} color="#e2e8f0" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="pf-pm-legend">
        <span><i className="pf-pm-sw entry" /> entry point — most operations start here</span>
        <span><i className="pf-pm-sw write" /> changes something</span>
        <span><i className="pf-pm-sw thick" /> thicker edge = more frequent transition</span>
      </div>
    </>
  );
}
