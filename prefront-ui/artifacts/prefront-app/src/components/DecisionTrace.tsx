// DecisionTrace — renders the deterministic governance trace the runtime returns
// on every governed call (the `governance` object on an MCP tool response). It is
// the audit "product": what was asked, by whom, which rules fired, what was
// decided, what executed. Pure presentation of the existing trace — no enrichment.

type Rule = {
  rule_key: string;
  decision?: string;
  fired?: boolean;
  missing?: string[];
};

export type GovernanceTrace = {
  trace_id?: string;
  ts?: string;
  tool?: string;
  matched_intent?: string;
  template_id?: string | null;
  caller?: Record<string, any> | null;
  parameters?: Record<string, any>;
  decision?: string; // "allowed" | "blocked" | "approval_required"
  reasons?: string[];
  approver_roles?: string[];
  masked_fields?: string[];
  rules_evaluated?: Rule[];
  execution_status?: string;
};

function decisionVerdictClass(decision = "", masked = false) {
  if (decision === "blocked") return "v-block";
  if (decision === "approval_required") return "v-appr";
  if (masked) return "v-mask";
  return "v-allow";
}

function decisionLabel(decision = "", masked = false) {
  if (decision === "blocked") return "BLOCK";
  if (decision === "approval_required") return "APPROVAL REQUIRED";
  if (masked) return "ALLOW · MASKED";
  return "ALLOW";
}

const EXEC_LABEL: Record<string, string> = {
  executed: "SQL executed",
  write_executed: "write executed",
  write_dry_run: "write simulated (dry-run)",
  not_executed: "not executed",
  error: "execution error",
};

function execClass(status = "") {
  if (status === "executed" || status === "write_executed") return "ok";
  if (status === "write_dry_run") return "dry";
  return "off"; // not_executed / error
}

// One rule row: rule_key + effect + a fired / not-fired / indeterminate chip.
function RuleRow({ r }: { r: Rule }) {
  const indeterminate = !!(r.missing && r.missing.length);
  const chip = indeterminate ? "indeterminate" : r.fired ? "fired" : "not-fired";
  const chipClass = indeterminate ? "ind" : r.fired ? "fired" : "skip";
  return (
    <div className={`pf-trace-rule ${r.fired ? "is-fired" : ""}`}>
      <code className="pf-trace-rulekey">{r.rule_key}</code>
      {r.decision && <span className="pf-trace-effect">{r.decision}</span>}
      <span className={`pf-trace-chip ${chipClass}`}>{chip}</span>
      {indeterminate && (
        <span className="pf-trace-missing">missing: {r.missing!.join(", ")}</span>
      )}
    </div>
  );
}

function Stage({ n, title, children }: { n: number; title: string; children: any }) {
  return (
    <li className="pf-trace-stage">
      <span className="pf-trace-marker">{n}</span>
      <div className="pf-trace-body">
        <div className="pf-trace-title">{title}</div>
        <div className="pf-trace-detail">{children}</div>
      </div>
    </li>
  );
}

export default function DecisionTrace({ trace }: { trace?: GovernanceTrace | null }) {
  if (!trace) return null;

  const caller = trace.caller || null;
  const params = trace.parameters || {};
  const rules = trace.rules_evaluated || [];
  const masked = (trace.masked_fields || []).length > 0;
  const reasons = trace.reasons || [];
  const approvers = trace.approver_roles || [];
  // An authorization short-circuit (role gate) blocks before any rule runs.
  const authzBlock = trace.decision === "blocked" && rules.length === 0;

  return (
    <div className="pf-trace">
      <div className="pf-trace-head">
        <span className="pf-trace-kicker">Decision trace</span>
        {trace.trace_id && <code className="pf-trace-id">{trace.trace_id}</code>}
      </div>
      <ol className="pf-trace-stages">
        {/* 1 — Identity */}
        <Stage n={1} title="Identity">
          {caller ? (
            <div className="pf-trace-kv">
              {Object.entries(caller).map(([k, v]) => (
                <span key={k} className="pf-trace-kvpair">
                  <span className="k">{k}</span>
                  <span className="v">{String(v)}</span>
                </span>
              ))}
            </div>
          ) : (
            <span className="pf-trace-muted">
              no caller identity — everything blocks
            </span>
          )}
        </Stage>

        {/* 2 — Request */}
        <Stage n={2} title="Request">
          <code className="pf-trace-call">
            {trace.matched_intent}
            (
            {Object.entries(params)
              .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
              .join(", ")}
            )
          </code>
          {trace.template_id && (
            <div className="pf-trace-sub">template {trace.template_id}</div>
          )}
        </Stage>

        {/* 3 — Policy evaluation */}
        <Stage n={3} title="Policy evaluation">
          {rules.length > 0 ? (
            rules.map((r, i) => <RuleRow key={i} r={r} />)
          ) : authzBlock ? (
            <span className="pf-trace-muted">
              blocked at authorization — no business rules evaluated
            </span>
          ) : (
            <span className="pf-trace-muted">no rules applied to this intent</span>
          )}
        </Stage>

        {/* 4 — Decision */}
        <Stage n={4} title="Decision">
          <span
            className={`pf-verdict ${decisionVerdictClass(trace.decision, masked)}`}
          >
            {decisionLabel(trace.decision, masked)}
          </span>
          {reasons.map((r, i) => (
            <div key={i} className="pf-diff-reason">
              <span className="lbl">reason</span>
              {r}
            </div>
          ))}
          {approvers.length > 0 && (
            <div className="pf-diff-reason">
              <span className="lbl">approver</span>
              {approvers.join(", ")}
            </div>
          )}
          {masked && (
            <div className="pf-diff-reason">
              <span className="lbl">masked</span>
              {trace.masked_fields!.join(", ")}
            </div>
          )}
        </Stage>

        {/* 5 — Execution */}
        <Stage n={5} title="Execution">
          <span className={`pf-trace-exec ${execClass(trace.execution_status)}`}>
            {EXEC_LABEL[trace.execution_status || ""] || trace.execution_status || "—"}
          </span>
        </Stage>
      </ol>
    </div>
  );
}
