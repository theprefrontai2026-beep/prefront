const CHECKS: [string, string][] = [
  ["schema_valid", "schema"],
  ["source_grounded", "grounded"],
  ["semantic_valid", "semantic"],
  ["executable", "executable"],
  ["testable", "testable"],
  ["consistency_valid", "consistent"],
];

function Tick({ ok }: { ok: boolean }) {
  return <span className={ok ? "pf-vr-ok" : "pf-vr-bad"}>{ok ? "✓" : "✗"}</span>;
}

interface Props {
  report: any;
}

export default function ValidationReport({ report }: Props) {
  if (!report) return <p className="pf-hint">Run validation to see the report.</p>;
  const s = report.summary || {};
  const rules = report.rule_results || [];

  return (
    <div>
      <div className="pf-summary" style={{ marginBottom: 16 }}>
        <span className="pf-pill">{s.candidate_rules_total ?? 0} rules</span>
        <span className="pf-pill approved">{s.executable_rules ?? 0} executable</span>
        <span className="pf-pill">{s.testable_rules ?? 0} testable</span>
        <span className="pf-pill approved">{s.publishable_rules ?? 0} publishable</span>
        <span className={`pf-pill ${(s.critical_unresolved_items ?? 0) ? "rejected" : ""}`}>
          {s.unresolved_items_total ?? 0} unresolved
        </span>
      </div>

      <table className="pf-vr-table">
        <thead>
          <tr>
            <th>Rule</th>
            {CHECKS.map(([, label]) => <th key={label}>{label}</th>)}
            <th>Publishable</th>
            <th>Blockers</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r: any) => (
            <tr key={r.rule_key}>
              <td><code className="pf-rule-key-sm">{r.rule_key}</code></td>
              {CHECKS.map(([key]) => <td key={key}><Tick ok={r[key]} /></td>)}
              <td><Tick ok={r.publishable} /></td>
              <td style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {(r.publish_blockers || []).map((b: string) => (
                  <span key={b} className="pf-chip warn">{b}</span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
