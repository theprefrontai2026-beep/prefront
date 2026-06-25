/*
 * Decision-Intelligence Dashboard — the Prefront home screen.
 *
 * Answers one question: "Can I trust what my agents are doing right now?"
 *
 * Populated with the SecureBank demo's real governance vocabulary — intents
 * (view_users / initiate_transfer / decide_loan …), rules (ssn_manager_only,
 * transfer_requires_approval, $250k ceiling …), roles (Account Holder / Bank
 * Teller / Bank Manager), and the B1–B9 scenario personas (maria/sam/tom/
 * priya). v1 is presentational with hardcoded fixtures (below); swap each
 * `const` for an API call (/api/audit + governance summary) when the backend
 * lands — the component reads only from these shapes, so wiring is mechanical.
 */

/* ── Fixtures (fudged SecureBank data) ───────────────────────────────────── */

const HEALTH = {
  score: 98.1,
  label: "Excellent",
  deltaPct: 1.4, // week over week
  approvalsWaiting: 3, // transfers > $10k awaiting Bank Manager
  blockedToday: 17,
  criticalExecuted: 1, // a customer-directory read by a Bank Manager
};

const AGENT_ACTIVITY: { label: string; value: string; tone?: "green" | "red" | "amber" }[] = [
  { label: "Agents Active", value: "5" },
  { label: "Requests Today", value: "1,284" },
  { label: "Successful", value: "1,196", tone: "green" },
  { label: "Blocked", value: "58", tone: "red" },
  { label: "Approval Pending", value: "21", tone: "amber" },
  { label: "Sensitive Fields Masked", value: "63" },
];

type OutcomeTone = "green" | "red" | "amber" | "purple";
const OUTCOMES: { label: string; pct: number; tone: OutcomeTone }[] = [
  { label: "Allowed", pct: 84, tone: "green" },
  { label: "Blocked", pct: 9, tone: "red" },
  { label: "Approval Required", pct: 6, tone: "amber" },
  { label: "Overridden", pct: 1, tone: "purple" },
];

// Only `transfer_requires_approval` routes to a human — transfers in
// ($10k, $250k] await a Bank Manager.
const APPROVALS = [
  { request: "Transfer $75,000 — acct 1042 → 5005", owner: "Bank Manager", waiting: "6m" },
  { request: "Transfer $42,500 — acct 1001 → 8810", owner: "Bank Manager", waiting: "24m" },
  { request: "Transfer $18,200 — acct 1002 → 3140", owner: "Bank Manager", waiting: "1h 12m" },
];

