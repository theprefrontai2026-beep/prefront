/*
 * Decision-Intelligence Dashboard — the Prefront home screen.
 *
 * Answers one question: "Can I trust what my agents are doing right now?"
 *
 * Populated with the SecureBank demo's real governance vocabulary — intents
 * (view_users / initiate_transfer / decide_loan …), rules (ssn_manager_only,
 * transfer_requires_approval, $250k ceiling …), roles (Account Holder / Bank
 * Teller / Bank Manager), and the B1–B9 scenario personas (maria/sam/tom/
 * priya).
 *
 * The Live Decision Trace Feed is now wired to REAL data — it reads persisted
 * governance traces from the DB (GET /api/decisions; see useDecisionFeed) and
 * never re-runs the LLM on load. The remaining panels are still presentational
 * fixtures (below); swap each `const` for an API call as the backend summary
 * endpoints land.
 */

import { useDecisionFeed } from "../hooks/useDecisionFeed";
import type { FeedDecision, AgentStats, PolicyStat } from "../hooks/useDecisionFeed";

/* ── Live: Agent Activity (cumulative, never-reset totals) ───────────────── */

type StatTone = "green" | "red" | "amber";
type Stat = { label: string; value: string; tone?: StatTone };

const num = (n: number) => n.toLocaleString();

// Cumulative counters from GET /api/stats → the Agent Activity tiles.
// Zeros until the first decision is recorded (or while stats load).
function agentActivity(s: AgentStats | null): Stat[] {
  const z: AgentStats = s ?? {
    agentsActive: 0, total: 0, allowed: 0, masked: 0, blocked: 0, approval: 0, maskedFields: 0,
  };
  return [
    { label: "Agents Active", value: num(z.agentsActive) },
    { label: "Total Requests", value: num(z.total) },
    { label: "Successful", value: num(z.allowed + z.masked), tone: "green" },
    { label: "Blocked", value: num(z.blocked), tone: "red" },
    { label: "Approval Pending", value: num(z.approval), tone: "amber" },
    { label: "Sensitive Fields Masked", value: num(z.maskedFields) },
  ];
}

// Decision Outcomes: the four governed buckets as a share of all requests,
// from the same cumulative stats. Zeros (0%) until the first decision lands.
type OutcomeTone = "green" | "teal" | "red" | "amber";
function decisionOutcomes(s: AgentStats | null): { label: string; pct: number; tone: OutcomeTone }[] {
  const total = s?.total ?? 0;
  const pct = (n: number) => (total > 0 ? Math.round((n / total) * 100) : 0);
  return [
    { label: "Allowed", pct: pct(s?.allowed ?? 0), tone: "green" },
    { label: "Masked", pct: pct(s?.masked ?? 0), tone: "teal" },
    { label: "Blocked", pct: pct(s?.blocked ?? 0), tone: "red" },
    { label: "Approval Required", pct: pct(s?.approval ?? 0), tone: "amber" },
  ];
}

/* ── Fixtures (fudged SecureBank data) ───────────────────────────────────── */

// Only `transfer_requires_approval` routes to a human — transfers in
// ($10k, $250k] await a Bank Manager.
const APPROVALS = [
  { request: "Transfer $75,000 — acct 1042 → 5005", owner: "Bank Manager", waiting: "6m" },
  { request: "Transfer $42,500 — acct 1001 → 8810", owner: "Bank Manager", waiting: "24m" },
  { request: "Transfer $18,200 — acct 1002 → 3140", owner: "Bank Manager", waiting: "1h 12m" },
];

type Risk = "Critical" | "High" | "Medium" | "Low";
const INTENTS: { intent: string; executions: number; risk: Risk }[] = [
  { intent: "view_accounts", executions: 612, risk: "Low" },
  { intent: "view_account", executions: 284, risk: "Low" },
  { intent: "initiate_transfer", executions: 96, risk: "High" },
  { intent: "view_user", executions: 71, risk: "High" },
  { intent: "decide_loan", executions: 23, risk: "Medium" },
  { intent: "view_users", executions: 14, risk: "Critical" },
];

