import { useMemo, useState } from "react";

const SEV_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

interface Props {
  items: any[];
  onResolve: (id: string, status: string) => void;
}

export default function UnresolvedItems({ items, onResolve }: Props) {
  const [sev, setSev] = useState("all");
  const [status, setStatus] = useState("open");

  const filtered = useMemo(() => {
    const rows = (items || []).filter(
      (i) => (sev === "all" || i.severity === sev) && (status === "all" || i.status === status)
    );
    return rows.sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  }, [items, sev, status]);

  if (!items?.length) return <p className="pf-hint">No unresolved items. Run validation to populate.</p>;

  return (
    <div>
      <div className="pf-filters">
        <label>
          Severity
          <select value={sev} onChange={(e) => setSev(e.target.value)}>
            <option value="all">All</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
            <option value="waived">Waived</option>
            <option value="all">All</option>
          </select>
        </label>
      </div>

      <ul className="pf-unresolved-list">
        {filtered.map((row) => {
          const it = row.item || {};
          return (
            <li key={row.unresolved_id} className={`pf-u-item sev-${row.severity}`}>
              <div className="pf-u-head">
                <span className={`pf-badge sev-${row.severity}`}>{row.severity}</span>
                <span className="pf-badge type">{row.unresolved_type}</span>
                {it.rule_key && <code className="pf-rule-key-sm">{it.rule_key}</code>}
                <span className={`pf-badge review-${row.status}`}>{row.status}</span>
              </div>
              <p className="pf-u-issue">{it.issue}</p>
              {it.recommended_action && (
                <p className="pf-u-action">→ {it.recommended_action}</p>
              )}
              {row.status === "open" && (
                <div className="pf-actions" style={{ marginTop: 8 }}>
                  <button className="pf-btn approve sm" onClick={() => onResolve(row.unresolved_id, "resolved")}>
                    Resolve
                  </button>
                  <button className="pf-btn sm" onClick={() => onResolve(row.unresolved_id, "waived")}>
                    Waive
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