type Decision = "BLOCKED" | "APPROVED" | "ALLOWED";
const FEED: {
  time: string;
  decision: Decision;
  agent: string;
  intent: string;
  reason: string;
  source: string;
}[] = [
  { time: "10:21", decision: "BLOCKED", agent: "Teller Copilot · tom", intent: "view_users", reason: "Bulk customer export rejected — no over-broad reads", source: "validator · executable" },
  { time: "10:19", decision: "ALLOWED", agent: "Teller Copilot · tom", intent: "view_user (Maria Lopez)", reason: "ssn_manager_only → ssn masked for Bank Teller", source: "policy.yaml · data_access" },
  { time: "10:17", decision: "APPROVED", agent: "Teller Copilot · tom", intent: "initiate_transfer $75,000", reason: "transfer_requires_approval → granted by Bank Manager (priya)", source: "policy.yaml · approval_threshold" },
  { time: "10:14", decision: "BLOCKED", agent: "Teller Copilot · tom", intent: "initiate_transfer $300,000", reason: "transfer_ceiling — $250k hard limit exceeded", source: "policy.yaml · restriction" },
  { time: "10:09", decision: "BLOCKED", agent: "Customer Assistant · maria", intent: "view_users", reason: "view_users_account_holder_block — role not permitted", source: "policy.yaml · restriction" },
  { time: "10:04", decision: "BLOCKED", agent: "Customer Assistant · maria", intent: "view_account 1042", reason: "own-data-only — account not owned by caller", source: "query_templates · scoping" },
  { time: "09:58", decision: "BLOCKED", agent: "Teller Copilot · tom", intent: "decide_loan 7001", reason: "loan_decision_manager_only — Bank Manager required", source: "policy.yaml · restriction" },
  { time: "09:52", decision: "ALLOWED", agent: "Customer Assistant · maria", intent: "view_accounts", reason: "scoped to caller_user_id — own accounts only", source: "query_templates · read" },
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

const POLICIES = [
  { policy: "ssn_manager_only — SSN masked off-role", count: 63 },
  { policy: "view_users_account_holder_block", count: 19 },
  { policy: "transfer_requires_approval (> $10k)", count: 11 },
  { policy: "loan_decision_manager_only", count: 8 },
  { policy: "transfer_ceiling (> $250k)", count: 4 },
  { policy: "transfer_from_suspended_account", count: 3 },
];

const PRECEDENTS: { title: string; children: string[] }[] = [
  { title: "transfer_requires_approval", children: ["$75,000 — acct 1042 (granted)", "$42,500 — acct 1001 (granted)", "$60,000 — acct 1002 (denied)"] },
  { title: "ssn_manager_only", children: ["view_user → Maria Lopez", "view_user → Sam Carter", "view_users → masked directory"] },
  { title: "loan_decision_manager_only", children: ["Loan #7001 — approved (priya)", "Loan #7002 — rejected (priya)"] },
];

/* ── Small presentational helpers ────────────────────────────────────────── */

function DecisionChip({ decision }: { decision: Decision }) {
  const tone = decision === "BLOCKED" ? "red" : decision === "APPROVED" ? "amber" : "green";
  return <span className={`pf-dash-chip ${tone}`}>{decision}</span>;
}

function RiskBadge({ risk }: { risk: Risk }) {
  const tone =
    risk === "Critical" ? "red" : risk === "High" ? "amber" : risk === "Medium" ? "teal" : "muted";
  return <span className={`pf-dash-risk ${tone}`}>{risk}</span>;
}

/* ── Dashboard ───────────────────────────────────────────────────────────── */

export default function Dashboard() {
  return (
    <div className="pf-dash">
      {/* ── Hero: Governance Health ── */}
      <section className="pf-panel pf-dash-hero">
        <div className="pf-dash-hero-score">
          <div className="pf-dash-hero-label">Governance Health</div>
          <div className="pf-dash-hero-value">{HEALTH.score}%</div>
          <div className="pf-dash-hero-sub">
            <span className="pf-dash-hero-grade">{HEALTH.label}</span>
            <span className="pf-dash-hero-delta">↑ {HEALTH.deltaPct}% this week</span>
          </div>
        </div>
        <div className="pf-dash-hero-counters">
          <div className="pf-dash-counter">
            <div className="pf-dash-counter-value amber">{HEALTH.approvalsWaiting}</div>
            <div className="pf-dash-counter-label">Approval Requests Waiting</div>
          </div>
          <div className="pf-dash-counter">
            <div className="pf-dash-counter-value red">{HEALTH.blockedToday}</div>
            <div className="pf-dash-counter-label">Policy Violations Blocked Today</div>
          </div>
          <div className="pf-dash-counter">
            <div className="pf-dash-counter-value">{HEALTH.criticalExecuted}</div>
            <div className="pf-dash-counter-label">Critical-Risk Intents Executed</div>
          </div>
        </div>
      </section>

      {/* ── Agent Activity + Decision Outcomes ── */}
      <div className="pf-dash-row pf-dash-row-2">
        <section className="pf-panel">
          <h2>Agent Activity</h2>
          <div className="pf-dash-stat-grid">
            {AGENT_ACTIVITY.map((s) => (
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
            {OUTCOMES.map((o) => (
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
            <span className="pf-dash-live"><span className="pf-dash-live-dot" />live</span>
          </div>
          <div className="pf-dash-feed">
            {FEED.map((f, i) => (
              <div key={i} className="pf-dash-feed-row">
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

        <section className="pf-panel">
          <h2>Policies Triggered Today</h2>
          <table className="pf-dash-table">
            <thead>
              <tr><th>Policy</th><th className="num">Count</th></tr>
            </thead>
            <tbody>
              {POLICIES.map((p) => (
                <tr key={p.policy}>
                  <td>{p.policy}</td>
                  <td className="num">{p.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
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