const PRECEDENTS: { title: string; children: string[] }[] = [
  { title: "transfer_requires_approval", children: ["$75,000 — acct 1042 (granted)", "$42,500 — acct 1001 (granted)", "$60,000 — acct 1002 (denied)"] },
  { title: "ssn_manager_only", children: ["view_user → Maria Lopez", "view_user → Sam Carter", "view_users → masked directory"] },
  { title: "loan_decision_manager_only", children: ["Loan #7001 — approved (priya)", "Loan #7002 — rejected (priya)"] },
];

/* ── Small presentational helpers ────────────────────────────────────────── */

function DecisionChip({ decision }: { decision: FeedDecision }) {
  const tone =
    decision === "BLOCKED" ? "red"
    : decision === "APPROVAL" ? "amber"
    : decision === "MASKED" ? "teal"
    : "green";
  return <span className={`pf-dash-chip ${tone}`}>{decision}</span>;
}

function RiskBadge({ risk }: { risk: Risk }) {
  const tone =
    risk === "Critical" ? "red" : risk === "High" ? "amber" : risk === "Medium" ? "teal" : "muted";
  return <span className={`pf-dash-risk ${tone}`}>{risk}</span>;
}

// block → red, mask → teal, approval → amber, allow → green.
function effectTone(effect: PolicyStat["effect"]): string {
  return effect === "block" ? "red"
    : effect === "mask" ? "teal"
    : effect === "approval" ? "amber"
    : "green";
}

/**
 * Policies Enforced — a cumulative leaderboard of every governance policy that
 * has ever fired, ranked by trigger count. The #1 is emphasized; each row's bar
 * width is proportional to the busiest policy, color-coded by effect.
 */
