interface Props {
  entries: any[];
}

export default function ClauseLedger({ entries }: Props) {
  if (!entries?.length) return <p className="pf-hint">No ledger yet. Classify clauses to populate.</p>;
  return (
    <table className="pf-ledger-table">
      <thead>
        <tr>
          <th>Clause</th>
          <th>Section</th>
          <th>Disposition</th>
          <th>Atoms</th>
          <th>Rules</th>
          <th>Unresolved</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e: any) => (
          <tr key={e.clause_id}>
            <td><code className="pf-rule-key-sm">{e.clause_id}</code></td>
            <td style={{ color: "var(--ink-soft)", fontSize: 13 }}>{e.section}</td>
            <td><span className={`pf-badge disp disp-${e.disposition || ""}`}>{e.disposition || "—"}</span></td>
            <td style={{ color: "var(--muted)", fontSize: 13 }}>{(e.generated_atoms || []).length || "—"}</td>
            <td>
              {(e.generated_rules || []).map((r: string) => (
                <code key={r} className="pf-rule-key-sm">{r}</code>
              ))}
              {!(e.generated_rules || []).length && <span style={{ color: "var(--muted)" }}>—</span>}
            </td>
            <td style={{ color: (e.unresolved_items || []).length ? "var(--terracotta)" : "var(--muted)", fontSize: 13 }}>
              {(e.unresolved_items || []).length || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
