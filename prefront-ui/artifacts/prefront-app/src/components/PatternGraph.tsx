/*
 * One call pattern, drawn as its own small graph.
 *
 * The merged process map shows every pattern at once, which answers "what does
 * this system do" and actively obstructs "should I approve THIS". A reviewer
 * deciding about one pattern should never have to leave its row, and should
 * never have to find it inside a diagram of everything else.
 *
 * So each pattern carries its own graph: the same nodes and arrows as the big
 * map, restricted to this pattern and annotated with what a decision turns on
 * — which step changes something, which identifier the whole run is scoped to,
 * and where the run terminates.
 *
 * Inline SVG rather than the graph library: a pattern is a fixed left-to-right
 * chain with no layout to solve, and it has to render inside a list row at a
 * readable size. ReactFlow would bring a canvas, a viewport and a pan handler
 * to draw six boxes in a line.
 */

const BOX_W = 152;
const BOX_H = 40;
const GAP = 38;

export default function PatternGraph({ steps, closedBy, subject, episodes, roles }: {
  steps: string[]; closedBy?: string; subject?: string; episodes: number;
  roles: { value: string; episodes: number }[];
}) {
  if (!steps.length) return null;
  const w = steps.length * BOX_W + (steps.length - 1) * GAP + 24;
  const h = BOX_H + 54;
  const y = 30;

  return (
    <svg className="pf-pg" viewBox={`0 0 ${w} ${h}`} width="100%" style={{ maxWidth: w }}
         role="img" aria-label={`${steps.join(" then ")}, ${episodes} times`}>
      <defs>
        <marker id="pg-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
        </marker>
      </defs>

      {/* The scope line. A run is one operation on ONE subject, and that is a
          fact about the whole pattern rather than any step in it — so it is
          drawn as a brace over the run, not repeated on every box. */}
      {subject && (
        <>
          <line x1="12" y1="16" x2={w - 12} y2="16" stroke="#cbd5e1" strokeWidth="1" strokeDasharray="3 3" />
          <text x={w / 2} y="11" textAnchor="middle" className="pf-pg-scope">one {subject} per run</text>
        </>
      )}

      {steps.map((t, i) => {
        const x = 12 + i * (BOX_W + GAP);
        const isWrite = i === steps.length - 1 && !!closedBy;
        return (
          <g key={`${t}-${i}`}>
            {i > 0 && (
              <line x1={x - GAP} y1={y + BOX_H / 2} x2={x - 6} y2={y + BOX_H / 2}
                    stroke="#94a3b8" strokeWidth="1.5" markerEnd="url(#pg-arrow)" />
            )}
            <rect x={x} y={y} width={BOX_W} height={BOX_H} rx="6"
                  className={isWrite ? "pf-pg-box write" : "pf-pg-box"} />
            <text x={x + BOX_W / 2} y={y + 18} textAnchor="middle" className="pf-pg-name">{t}</text>
            <text x={x + BOX_W / 2} y={y + 31} textAnchor="middle" className="pf-pg-sub">
              {isWrite ? "changes data" : "reads"}
            </text>
          </g>
        );
      })}

      <text x="12" y={h - 6} className="pf-pg-foot">
        {episodes}× · {roles.map((r) => `${r.value} ${r.episodes}`).join(" · ") || "no role recorded"}
      </text>
    </svg>
  );
}