function PoliciesEnforced({ policies }: { policies: PolicyStat[] }) {
  const max = policies[0]?.count || 1;
  return (
    <section className="pf-panel">
      <div className="pf-dash-panel-head">
        <h2>Policies Enforced</h2>
        <span className="pf-dash-subtle">most-triggered · all-time</span>
      </div>
      {policies.length === 0 ? (
        <div className="pf-dash-feed-status">No policies triggered yet.</div>
      ) : (
        <div className="pf-dash-pol">
          {policies.map((p, i) => (
            <div key={p.policy} className={`pf-dash-pol-row${i === 0 ? " top" : ""}`}>
              <div className="pf-dash-pol-head">
                <span className="pf-dash-pol-name">{p.policy}</span>
                {i === 0 && <span className="pf-dash-pol-rank">most enforced</span>}
                {p.effect && <span className={`pf-dash-chip ${effectTone(p.effect)}`}>{p.effect}</span>}
                <span className="pf-dash-pol-count">{p.count.toLocaleString()}</span>
              </div>
              <div className="pf-dash-pol-track">
                <div
                  className={`pf-dash-pol-fill ${effectTone(p.effect)}`}
                  style={{ width: `${Math.max((p.count / max) * 100, 4)}%` }}
                />
              </div>
              {p.reason && <div className="pf-dash-pol-reason">{p.reason}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Dashboard ───────────────────────────────────────────────────────────── */

export default function Dashboard() {
  const feed = useDecisionFeed();
  return (
    <div className="pf-dash">
      {/* ── Agent Activity + Decision Outcomes ── */}
      <div className="pf-dash-row pf-dash-row-2">
        <section className="pf-panel">
          <h2>Agent Activity</h2>
          <div className="pf-dash-stat-grid">
            {agentActivity(feed.stats).map((s) => (
              <div key={s.label} className="pf-dash-stat">
                <div className={`pf-dash-stat-value ${s.tone ?? ""}`}>{s.value}</div>
                <div className="pf-dash-stat-label">{s.label}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="pf-panel">
          <h2>Decision Outcomes</h2>
          <div className="pf-dash-bars">
            {decisionOutcomes(feed.stats).map((o) => (
              <div key={o.label} className="pf-dash-bar-row">
                <div className="pf-dash-bar-label">{o.label}</div>
                <div className="pf-dash-bar-track">
                  <div className={`pf-dash-bar-fill ${o.tone}`} style={{ width: `${o.pct}%` }} />
                </div>
                <div className="pf-dash-bar-pct">{o.pct}%</div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* ── Pending Approvals + Live Decision Trace Feed ── */}
      <div className="pf-dash-row pf-dash-row-approvals">
        <section className="pf-panel">
          <div className="pf-dash-panel-head">
            <h2>Pending Approvals</h2>
            <button className="pf-dash-link" type="button">Review Queue →</button>
          </div>
          <table className="pf-dash-table">
            <thead>
              <tr><th>Request</th><th>Owner</th><th className="num">Waiting</th></tr>
            </thead>
            <tbody>
              {APPROVALS.map((a) => (
                <tr key={a.request}>
                  <td>{a.request}</td>
                  <td className="muted">{a.owner}</td>
                  <td className="num">{a.waiting}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="pf-panel">
          <div className="pf-dash-panel-head">
            <h2>Live Decision Trace Feed</h2>
            <div className="pf-dash-feed-actions">
              {feed.rows.length > 0 && !feed.populating && (
                <button className="pf-dash-link danger" type="button" onClick={feed.clear}>
                  Clear ✕
                </button>
              )}
              {feed.populating ? (
                <span className="pf-dash-live"><span className="pf-dash-live-dot" />running…</span>
              ) : feed.status === "error" ? (
                <button className="pf-dash-link" type="button" onClick={feed.reload}>Retry ↻</button>
              ) : (
                <button className="pf-dash-link" type="button" onClick={feed.reload} disabled={feed.status === "loading"}>
                  Refresh ↻
                </button>
              )}
            </div>
          </div>
          <div className="pf-dash-feed">
            {feed.rows.map((f) => (
              <div key={f.id} className="pf-dash-feed-row">
                <div className="pf-dash-feed-time">{f.time}</div>
                <div className="pf-dash-feed-body">
                  <div className="pf-dash-feed-top">
                    <DecisionChip decision={f.decision} />
                    <span className="pf-dash-feed-agent">{f.agent}</span>
                    <span className="pf-dash-feed-arrow">·</span>
                    <span className="pf-dash-feed-intent">{f.intent}</span>
                  </div>
                  <div className="pf-dash-feed-meta">
                    {f.reason} <span className="pf-dash-feed-source">— {f.source}</span>
                  </div>
                </div>
              </div>
            ))}
            {feed.status === "loading" && feed.rows.length === 0 && (
              <div className="pf-dash-feed-status">Loading persisted decision traces…</div>
            )}
            {feed.status === "error" && (
              <div className="pf-dash-feed-status error">
                Couldn’t load traces ({feed.error}). Is the api-server running?
              </div>
            )}
            {feed.status === "ready" && feed.rows.length === 0 && (
              <div className="pf-dash-feed-status">
                No decisions recorded yet. Run scenarios in the Runtime tab, or{" "}
                <button className="pf-dash-link" type="button" onClick={feed.populate} disabled={feed.populating}>
                  {feed.populating ? "populating…" : "populate from the demo →"}
                </button>
              </div>
            )}
          </div>
        </section>
      </div>

      {/* ── Intent Intelligence + Policy Activity ── */}
      <div className="pf-dash-row pf-dash-row-2">
        <section className="pf-panel">
          <h2>Most Used Intents</h2>
          <table className="pf-dash-table">
            <thead>
              <tr><th>Intent</th><th className="num">Executions</th><th>Risk</th></tr>
            </thead>
            <tbody>
              {INTENTS.map((it) => (
                <tr key={it.intent}>
                  <td>{it.intent}</td>
                  <td className="num">{it.executions}</td>
                  <td><RiskBadge risk={it.risk} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <PoliciesEnforced policies={feed.policies} />
      </div>

      {/* ── Context / Precedent Graph ── */}
      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Top Precedents Influencing Decisions</h2>
          <span className="pf-dash-subtle">Decision context graph</span>
        </div>
        <div className="pf-dash-tree-grid">
          {PRECEDENTS.map((p) => (
            <div key={p.title} className="pf-dash-tree">
              <div className="pf-dash-tree-root">{p.title}</div>
              <ul className="pf-dash-tree-children">
                {p.children.map((c) => (
                  <li key={c} className="pf-dash-tree-child">{c}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
